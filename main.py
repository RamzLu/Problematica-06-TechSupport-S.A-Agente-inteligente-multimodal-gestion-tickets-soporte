import secrets
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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
    autenticar_credenciales,
    usuario_publico,
)
from agent.tools.tools import (
    ARCHIVOS_DIR,
    ARCHIVOS_PROTEGIDOS,
    EXTENSIONES_IMAGEN,
    EXTENSIONES_PERMITIDAS,
    MAX_TAMANO_ARCHIVO,
    _PATRON_ARCHIVO_SEGURO,
    actualizar_estado,
    agregar_comentario,
    asignar_responsable,
    buscar_tickets,
    cambiar_prioridad,
    consultar_ticket,
    fijar_usuario_sesion,
    generar_resumen,
    limpiar_usuario_sesion,
    listar_archivos,
    sanitizar_identificador,
    sanitizar_texto,
)

app = FastAPI(title="TechSupport S.A. - Agente Multimodal de Tickets")

FRONTEND_FILE = Path(__file__).resolve().parent / "frontend" / "index.html"
ARCHIVOS_DIR.mkdir(exist_ok=True)

runner = InMemoryRunner(agent=root_agent, app_name="techsupport_app")
_sesiones_por_usuario: dict[str, str] = {}
_tokens_activos: dict[str, str] = {}


@app.middleware("http")
async def agregar_headers_seguridad(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    return response


async def _obtener_sesion(usuario_id: str) -> str:
    session_id = _sesiones_por_usuario.get(usuario_id)
    if not session_id:
        s = await runner.session_service.create_session(
            app_name="techsupport_app", user_id=usuario_id
        )
        session_id = s.id
        _sesiones_por_usuario[usuario_id] = session_id
    return session_id


def _extraer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    partes = authorization.strip().split(" ", 1)
    if len(partes) == 2 and partes[0].lower() == "bearer":
        return partes[1].strip()
    return None


def _resolver_usuario_request(
    authorization: Optional[str] = Header(None),
    usuario_id_param: Optional[str] = None,
) -> str:
    token = _extraer_token(authorization)
    if token:
        uid_token = _tokens_activos.get(token)
        if not uid_token or uid_token not in USUARIOS:
            raise HTTPException(status_code=401, detail="Sesion invalida o expirada. Inicie sesion nuevamente.")
        if usuario_id_param and usuario_id_param.strip().lower() != uid_token.lower():
            raise HTTPException(
                status_code=403,
                detail="No tiene permiso para operar en nombre de otro usuario.",
            )
        return uid_token

    if not usuario_id_param:
        raise HTTPException(
            status_code=401,
            detail="Autenticacion requerida: debe iniciar sesion con sus credenciales corporativas.",
        )
    uid_limpio, err = sanitizar_identificador(usuario_id_param, campo="usuario_id")
    if err:
        raise HTTPException(status_code=400, detail=err["error"])
    if uid_limpio not in USUARIOS:
        raise HTTPException(status_code=401, detail=f"Usuario '{uid_limpio}' invalido.")
    return uid_limpio


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
    return FileResponse(FRONTEND_FILE)


@app.get("/usuarios")
def listar_usuarios():
    return {
        "usuarios": [usuario_publico(uid) for uid in USUARIOS],
        "organizaciones": list(ORGANIZACIONES.values()),
    }


@app.get("/catalogos")
def obtener_catalogos():
    soportes = [
        usuario_publico(uid) for uid, u in USUARIOS.items() if u.get("rol") == "soporte"
    ]
    return {
        "categorias": CATEGORIAS,
        "prioridades": PRIORIDADES,
        "estados": ESTADOS,
        "transiciones_permitidas": TRANSICIONES_PERMITIDAS,
        "organizaciones": list(ORGANIZACIONES.values()),
        "equipo_soporte": soportes,
        "archivos_disponibles": listar_archivos().get("archivos", []),
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

    info = autenticar_credenciales(ident_limpio, password)
    if not info:
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas. Verifique su ID/correo corporativo y contrasena asignada por la empresa.",
        )

    token = secrets.token_urlsafe(32)
    _tokens_activos[token] = info["id"]
    return {"ok": True, "token": token, "usuario": info}


@app.post("/auth/logout")
def cerrar_sesion(authorization: Optional[str] = Header(None)):
    token = _extraer_token(authorization)
    if token:
        _tokens_activos.pop(token, None)
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
    return res


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
    return res


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
        codigo = 403 if "permiso" in res.get("error", "").lower() else 400
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return res


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
        codigo = 403 if "permiso" in res.get("error", "").lower() else 400
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return res


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
        codigo = 403 if "permiso" in res.get("error", "").lower() else 400
        raise HTTPException(status_code=codigo, detail=res.get("error"))
    return res


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


@app.post("/reset-sesion")
async def reset_sesion(
    usuario_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)
    _sesiones_por_usuario.pop(uid, None)
    nueva = await _obtener_sesion(uid)
    return {"ok": True, "session_id": nueva}


@app.post("/chat")
async def chat_con_agente(
    usuario_id: Optional[str] = Form(None),
    mensaje: str = Form(""),
    archivo: Optional[UploadFile] = File(None),
    archivo_servidor: str = Form(""),
    authorization: Optional[str] = Header(None),
):
    uid = _resolver_usuario_request(authorization, usuario_id)

    mensaje_limpio, err = sanitizar_texto(mensaje, campo="mensaje", max_largo=2000)
    if err:
        return {"ok": False, "usuario_id": uid, "tools_usadas": [], "respuesta": err["error"]}

    info_usuario = usuario_publico(uid)
    partes: list[types.Part] = []
    nota_archivo = ""

    if archivo and archivo.filename:
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

        destino = ARCHIVOS_DIR / nombre_limpio
        if nombre_limpio in ARCHIVOS_PROTEGIDOS and destino.exists():
            if destino.read_bytes() != contenido_bytes:
                nombre_limpio = f"{uid}_{nombre_limpio}"
                destino = ARCHIVOS_DIR / nombre_limpio
        with open(destino, "wb") as f:
            f.write(contenido_bytes)

        if ext in EXTENSIONES_IMAGEN:
            mime = f"image/{ext.lstrip('.')}"
            if mime == "image/jpg":
                mime = "image/jpeg"
            partes.append(
                types.Part(inline_data=types.Blob(data=contenido_bytes, mime_type=mime))
            )
            nota_archivo = f" [Imagen adjunta en el chat: '{nombre_limpio}']"
        else:
            nota_archivo = (
                f" [Archivo subido y guardado en 'archivos/{nombre_limpio}': "
                f"analizalo con leer_archivo('{nombre_limpio}')]"
            )
    elif archivo_servidor and archivo_servidor.strip():
        nom_srv, err = sanitizar_texto(archivo_servidor, campo="archivo_servidor", max_largo=120)
        if err:
            return {"ok": False, "usuario_id": uid, "tools_usadas": [], "respuesta": err["error"]}
        nombre_limpio = Path(nom_srv).name
        ruta_srv = (ARCHIVOS_DIR / nombre_limpio).resolve()
        if (
            not _PATRON_ARCHIVO_SEGURO.match(nombre_limpio)
            or ruta_srv.parent != ARCHIVOS_DIR.resolve()
            or not ruta_srv.is_file()
        ):
            return {
                "ok": False,
                "usuario_id": uid,
                "tools_usadas": [],
                "respuesta": f"El archivo de prueba '{nombre_limpio}' no existe en 'archivos/'.",
            }
        ext = ruta_srv.suffix.lower()
        if ext in EXTENSIONES_IMAGEN:
            contenido_bytes = ruta_srv.read_bytes()
            mime = f"image/{ext.lstrip('.')}"
            if mime == "image/jpg":
                mime = "image/jpeg"
            partes.append(
                types.Part(inline_data=types.Blob(data=contenido_bytes, mime_type=mime))
            )
            nota_archivo = f" [Imagen adjunta en el chat: '{nombre_limpio}']"
        else:
            nota_archivo = (
                f" [Archivo adjunto desde 'archivos/{nombre_limpio}': "
                f"analizalo con leer_archivo('{nombre_limpio}')]"
            )

    texto_final = (
        f"[Contexto de sesion verificada -> usuario_id: {uid} | "
        f"nombre: {info_usuario['nombre']} | rol: {info_usuario['rol']} | "
        f"area: {info_usuario['area']} | organizacion: {info_usuario['organizacion']}]{nota_archivo}\n"
        f"{mensaje_limpio or 'Analiza el archivo adjunto.'}"
    )
    partes.append(types.Part(text=texto_final))

    session_id = await _obtener_sesion(uid)
    msg = types.Content(role="user", parts=partes)

    respuestas: list[str] = []
    tools_usadas: list[dict] = []

    token_ctx = fijar_usuario_sesion(uid)
    try:
        async for ev in runner.run_async(
            user_id=uid, session_id=session_id, new_message=msg
        ):
            if ev.content and ev.content.parts:
                for p in ev.content.parts:
                    if p.function_call:
                        tools_usadas.append(
                            {
                                "tool": p.function_call.name,
                                "args": dict(p.function_call.args or {}),
                            }
                        )
                    if p.text:
                        respuestas.append(p.text)
    finally:
        limpiar_usuario_sesion(token_ctx)

    return {
        "ok": True,
        "usuario_id": uid,
        "tools_usadas": tools_usadas,
        "respuesta": "\n".join(r.strip() for r in respuestas if r.strip()),
    }
