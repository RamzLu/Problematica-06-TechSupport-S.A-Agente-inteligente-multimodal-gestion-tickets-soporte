from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent.agent import root_agent
from agent.data import USUARIOS
from agent.tools.tools import (
    ARCHIVOS_DIR,
    buscar_tickets,
    consultar_ticket,
    sanitizar_identificador,
    sanitizar_texto,
)

app = FastAPI(title="TechSupport S.A. - Agente Multimodal de Tickets")

FRONTEND_FILE = Path(__file__).resolve().parent / "frontend" / "index.html"
ARCHIVOS_DIR.mkdir(exist_ok=True)

runner = InMemoryRunner(agent=root_agent, app_name="techsupport_app")
_sesiones_por_usuario: dict[str, str] = {}


async def _obtener_sesion(usuario_id: str) -> str:
    session_id = _sesiones_por_usuario.get(usuario_id)
    if not session_id:
        s = await runner.session_service.create_session(
            app_name="techsupport_app", user_id=usuario_id
        )
        session_id = s.id
        _sesiones_por_usuario[usuario_id] = session_id
    return session_id


@app.get("/")
def servir_frontend():
    return FileResponse(FRONTEND_FILE)


@app.get("/usuarios")
def listar_usuarios():
    return {"usuarios": [{"id": uid, **datos} for uid, datos in USUARIOS.items()]}


@app.post("/usuarios")
def crear_usuario(
    nombre: str = Form(...),
    rol: str = Form("usuario"),
    email: str = Form(""),
    usuario_id: str = Form(""),
):
    nombre_limpio, err = sanitizar_texto(nombre, campo="nombre", max_largo=80)
    if err:
        raise HTTPException(status_code=400, detail=err["error"])
    if not nombre_limpio:
        raise HTTPException(status_code=400, detail="El nombre es obligatorio.")
    rol_limpio, err = sanitizar_texto(rol, campo="rol", max_largo=20)
    if err:
        raise HTTPException(status_code=400, detail=err["error"])
    rol_limpio = rol_limpio.lower()
    if rol_limpio not in {"usuario", "soporte"}:
        raise HTTPException(status_code=400, detail="El rol debe ser 'usuario' o 'soporte'.")

    prefijo = "s" if rol_limpio == "soporte" else "u"
    uid = usuario_id.strip().lower()
    if uid:
        uid_ok, err = sanitizar_identificador(uid, campo="usuario_id")
        if err:
            raise HTTPException(status_code=400, detail=err["error"])
        uid = uid_ok
    else:
        existentes = [
            int(k[1:])
            for k in USUARIOS
            if k.startswith(prefijo) and k[1:].isdigit()
        ]
        siguiente = (max(existentes) + 1) if existentes else 1
        uid = f"{prefijo}{siguiente:03d}"

    if uid in USUARIOS:
        raise HTTPException(status_code=400, detail=f"El ID '{uid}' ya existe.")

    email_limpio, err = sanitizar_texto(email, campo="email", max_largo=120)
    if err:
        raise HTTPException(status_code=400, detail=err["error"])
    correo = email_limpio or f"{uid}@techsupport.test"
    USUARIOS[uid] = {"nombre": nombre_limpio, "rol": rol_limpio, "email": correo}
    return {"ok": True, "usuario": {"id": uid, **USUARIOS[uid]}}


@app.get("/tickets")
def listar_tickets_visibles(usuario_id: str = "s001"):
    return buscar_tickets(usuario_id=usuario_id)


@app.get("/tickets/{ticket_id}")
def detalle_ticket(ticket_id: int, usuario_id: str = "s001"):
    return consultar_ticket(ticket_id=ticket_id, usuario_id=usuario_id)


@app.post("/reset-sesion")
async def reset_sesion(usuario_id: str = Form(...)):
    _sesiones_por_usuario.pop(usuario_id, None)
    nueva = await _obtener_sesion(usuario_id)
    return {"ok": True, "session_id": nueva}


@app.post("/chat")
async def chat_con_agente(
    usuario_id: str = Form(...),
    mensaje: str = Form(""),
    archivo: Optional[UploadFile] = File(None),
):
    uid_limpio, err = sanitizar_identificador(usuario_id, campo="usuario_id")
    if err:
        raise HTTPException(status_code=400, detail=err["error"])
    usuario_id = uid_limpio
    if usuario_id not in USUARIOS:
        raise HTTPException(status_code=400, detail=f"Usuario '{usuario_id}' invalido.")

    mensaje_limpio, err = sanitizar_texto(mensaje, campo="mensaje", max_largo=2000)
    if err:
        return {"ok": False, "usuario_id": usuario_id, "tools_usadas": [], "respuesta": err["error"]}

    info_usuario = USUARIOS[usuario_id]
    partes: list[types.Part] = []
    nota_archivo = ""

    if archivo and archivo.filename:
        nombre_arch, err = sanitizar_texto(archivo.filename, campo="archivo", max_largo=120)
        if err:
            return {"ok": False, "usuario_id": usuario_id, "tools_usadas": [], "respuesta": err["error"]}
        nombre_limpio = Path(nombre_arch).name
        ext = Path(nombre_limpio).suffix.lower()
        destino = ARCHIVOS_DIR / nombre_limpio
        contenido_bytes = await archivo.read()
        with open(destino, "wb") as f:
            f.write(contenido_bytes)

        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            mime = archivo.content_type or f"image/{ext.lstrip('.')}"
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

    texto_final = (
        f"[Usuario activo: {usuario_id} - {info_usuario['nombre']} "
        f"(rol: {info_usuario['rol']})]{nota_archivo}\n"
        f"{mensaje_limpio or 'Analiza el archivo adjunto.'}"
    )
    partes.append(types.Part(text=texto_final))

    session_id = await _obtener_sesion(usuario_id)
    msg = types.Content(role="user", parts=partes)

    respuestas: list[str] = []
    tools_usadas: list[dict] = []

    async for ev in runner.run_async(
        user_id=usuario_id, session_id=session_id, new_message=msg
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

    return {
        "ok": True,
        "usuario_id": usuario_id,
        "tools_usadas": tools_usadas,
        "respuesta": "\n".join(r.strip() for r in respuestas if r.strip()),
    }
