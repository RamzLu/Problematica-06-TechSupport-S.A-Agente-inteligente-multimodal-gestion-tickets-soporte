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
    usuario_publico,
)
from agent.repo import RepoError, get_repo
from agent.tools.tools import (
    EXTENSIONES_IMAGEN,
    EXTENSIONES_PERMITIDAS,
    EXTENSIONES_TEXTO,
    MAX_TAMANO_ARCHIVO,
    MAX_TAMANO_TEXTO_BYTES,
    _PATRON_ARCHIVO_SEGURO,
    _leer_archivo_bytes,
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

get_repo()


@app.exception_handler(RepoError)
async def _error_de_base_de_datos(request: Request, exc: RepoError):
    return JSONResponse(
        status_code=503,
        content={"detail": "El servicio de datos no esta disponible en este momento. Intente nuevamente."},
    )


FRONTEND_FILE = Path(__file__).resolve().parent / "frontend" / "index.html"

runner = InMemoryRunner(agent=root_agent, app_name="techsupport_app")
_sesiones_por_usuario: dict[str, str] = {}


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
            app_name="techsupport_app", user_id="sesion_aislada"
        )
        session_id = s.id
        _sesiones_por_usuario[usuario_id] = session_id
    return session_id


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
    return FileResponse(FRONTEND_FILE)


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

    sesion = get_repo().autenticar(ident_limpio, password)
    if not sesion:
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas. Verifique su ID/correo corporativo y contraseña asignada por la empresa.",
        )
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
        codigo = 403 if "permiso" in res.get("error", "").lower() else 400
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
        codigo = 403 if "permiso" in res.get("error", "").lower() else 400
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
        codigo = 403 if "permiso" in res.get("error", "").lower() else 400
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

    mensaje_str = mensaje if isinstance(mensaje, str) else ""
    archivo_srv_str = archivo_servidor if isinstance(archivo_servidor, str) else ""
    tiene_archivo_subido = bool(archivo and getattr(archivo, "filename", None))
    tiene_archivo_srv = bool(archivo_srv_str and archivo_srv_str.strip())

    mensaje_limpio, err = sanitizar_texto(mensaje_str, campo="mensaje", max_largo=2000)
    if err:
        return {"ok": False, "usuario_id": uid, "tools_usadas": [], "respuesta": err["error"]}

    if not mensaje_limpio and not tiene_archivo_subido and not tiene_archivo_srv:
        return {
            "ok": False,
            "usuario_id": uid,
            "tools_usadas": [],
            "respuesta": "Falta ingresar un mensaje o adjuntar un archivo (.png, .jpg, .webp, .txt, .log) para procesar la consulta.",
        }

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
            if detectar_prompt_injection(texto_arch):
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
            partes.append(
                types.Part(inline_data=types.Blob(data=contenido_bytes, mime_type=mime))
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
            partes.append(
                types.Part(inline_data=types.Blob(data=contenido_bytes, mime_type=mime))
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

    session_id = await _obtener_sesion(uid)
    msg = types.Content(role="user", parts=partes)

    respuestas: list[str] = []
    tools_usadas: list[dict] = []

    try:
        async for ev in runner.run_async(
            user_id="sesion_aislada", session_id=session_id, new_message=msg
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
    except Exception:
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
