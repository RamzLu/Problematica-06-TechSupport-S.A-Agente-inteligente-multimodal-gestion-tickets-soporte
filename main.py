import asyncio
import contextvars
import logging
import os
import re
import secrets
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent.agent import root_agent
from agent.data import (
    CATEGORIAS,
    ESTADOS,
    ORGANIZACIONES,
    PRIORIDADES,
    TRANSICIONES_PERMITIDAS,
    USUARIOS,
    usuario_publico,
)
from agent.conversaciones import TITULO_POR_DEFECTO, AlmacenConversaciones
from agent.limitador import limitador
from agent.repo import RepoError, get_repo
from agent.tools.tools import (
    EXTENSIONES_IMAGEN,
    EXTENSIONES_PERMITIDAS,
    EXTENSIONES_TEXTO,
    MAX_TAMANO_ARCHIVO,
    MAX_TAMANO_TEXTO_BYTES,
    _PATRON_ARCHIVO_SEGURO,
    _leer_archivo_bytes,
    redactar_datos_sensibles,
    actualizar_estado,
    agregar_comentario,
    asignar_responsable,
    buscar_tickets,
    cambiar_prioridad,
    consultar_ticket,
    detectar_prompt_injection,
    enriquecer_ticket_ui,
    generar_resumen,
    limpiar_usuario_sesion,
    listar_archivos,
    preprocesar_contexto_seguro,
    sanitizar_identificador,
    sanitizar_texto,
)

app = FastAPI(title="TechSupport S.A. - Agente Multimodal de Tickets")

_log = logging.getLogger("uvicorn.error")

get_repo()


@app.exception_handler(RepoError)
async def _error_de_base_de_datos(request: Request, exc: RepoError):
    return JSONResponse(
        status_code=503,
        content={"detail": "El servicio de datos no esta disponible en este momento. Intente nuevamente."},
    )


FRONTEND_FILE = Path(__file__).resolve().parent / "frontend" / "index.html"

APP_NAME = "techsupport_app"
MAX_TURNOS_SESION = int(os.getenv("TS_MAX_TURNOS", "10"))
MAX_CHAT_POR_MINUTO = int(os.getenv("TS_MAX_CHAT_MIN", "20"))
TIMEOUT_CHAT_SEG = float(os.getenv("TS_CHAT_TIMEOUT", "240"))
MAX_LLAMADAS_HERRAMIENTAS = int(os.getenv("TS_MAX_HERRAMIENTAS", "8"))
MAX_LLAMADAS_IGUALES = 3
MAX_LADO_IMAGEN_MODELO = int(os.getenv("TS_IMG_MAX_LADO", "768"))
RUTA_SESIONES_DB = Path(__file__).resolve().parent / "agent" / ".adk" / "sesiones_chat.db"
RUTA_CONVERSACIONES_DB = Path(__file__).resolve().parent / "agent" / ".adk" / "conversaciones.db"

_ip_cliente: contextvars.ContextVar[str] = contextvars.ContextVar("ip_cliente", default="")


def _modo_pedido() -> str:
    """'memoria' si se pidio expresamente (TS_SESIONES o TS_BACKEND=memoria, como en los tests); si no, 'sqlite'."""
    modo = os.getenv("TS_SESIONES", "").strip().lower()
    if not modo:
        modo = "memoria" if os.getenv("TS_BACKEND", "").strip().lower() == "memoria" else "sqlite"
    return modo


def _crear_runner():
    """Crea el runner del agente.

    Las conversaciones se guardan en SQLite (sobreviven a un reinicio). Si no se puede abrir
    esa base, o se pide `TS_SESIONES=memoria`, se usan sesiones en memoria. Los tests usan memoria.
    """
    modo = _modo_pedido()
    if modo == "sqlite":
        try:
            from google.adk.runners import Runner
            from google.adk.sessions import DatabaseSessionService

            RUTA_SESIONES_DB.parent.mkdir(parents=True, exist_ok=True)
            servicio = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{RUTA_SESIONES_DB.as_posix()}")
            return Runner(agent=root_agent, app_name=APP_NAME, session_service=servicio), "sqlite"
        except Exception:
            _log.warning("No se pudo abrir la base de sesiones; se usan sesiones en memoria.", exc_info=True)
    return InMemoryRunner(agent=root_agent, app_name=APP_NAME), "memoria"


runner, _modo_sesiones = _crear_runner()


def _crear_almacen_conversaciones() -> AlmacenConversaciones:
    """Historial visible del chat: en disco (sobrevive a reinicios) salvo que se pidan sesiones en memoria."""
    # Va a disco aunque el contexto del modelo haya caido a memoria (por ejemplo, si falta sqlalchemy).
    if _modo_pedido() == "sqlite":
        try:
            return AlmacenConversaciones(RUTA_CONVERSACIONES_DB)
        except Exception:
            _log.warning("No se pudo abrir la base de conversaciones; se guarda en memoria.", exc_info=True)
    return AlmacenConversaciones(None)


conversaciones = _crear_almacen_conversaciones()


def _pasar_a_memoria() -> None:
    global runner, _modo_sesiones
    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    _modo_sesiones = "memoria"


def _es_error_de_almacen(exc: Exception) -> bool:
    """Error de la base de sesiones (no del modelo ni de la red)."""
    return _modo_sesiones == "sqlite" and type(exc).__module__.split(".")[0] in {
        "sqlalchemy",
        "aiosqlite",
        "sqlite3",
    }


_PATRON_ETIQUETA_INLINE = re.compile(r"<(script|style)(?=[\s>])")


def renderizar_frontend(html: str, nonce: str) -> str:
    """Marca los bloques <script> y <style> con el nonce de esta respuesta."""
    return _PATRON_ETIQUETA_INLINE.sub(lambda m: f'<{m.group(1)} nonce="{nonce}"', html)


def _codigo_error_cambio(res: dict) -> int:
    texto = res.get("error", "").lower()
    if "permiso" in texto:
        return 403
    if "otra persona" in texto:
        return 409
    return 400


@app.middleware("http")
async def agregar_headers_seguridad(request: Request, call_next):
    ip = request.client.host if request.client else ""
    _ip_cliente.set(ip)

    if request.method == "POST" and request.url.path in ("/auth/login", "/auth/refresh"):
        espera = limitador.bloqueado(f"ip-auth:{ip}", 30, 60)
        if espera:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Demasiados intentos. Espere {espera} segundos e intente de nuevo."},
                headers={"Retry-After": str(espera)},
            )
        limitador.registrar(f"ip-auth:{ip}")

    response = await call_next(request)
    nonce = response.headers.get("X-CSP-Nonce")
    if nonce:
        del response.headers["X-CSP-Nonce"]
    permitido = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src {permitido}; "
        f"style-src {permitido}; "
        "style-src-attr 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    return response


async def _nueva_sesion(usuario_id: str) -> str:
    s = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=usuario_id, session_id=f"{usuario_id}-{uuid.uuid4().hex[:10]}"
    )
    return s.id


async def _obtener_sesion(usuario_id: str, conv: dict) -> str:
    """Devuelve la sesion del modelo que corresponde a la conversacion, o abre una nueva.

    Cada conversacion retoma su propio contexto (tambien despues de reiniciar el servidor, si las
    sesiones estan en SQLite). Al llegar a MAX_TURNOS_SESION mensajes el modelo sigue con una
    sesion nueva, para que el historial no crezca sin limite con un modelo de contexto chico; la
    conversacion visible no se corta.
    """
    servicio = runner.session_service
    sid = conv.get("session_id") or ""
    if sid:
        actual = await servicio.get_session(app_name=APP_NAME, user_id=usuario_id, session_id=sid)
        if actual is not None:
            turnos = sum(1 for e in actual.events if getattr(e, "author", "") == "user")
            if turnos < MAX_TURNOS_SESION:
                return actual.id
            _log.info("chat %s: contexto con %d mensajes, se abre una sesion nueva", usuario_id, turnos)
    nueva = await _nueva_sesion(usuario_id)
    conversaciones.fijar_session(conv["id"], nueva)
    conv["session_id"] = nueva
    return nueva


def _extraer_token(authorization: Optional[str]) -> Optional[str]:
    if not isinstance(authorization, str) or not authorization:
        return None
    partes = authorization.strip().split(" ", 1)
    if len(partes) == 2 and partes[0].lower() == "bearer":
        return partes[1].strip()
    return None


def _resolver_usuario_request(
    authorization: Optional[str] = Header(None),
    usuario_id_param: Optional[str] = None,
) -> str:
    """Identifica al usuario SOLO a partir del token de Supabase Auth (Bearer).

    `usuario_id_param` ya no identifica a nadie: si llega, debe coincidir con el
    usuario del token; de lo contrario se rechaza.
    """
    if not isinstance(usuario_id_param, str):
        usuario_id_param = None
    token = _extraer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Autenticacion requerida: debe iniciar sesion con sus credenciales corporativas.",
        )
    uid = get_repo().usuario_desde_token(token)
    if not uid or uid not in USUARIOS:
        raise HTTPException(status_code=401, detail="Sesion invalida o expirada. Inicie sesion nuevamente.")
    if usuario_id_param and usuario_id_param.strip().lower() != uid.lower():
        raise HTTPException(
            status_code=403,
            detail="No tiene permiso para operar en nombre de otro usuario.",
        )
    return uid


def _validar_magic_bytes_imagen(ext: str, data: bytes) -> bool:
    if len(data) < 4:
        return False
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


@app.get("/")
def servir_frontend():
    nonce = secrets.token_urlsafe(16)
    html = renderizar_frontend(FRONTEND_FILE.read_text(encoding="utf-8"), nonce)
    return HTMLResponse(html, headers={"X-CSP-Nonce": nonce, "Cache-Control": "no-store"})


@app.get("/cargando.gif", include_in_schema=False)
def servir_gif_cargando():
    return FileResponse(
        FRONTEND_FILE.parent / "cargando.gif",
        media_type="image/gif",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/usuarios")
def listar_usuarios(authorization: Optional[str] = Header(None)):
    uid = _resolver_usuario_request(authorization, None)
    if USUARIOS[uid].get("rol") != "soporte":
        raise HTTPException(status_code=403, detail="Solo el personal de soporte puede ver el directorio.")
    return {
        "usuarios": [usuario_publico(uid) for uid in USUARIOS],
        "organizaciones": list(ORGANIZACIONES.values()),
    }


@app.get("/catalogos")
def obtener_catalogos(authorization: Optional[str] = Header(None)):
    uid_cat = _resolver_usuario_request(authorization, None)
    soportes = [
        {k: v for k, v in usuario_publico(uid).items() if k != "email"}
        for uid, u in USUARIOS.items()
        if u.get("rol") == "soporte" and u.get("activo", True)
    ]
    return {
        "categorias": CATEGORIAS,
        "prioridades": PRIORIDADES,
        "estados": ESTADOS,
        "transiciones_permitidas": TRANSICIONES_PERMITIDAS,
        "organizaciones": list(ORGANIZACIONES.values()),
        "equipo_soporte": soportes,
        "archivos_disponibles": listar_archivos(usuario_id=uid_cat).get("archivos", []),
    }


def _respuesta_sesion(sesion: dict) -> dict:
    return {
        "ok": True,
        "token": sesion["access_token"],
        "refresh_token": sesion["refresh_token"],
        "expires_in": sesion["expires_in"],
        "usuario": usuario_publico(sesion["codigo"]),
    }


@app.post("/auth/login")
def iniciar_sesion(
    identificador: str = Form(...),
    password: str = Form(...),
):
    ident_limpio, err = sanitizar_texto(identificador, campo="identificador", max_largo=120)
    if err or not ident_limpio:
        raise HTTPException(status_code=400, detail=(err or {}).get("error", "Identificador invalido."))
    if not password or len(password) > 128:
        raise HTTPException(status_code=400, detail="Credencial invalida.")

    ident_clave = ident_limpio.lower()
    clave_par = f"login:{ident_clave}|{_ip_cliente.get()}"
    clave_ident = f"login-ident:{ident_clave}"
    espera = max(
        limitador.bloqueado(clave_par, 5, 300),
        limitador.bloqueado(clave_ident, 20, 900),
    )
    if espera:
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos fallidos. Intente nuevamente en {espera} segundos.",
            headers={"Retry-After": str(espera)},
        )

    sesion = get_repo().autenticar(ident_limpio, password)
    if not sesion:
        limitador.registrar(clave_par)
        limitador.registrar(clave_ident)
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas. Verifique su ID/correo corporativo y contraseña asignada por la empresa.",
        )
    limitador.olvidar(clave_par)
    return _respuesta_sesion(sesion)


@app.post("/auth/refresh")
def renovar_sesion(refresh_token: str = Form(...)):
    if not refresh_token or len(refresh_token) > 512:
        raise HTTPException(status_code=400, detail="Token de renovacion invalido.")
    sesion = get_repo().renovar_sesion(refresh_token)
    if not sesion:
        raise HTTPException(status_code=401, detail="Sesion expirada. Inicie sesion nuevamente.")
    return _respuesta_sesion(sesion)


@app.post("/auth/logout")
def cerrar_sesion(authorization: Optional[str] = Header(None)):
    token = _extraer_token(authorization)
    if token:
        get_repo().cerrar_sesion(token)
    return {"ok": True}


@app.get("/auth/me")
def sesion_actual(authorization: Optional[str] = Header(None)):
    uid = _resolver_usuario_request(authorization, None)
    return {"ok": True, "usuario": usuario_publico(uid)}


@app.get("/tickets")
def listar_tickets_visibles(
    usuario_id: Optional[str] = None,
    estado: str = "",
    categoria: str = "",
    prioridad: str = "",
    area: str = "",
    solicitante: str = "",
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = buscar_tickets(
        usuario_id=uid,
        solicitante=solicitante,
        estado=estado,
        categoria=categoria,
        prioridad=prioridad,
        area=area,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Error al buscar tickets."))
    completos = {t["id"]: t for t in get_repo().tickets_todos()}
    tickets_ui = [
        enriquecer_ticket_ui(completos[t["id"]])
        for t in res.get("tickets", [])
        if t["id"] in completos
    ]
    return {**res, "tickets": tickets_ui}


@app.get("/tickets/{ticket_id}")
def detalle_ticket(
    ticket_id: int,
    usuario_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = consultar_ticket(ticket_id=ticket_id, usuario_id=uid)
    if not res.get("ok"):
        codigo = 403 if "autorizacion" in res.get("error", "").lower() else 404
        raise HTTPException(status_code=codigo, detail=res.get("error", "No se pudo consultar el ticket."))
    return {"ok": True, "ticket": enriquecer_ticket_ui(get_repo().ticket(ticket_id))}


@app.get("/resumen")
def resumen_incidencias(
    usuario_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = generar_resumen(usuario_id=uid)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "No se pudo generar el resumen."))
    return res


@app.post("/tickets/{ticket_id}/estado")
def endpoint_cambiar_estado(
    ticket_id: int,
    nuevo_estado: str = Form(...),
    usuario_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = actualizar_estado(ticket_id=ticket_id, usuario_id=uid, nuevo_estado=nuevo_estado)
    if not res.get("ok"):
        codigo = _codigo_error_cambio(res)
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return {"ok": True, "ticket": enriquecer_ticket_ui(get_repo().ticket(ticket_id))}


@app.post("/tickets/{ticket_id}/prioridad")
def endpoint_cambiar_prioridad(
    ticket_id: int,
    nueva_prioridad: str = Form(...),
    usuario_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = cambiar_prioridad(ticket_id=ticket_id, usuario_id=uid, nueva_prioridad=nueva_prioridad)
    if not res.get("ok"):
        codigo = _codigo_error_cambio(res)
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return {"ok": True, "ticket": enriquecer_ticket_ui(get_repo().ticket(ticket_id))}


@app.post("/tickets/{ticket_id}/asignar")
def endpoint_asignar_responsable(
    ticket_id: int,
    responsable_id: str = Form(...),
    usuario_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = asignar_responsable(ticket_id=ticket_id, usuario_id=uid, responsable_id=responsable_id)
    if not res.get("ok"):
        codigo = _codigo_error_cambio(res)
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return {"ok": True, "ticket": enriquecer_ticket_ui(get_repo().ticket(ticket_id))}


@app.post("/tickets/{ticket_id}/comentario")
def endpoint_agregar_comentario(
    ticket_id: int,
    comentario: str = Form(...),
    usuario_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    res = agregar_comentario(ticket_id=ticket_id, usuario_id=uid, comentario=comentario)
    if not res.get("ok"):
        codigo = 403 if "autorizacion" in res.get("error", "").lower() else 400
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return res


@app.get("/conversaciones")
def listar_conversaciones(authorization: Optional[str] = Header(None)):
    uid = _resolver_usuario_request(authorization, None)
    return {"ok": True, "conversaciones": conversaciones.listar(uid)}


@app.get("/conversaciones/{conversacion_id}")
def ver_conversacion(conversacion_id: str, authorization: Optional[str] = Header(None)):
    uid = _resolver_usuario_request(authorization, None)
    conv = conversaciones.obtener(uid, conversacion_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="La conversacion no existe.")
    return {
        "ok": True,
        "conversacion": {"id": conv["id"], "titulo": conv["titulo"]},
        "mensajes": conversaciones.mensajes(conv["id"]),
    }


_SALUDOS = {"hola","holi", "holaa", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches", "hey", "hello", "ola"}
_AGRADECIMIENTOS = {"gracias", "muchas gracias", "mil gracias", "ok gracias", "listo gracias", "genial gracias"}


def _respuesta_rapida(mensaje: str) -> str:
    """Respuesta fija para un saludo o un agradecimiento solo; no hace falta gastar una vuelta del modelo."""
    base = unicodedata.normalize("NFKD", mensaje.lower())
    limpio = re.sub(r"[^a-z0-9 ]", " ", "".join(c for c in base if not unicodedata.combining(c)))
    limpio = " ".join(limpio.split())
    if limpio in _SALUDOS:
        return "¡Hola! ¿En qué puedo ayudarte? Podés contarme un problema técnico, adjuntar una captura o un log, o consultar el estado de tus tickets."
    if limpio in _AGRADECIMIENTOS:
        return "¡De nada! Si necesitás algo más, avisame."
    return ""


class _BucleDeHerramientas(Exception):
    """El modelo repite la misma herramienta (o encadena demasiadas) sin llegar a responder."""


def _imagen_para_modelo(datos: bytes, mime: str) -> tuple[bytes, str]:
    """Reduce la imagen que se le manda al modelo (el archivo guardado queda intacto).

    Con un modelo de vision en CPU, cada pixel extra son mas "tokens" de imagen y minutos de espera.
    Si Pillow no esta instalado o la imagen ya es chica, se envia tal cual.
    """
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(datos))
        if max(img.size) <= MAX_LADO_IMAGEN_MODELO:
            return datos, mime
        img.thumbnail((MAX_LADO_IMAGEN_MODELO, MAX_LADO_IMAGEN_MODELO))
        salida = io.BytesIO()
        if mime == "image/jpeg":
            img.convert("RGB").save(salida, "JPEG", quality=85)
            return salida.getvalue(), "image/jpeg"
        img.save(salida, "PNG")
        return salida.getvalue(), "image/png"
    except Exception:
        return datos, mime


def _guardar_turno(conv: dict, mensaje, archivo, archivo_servidor, res: dict, duracion_ms: int) -> None:
    """Guarda en el historial lo que el usuario ve: su mensaje y la respuesta (o el aviso).

    El texto del usuario se guarda enmascarado (Zero-PII), igual que lo recibe el modelo.
    """
    texto = redactar_datos_sensibles(mensaje.strip()) if isinstance(mensaje, str) else ""
    adjunto = ""
    if archivo is not None and getattr(archivo, "filename", None):
        adjunto = f"\n[Archivo adjunto: {archivo.filename}]"
    elif isinstance(archivo_servidor, str) and archivo_servidor.strip():
        adjunto = f"\n[Archivo adjunto: {archivo_servidor.strip()}]"
    if texto or adjunto:
        conversaciones.agregar_mensaje(conv["id"], "user", (texto or "(Analizar archivo adjunto)") + adjunto)
        if conv.get("titulo") == TITULO_POR_DEFECTO:
            conversaciones.fijar_titulo(conv["id"], texto or adjunto.strip())
    respuesta = str(res.get("respuesta") or "(sin respuesta de texto)")
    if res.get("ok") is False:
        conversaciones.agregar_mensaje(conv["id"], "sys", respuesta)
    else:
        conversaciones.agregar_mensaje(conv["id"], "agent", respuesta, duracion_ms)


@app.delete("/conversaciones/{conversacion_id}")
async def eliminar_conversacion(conversacion_id: str, authorization: Optional[str] = Header(None)):
    uid = _resolver_usuario_request(authorization, None)
    conv = conversaciones.eliminar(uid, conversacion_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="La conversacion no existe.")
    if conv.get("session_id"):
        try:
            await runner.session_service.delete_session(
                app_name=APP_NAME, user_id=uid, session_id=conv["session_id"]
            )
        except Exception:
            _log.info("chat %s: no se pudo borrar la sesion del modelo (se ignora)", uid, exc_info=True)
    return {"ok": True}


@app.post("/chat")
async def chat_con_agente(
    usuario_id: Optional[str] = Form(None),
    mensaje: str = Form(""),
    archivo: Optional[UploadFile] = File(None),
    archivo_servidor: str = Form(""),
    conversacion_id: str = Form(""),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)

    conv = None
    if isinstance(conversacion_id, str) and conversacion_id.strip():
        conv = conversaciones.obtener(uid, conversacion_id.strip())
        if conv is None:
            raise HTTPException(status_code=404, detail="La conversacion no existe.")
    es_nueva = conv is None
    if es_nueva:
        conv = conversaciones.crear(uid)

    t_chat = time.perf_counter()
    try:
        res = await _procesar_chat(uid, conv, mensaje, archivo, archivo_servidor)
    except BaseException:
        if es_nueva:
            conversaciones.eliminar_si_vacia(conv["id"])
        raise
    duracion_ms = int((time.perf_counter() - t_chat) * 1000)
    try:
        _guardar_turno(conv, mensaje, archivo, archivo_servidor, res, duracion_ms)
    except Exception:
        _log.warning("chat %s: no se pudo guardar la conversacion", uid, exc_info=True)
    res["conversacion_id"] = conv["id"]
    return res


async def _procesar_chat(uid: str, conv: dict, mensaje, archivo, archivo_servidor) -> dict:
    espera_chat = limitador.bloqueado(f"chat:{uid}", MAX_CHAT_POR_MINUTO, 60)
    if espera_chat:
        raise HTTPException(
            status_code=429,
            detail=f"Demasiadas consultas seguidas. Espere {espera_chat} segundos.",
            headers={"Retry-After": str(espera_chat)},
        )
    limitador.registrar(f"chat:{uid}")

    mensaje_str = mensaje if isinstance(mensaje, str) else ""
    archivo_srv_str = archivo_servidor if isinstance(archivo_servidor, str) else ""
    tiene_archivo_subido = bool(archivo and getattr(archivo, "filename", None))
    tiene_archivo_srv = bool(archivo_srv_str and archivo_srv_str.strip())

    mensaje_limpio, err = sanitizar_texto(mensaje_str, campo="mensaje", max_largo=2000, texto_libre=True)
    if err:
        return {"ok": False, "usuario_id": uid, "tools_usadas": [], "respuesta": err["error"]}

    if not mensaje_limpio and not tiene_archivo_subido and not tiene_archivo_srv:
        return {
            "ok": False,
            "usuario_id": uid,
            "tools_usadas": [],
            "respuesta": "Falta ingresar un mensaje o adjuntar un archivo (.png, .jpg, .webp, .txt, .log) para procesar la consulta.",
        }

    if not tiene_archivo_subido and not tiene_archivo_srv:
        rapida = _respuesta_rapida(mensaje_limpio)
        if rapida:
            return {"ok": True, "usuario_id": uid, "tools_usadas": [], "respuesta": rapida}

    partes: list[types.Part] = []
    nota_archivo = ""

    if tiene_archivo_subido:
        nombre_arch, err = sanitizar_texto(archivo.filename, campo="archivo", max_largo=120)
        if err:
            return {"ok": False, "usuario_id": uid, "tools_usadas": [], "respuesta": err["error"]}
        nombre_limpio = Path(nombre_arch).name
        if not _PATRON_ARCHIVO_SEGURO.match(nombre_limpio):
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": "Nombre de archivo invalido: solo se permiten letras, numeros, puntos, guion y guion bajo.",
            }
        ext = Path(nombre_limpio).suffix.lower()
        if ext not in EXTENSIONES_PERMITIDAS:
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": f"Formato de archivo '{ext}' no soportado. Formatos permitidos: .png, .jpg, .jpeg, .webp, .txt, .log.",
            }

        contenido_bytes = await archivo.read(MAX_TAMANO_ARCHIVO + 1)
        if len(contenido_bytes) == 0:
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": "El archivo adjunto esta vacio.",
            }
        if len(contenido_bytes) > MAX_TAMANO_ARCHIVO:
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": "El archivo supera el tamano maximo permitido de 5 MB.",
            }

        if ext in EXTENSIONES_IMAGEN and not _validar_magic_bytes_imagen(ext, contenido_bytes):
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": f"El archivo '{nombre_limpio}' no contiene una imagen valida ({ext}).",
            }

        if ext in EXTENSIONES_TEXTO:
            if len(contenido_bytes) > MAX_TAMANO_TEXTO_BYTES:
                return {
                    "ok": False,
                    "usuario_id": uid,
                    "tools_usadas": [],
                    "respuesta": "El archivo de texto/log supera el limite permitido de 100 KB.",
                }
            if b"\x00" in contenido_bytes:
                return {
                    "ok": False,
                    "usuario_id": uid,
                    "tools_usadas": [],
                    "respuesta": f"El archivo '{nombre_limpio}' contiene bytes binarios no validos para un archivo de texto.",
                }
            try:
                texto_arch = contenido_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return {
                    "ok": False,
                    "usuario_id": uid,
                    "tools_usadas": [],
                    "respuesta": f"El archivo '{nombre_limpio}' no tiene codificacion de texto UTF-8 valida.",
                }
            if detectar_prompt_injection(texto_arch, texto_libre=True):
                return {
                    "ok": False,
                    "usuario_id": uid,
                    "tools_usadas": [],
                    "respuesta": f"Archivo '{nombre_limpio}' bloqueado por seguridad: contiene patrones de inyeccion de prompt o instrucciones maliciosas.",
                }

        mime_guardado = (
            "image/jpeg" if ext in (".jpg", ".jpeg") else
            f"image/{ext.lstrip('.')}" if ext in EXTENSIONES_IMAGEN else "text/plain"
        )
        get_repo().archivo_guardar(uid, nombre_limpio, contenido_bytes, mime_guardado)

        if ext in EXTENSIONES_IMAGEN:
            mime = f"image/{ext.lstrip('.')}"
            if mime == "image/jpg":
                mime = "image/jpeg"
            datos_modelo, mime_modelo = _imagen_para_modelo(contenido_bytes, mime)
            partes.append(
                types.Part(inline_data=types.Blob(data=datos_modelo, mime_type=mime_modelo))
            )
            nota_archivo = f"[Imagen adjunta en el chat: '{nombre_limpio}']"
        else:
            nota_archivo = (
                f"[Archivo subido y guardado como '{nombre_limpio}': "
                f"analizalo con leer_archivo('{nombre_limpio}')]"
            )
    elif tiene_archivo_srv:
        nom_srv, err = sanitizar_texto(archivo_srv_str, campo="archivo_servidor", max_largo=120)
        if err:
            return {"ok": False, "usuario_id": uid, "tools_usadas": [], "respuesta": err["error"]}
        nombre_limpio = Path(nom_srv).name
        ext = Path(nombre_limpio).suffix.lower()
        if not _PATRON_ARCHIVO_SEGURO.match(nombre_limpio) or ext not in EXTENSIONES_PERMITIDAS:
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": f"El archivo de prueba '{nombre_limpio}' no es valido.",
            }
        contenido_bytes = _leer_archivo_bytes(uid, nombre_limpio)
        if contenido_bytes is None:
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": f"El archivo de prueba '{nombre_limpio}' no existe en tus archivos.",
            }
        if ext in EXTENSIONES_IMAGEN:
            mime = f"image/{ext.lstrip('.')}"
            if mime == "image/jpg":
                mime = "image/jpeg"
            datos_modelo, mime_modelo = _imagen_para_modelo(contenido_bytes, mime)
            partes.append(
                types.Part(inline_data=types.Blob(data=datos_modelo, mime_type=mime_modelo))
            )
            nota_archivo = f"[Imagen adjunta en el chat: '{nombre_limpio}']"
        else:
            nota_archivo = (
                f"[Archivo adjunto '{nombre_limpio}': "
                f"analizalo con leer_archivo('{nombre_limpio}')]"
            )

    ctx_seguro, err_ctx = preprocesar_contexto_seguro(
        usuario_id_autenticado=uid,
        mensaje=mensaje_limpio,
        nota_archivo=nota_archivo,
    )
    if err_ctx or not ctx_seguro:
        return {
            "ok": False,
            "usuario_id": uid,
            "tools_usadas": [],
            "respuesta": (err_ctx or {}).get("error", "Error en pre-validacion de sesion."),
        }

    token_ctx = ctx_seguro["token_ctx"]
    partes.append(types.Part(text=ctx_seguro["texto_para_agente"]))

    try:
        session_id = await _obtener_sesion(uid, conv)
    except Exception as exc:
        if not _es_error_de_almacen(exc):
            raise
        _log.warning("Fallo la base de sesiones; se continua en memoria.", exc_info=True)
        _pasar_a_memoria()
        session_id = await _obtener_sesion(uid, conv)
    msg = types.Content(role="user", parts=partes)

    respuestas: list[str] = []
    tools_usadas: list[dict] = []
    t_inicio = t_previo = time.perf_counter()

    llamadas_iguales: dict = {}

    async def _correr_agente():
        nonlocal t_previo
        async for ev in runner.run_async(
            user_id=uid, session_id=session_id, new_message=msg
        ):
            ahora = time.perf_counter()
            if ev.content and ev.content.parts:
                for p in ev.content.parts:
                    if p.function_call:
                        _log.info("chat %s: el modelo pidio '%s' (%.1fs de modelo)", uid, p.function_call.name, ahora - t_previo)
                    elif getattr(p, "function_response", None):
                        _log.info("chat %s: herramienta '%s' ejecutada (%.1fs)", uid, p.function_response.name, ahora - t_previo)
                    elif p.text:
                        _log.info("chat %s: el modelo respondio (%.1fs de modelo)", uid, ahora - t_previo)
                    if p.function_call:
                        firma = (p.function_call.name, repr(sorted(dict(p.function_call.args or {}).items())))
                        llamadas_iguales[firma] = llamadas_iguales.get(firma, 0) + 1
                        if (
                            llamadas_iguales[firma] >= MAX_LLAMADAS_IGUALES
                            or len(tools_usadas) >= MAX_LLAMADAS_HERRAMIENTAS
                        ):
                            raise _BucleDeHerramientas(p.function_call.name)
                        tools_usadas.append(
                            {
                                "tool": p.function_call.name,
                                "args": dict(p.function_call.args or {}),
                            }
                        )
                    if p.text:
                        respuestas.append(p.text)
            t_previo = ahora

    try:
        await asyncio.wait_for(_correr_agente(), timeout=TIMEOUT_CHAT_SEG)
        _log.info("chat %s: total %.1fs, %d herramienta(s)", uid, time.perf_counter() - t_inicio, len(tools_usadas))
    except _BucleDeHerramientas as bucle:
        _log.warning("chat %s: el modelo entro en bucle con '%s'; se corto la consulta", uid, bucle)
        return {
            "ok": False,
            "usuario_id": uid,
            "tools_usadas": tools_usadas,
            "respuesta": (
                "El asistente repitio la misma consulta varias veces sin llegar a una respuesta y se detuvo. "
                "Proba reformular el pedido con mas detalle."
            ),
        }
    except asyncio.TimeoutError:
        _log.warning("chat %s: la consulta se cancelo a los %.0fs", uid, TIMEOUT_CHAT_SEG)
        return {
            "ok": False,
            "usuario_id": uid,
            "tools_usadas": tools_usadas,
            "respuesta": (
                f"El motor de IA local tardo mas de {int(TIMEOUT_CHAT_SEG)} segundos y se cancelo la consulta. "
                "Proba de nuevo con un mensaje mas corto o sin imagen."
            ),
        }
    except Exception as exc:
        _log.warning("chat %s: la consulta con el agente fallo", uid, exc_info=True)
        if _es_error_de_almacen(exc):
            _pasar_a_memoria()
        return {
            "ok": False,
            "usuario_id": uid,
            "tools_usadas": tools_usadas,
            "respuesta": "Limitacion del servicio: no se pudo completar la consulta con el motor de IA local en este momento.",
        }
    finally:
        limpiar_usuario_sesion(token_ctx)

    return {
        "ok": True,
        "usuario_id": uid,
        "tools_usadas": tools_usadas,
        "respuesta": "\n".join(r.strip() for r in respuestas if r.strip()),
    }
