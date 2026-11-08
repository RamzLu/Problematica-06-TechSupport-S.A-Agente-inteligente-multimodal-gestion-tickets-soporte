"""
Tools (herramientas) del agente de tickets de TechSupport S.A.

Cada funcion es una tool: el modelo lee su docstring para saber cuando usarla y que
argumentos recibe (apunte "Fundamentos de Agentes", seccion 4). Por eso los docstrings
son parte del funcionamiento y no solo documentacion.

Todas devuelven un diccionario con "ok": True/False. Si algo falla (argumento invalido,
ticket inexistente, resultado vacio, falta de permiso, transicion no permitida) devuelven
{"ok": False, "error": "..."} en lugar de lanzar una excepcion, para que el agente pueda
explicarle el problema al usuario.

Controles (Paso 5): el apunte de agentes llama "Controles" a los limites, validaciones y
permisos de un agente, y dice que la aplicacion controla que herramientas existen y que
permisos tienen. Aqui los controles viven dentro de las tools, no en el modelo: aunque el
modelo se equivoque, la tool rechaza la operacion.

Los permisos por rol y las transiciones de estado son una PROPUESTA (ver data.py): el PDF
exige respetarlos pero no los define.
"""

import unicodedata
from datetime import datetime

from ..data import (
    CATEGORIAS,
    ESTADOS,
    PERMISOS_POR_ROL,
    PRIORIDADES,
    TICKETS,
    TRANSICIONES_PERMITIDAS,
    USUARIOS,
)


# --- Funciones auxiliares (no son tools: el agente no las ve) ----------------------


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _sin_tildes(texto: str) -> str:
    """Pasa a minusculas y quita tildes, para comparar 'Crítica' con 'Critica'."""
    texto = unicodedata.normalize("NFD", texto.strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _normalizar(valor: str, catalogo: list):
    """Devuelve el valor oficial del catalogo que coincide con 'valor', o None."""
    for oficial in catalogo:
        if _sin_tildes(oficial) == _sin_tildes(valor):
            return oficial
    return None


def _registrar(ticket: dict, usuario_id: str, tipo: str, detalle: str) -> None:
    """Agrega un evento al historial del ticket (F11): fecha, responsable y detalle."""
    ticket["historial"].append(
        {"fecha": _ahora(), "usuario": usuario_id, "tipo": tipo, "detalle": detalle}
    )


def _error(mensaje: str) -> dict:
    return {"ok": False, "error": mensaje}


def _validar_usuario(usuario_id: str):
    if not usuario_id or usuario_id not in USUARIOS:
        return _error(f"Usuario '{usuario_id}' no existe en el directorio.")
    return None


def _tiene_permiso(usuario_id: str, permiso: str) -> bool:
    rol = USUARIOS[usuario_id]["rol"]
    return permiso in PERMISOS_POR_ROL[rol]


def _puede_ver_o_tocar(usuario_id: str, ticket: dict, accion: str):
    """
    Control de acceso a un ticket concreto. 'accion' es consultar, comentar o adjuntar.
    Soporte puede con todos (permiso <accion>_todos); un usuario solo con los suyos
    (permiso <accion>_propios y ser el solicitante). Devuelve un error o None.
    """
    if _tiene_permiso(usuario_id, f"{accion}_todos"):
        return None
    if _tiene_permiso(usuario_id, f"{accion}_propios") and ticket["solicitante"] == usuario_id:
        return None
    return _error(
        f"El usuario {usuario_id} no tiene autorizacion para {accion} el ticket {ticket['id']}."
    )


def _exigir_permiso(usuario_id: str, permiso: str, descripcion: str):
    if not _tiene_permiso(usuario_id, permiso):
        return _error(f"El usuario {usuario_id} no tiene permiso para {descripcion}.")
    return None


def _obtener_ticket(ticket_id: int):
    """Devuelve (ticket, None) o (None, error)."""
    try:
        ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        return None, _error(f"El identificador '{ticket_id}' no es un numero valido.")
    ticket = TICKETS.get(ticket_id)
    if ticket is None:
        return None, _error(f"No existe el ticket {ticket_id}.")
    return ticket, None


def _resumen_corto(t: dict) -> dict:
    return {
        "id": t["id"],
        "titulo": t["titulo"],
        "estado": t["estado"],
        "prioridad": t["prioridad"],
        "categoria": t["categoria"],
        "solicitante": t["solicitante"],
        "responsable": t["responsable"],
    }


def _tickets_visibles(usuario_id: str) -> list:
    """Soporte ve todos los tickets; un usuario solo los suyos."""
    if _tiene_permiso(usuario_id, "consultar_todos"):
        return list(TICKETS.values())
    return [t for t in TICKETS.values() if t["solicitante"] == usuario_id]


# --- Tools ---------------------------------------------------------------------------


def crear_ticket(
    usuario_id: str, titulo: str, descripcion: str, categoria: str, prioridad: str
) -> dict:
    """
    Crea un ticket nuevo de soporte tecnico. Usala solo cuando ya tengas todos los datos.

    Args:
        usuario_id: identificador del usuario solicitante (por ejemplo "u001").
        titulo: titulo corto de la incidencia.
        descripcion: descripcion del problema, sin inventar datos que el usuario no dio.
        categoria: una de Hardware, Software, Redes y conectividad,
            Sistemas y aplicaciones, Accesos y permisos, Infraestructura, Otros.
        prioridad: una de Baja, Media, Alta, Critica (es una propuesta que soporte puede
            revisar y modificar).
    """
    error = _validar_usuario(usuario_id) or _exigir_permiso(usuario_id, "crear", "crear tickets")
    if error:
        return error
    if not titulo or not titulo.strip():
        return _error("Falta el titulo del ticket.")
    if not descripcion or not descripcion.strip():
        return _error("Falta la descripcion del ticket.")
    categoria_ok = _normalizar(categoria, CATEGORIAS)
    if categoria_ok is None:
        return _error(f"Categoria invalida '{categoria}'. Opciones: {CATEGORIAS}")
    prioridad_ok = _normalizar(prioridad, PRIORIDADES)
    if prioridad_ok is None:
        return _error(f"Prioridad invalida '{prioridad}'. Opciones: {PRIORIDADES}")

    nuevo_id = max(TICKETS) + 1 if TICKETS else 1001
    ticket = {
        "id": nuevo_id,
        "creado": _ahora(),
        "solicitante": usuario_id,
        "titulo": titulo.strip(),
        "descripcion": descripcion.strip(),
        "categoria": categoria_ok,
        "prioridad": prioridad_ok,
        "adjuntos": [],
        "estado": "Abierto",
        "responsable": None,
        "historial": [],
    }
    _registrar(ticket, usuario_id, "Creacion", "Ticket creado")
    TICKETS[nuevo_id] = ticket
    return {"ok": True, "ticket": ticket}


def consultar_ticket(ticket_id: int, usuario_id: str) -> dict:
    """
    Devuelve todos los datos de un ticket a partir de su identificador numerico.
    Un usuario solo puede consultar sus propios tickets; el personal de soporte, todos.

    Args:
        ticket_id: numero del ticket (por ejemplo 1052).
        usuario_id: identificador de quien consulta (por ejemplo "u001").
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    error = _puede_ver_o_tocar(usuario_id, ticket, "consultar")
    if error:
        return error
    return {"ok": True, "ticket": ticket}


def buscar_tickets(
    usuario_id: str,
    solicitante: str = "",
    estado: str = "",
    categoria: str = "",
    prioridad: str = "",
) -> dict:
    """
    Busca tickets filtrando por solicitante, estado, categoria y/o prioridad. Los filtros
    vacios no se aplican. Un usuario solo ve sus propios tickets; soporte ve todos.

    Args:
        usuario_id: identificador de quien hace la busqueda (obligatorio).
        solicitante: filtrar por el solicitante del ticket (por ejemplo "u001"). Vacio = todos
            los que el usuario pueda ver.
        estado: Abierto, En revision, En progreso, Pendiente, Resuelto o Cerrado.
        categoria: una de las categorias oficiales.
        prioridad: Baja, Media, Alta o Critica.
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    filtros = {}
    if solicitante:
        if solicitante not in USUARIOS:
            return _error(f"Usuario '{solicitante}' no existe en el directorio.")
        filtros["solicitante"] = solicitante
    for nombre, valor, catalogo in (
        ("estado", estado, ESTADOS),
        ("categoria", categoria, CATEGORIAS),
        ("prioridad", prioridad, PRIORIDADES),
    ):
        if valor:
            oficial = _normalizar(valor, catalogo)
            if oficial is None:
                return _error(f"Valor invalido '{valor}' para {nombre}. Opciones: {catalogo}")
            filtros[nombre] = oficial

    resultados = [
        _resumen_corto(t)
        for t in _tickets_visibles(usuario_id)
        if all(t[campo] == valor for campo, valor in filtros.items())
    ]
    if not resultados:
        return {"ok": True, "cantidad": 0, "tickets": [], "mensaje": "No hay tickets que coincidan."}
    return {"ok": True, "cantidad": len(resultados), "tickets": resultados}


def actualizar_estado(ticket_id: int, usuario_id: str, nuevo_estado: str) -> dict:
    """
    Cambia el estado de un ticket respetando las transiciones permitidas, y lo registra
    en el historial. Solo el personal de soporte puede cambiar estados. Antes de usar esta
    tool, confirma con el usuario el cambio.

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien solicita el cambio (por ejemplo "s001").
        nuevo_estado: Abierto, En revision, En progreso, Pendiente, Resuelto o Cerrado.
    """
    error = _validar_usuario(usuario_id) or _exigir_permiso(
        usuario_id, "cambiar_estado", "cambiar el estado de un ticket"
    )
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    estado_ok = _normalizar(nuevo_estado, ESTADOS)
    if estado_ok is None:
        return _error(f"Estado invalido '{nuevo_estado}'. Opciones: {ESTADOS}")
    anterior = ticket["estado"]
    if estado_ok == anterior:
        return _error(f"El ticket {ticket['id']} ya esta en estado '{estado_ok}'.")
    permitidos = TRANSICIONES_PERMITIDAS[anterior]
    if estado_ok not in permitidos:
        return _error(
            f"Transicion no permitida: de '{anterior}' a '{estado_ok}'. "
            f"Desde '{anterior}' solo se puede pasar a: {permitidos or 'ningun estado (estado final)'}."
        )

    ticket["estado"] = estado_ok
    _registrar(ticket, usuario_id, "Cambio de estado", f"{anterior} -> {estado_ok}")
    return {"ok": True, "ticket": _resumen_corto(ticket)}


def cambiar_prioridad(ticket_id: int, usuario_id: str, nueva_prioridad: str) -> dict:
    """
    Modifica la prioridad de un ticket (revision de la prioridad propuesta) y lo registra
    en el historial. Solo el personal de soporte puede hacerlo. Confirma con el usuario
    antes de usarla.

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien modifica (por ejemplo "s001").
        nueva_prioridad: Baja, Media, Alta o Critica.
    """
    error = _validar_usuario(usuario_id) or _exigir_permiso(
        usuario_id, "cambiar_prioridad", "cambiar la prioridad de un ticket"
    )
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    prioridad_ok = _normalizar(nueva_prioridad, PRIORIDADES)
    if prioridad_ok is None:
        return _error(f"Prioridad invalida '{nueva_prioridad}'. Opciones: {PRIORIDADES}")
    anterior = ticket["prioridad"]
    if prioridad_ok == anterior:
        return _error(f"El ticket {ticket['id']} ya tiene prioridad '{prioridad_ok}'.")
    ticket["prioridad"] = prioridad_ok
    _registrar(ticket, usuario_id, "Cambio de prioridad", f"{anterior} -> {prioridad_ok}")
    return {"ok": True, "ticket": _resumen_corto(ticket)}


def asignar_responsable(ticket_id: int, usuario_id: str, responsable_id: str) -> dict:
    """
    Asigna o reasigna un ticket a un integrante del personal de soporte y lo registra en
    el historial. Solo el personal de soporte puede hacerlo. Confirma antes de usarla.

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien realiza la asignacion (por ejemplo "s001").
        responsable_id: identificador del integrante de soporte que quedara a cargo
            (por ejemplo "s002").
    """
    error = _validar_usuario(usuario_id) or _exigir_permiso(
        usuario_id, "asignar", "asignar responsables"
    )
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    if responsable_id not in USUARIOS:
        return _error(f"Usuario '{responsable_id}' no existe en el directorio.")
    if USUARIOS[responsable_id]["rol"] != "soporte":
        return _error(f"{responsable_id} no pertenece al personal de soporte.")
    anterior = ticket["responsable"]
    if anterior == responsable_id:
        return _error(f"El ticket {ticket['id']} ya esta asignado a {responsable_id}.")
    ticket["responsable"] = responsable_id
    tipo = "Asignacion" if anterior is None else "Reasignacion"
    _registrar(ticket, usuario_id, tipo, f"{anterior or 'sin asignar'} -> {responsable_id}")
    return {"ok": True, "ticket": _resumen_corto(ticket)}


def agregar_comentario(ticket_id: int, usuario_id: str, comentario: str) -> dict:
    """
    Agrega un comentario a un ticket y lo registra en el historial. Un usuario solo puede
    comentar sus propios tickets; soporte, cualquiera.

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien comenta.
        comentario: texto del comentario.
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    error = _puede_ver_o_tocar(usuario_id, ticket, "comentar")
    if error:
        return error
    if not comentario or not comentario.strip():
        return _error("El comentario esta vacio.")
    _registrar(ticket, usuario_id, "Comentario", comentario.strip())
    return {"ok": True, "ticket_id": ticket["id"], "mensaje": "Comentario agregado."}


def adjuntar_archivo(ticket_id: int, usuario_id: str, nombre_archivo: str) -> dict:
    """
    Registra un archivo adjunto en un ticket (por ejemplo una captura o un archivo LOG).
    Un usuario solo puede adjuntar a sus propios tickets; soporte, a cualquiera.

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien adjunta.
        nombre_archivo: nombre del archivo, por ejemplo "error.log" o "captura.png".
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    error = _puede_ver_o_tocar(usuario_id, ticket, "adjuntar")
    if error:
        return error
    if not nombre_archivo or not nombre_archivo.strip():
        return _error("Falta el nombre del archivo.")
    nombre = nombre_archivo.strip()
    if nombre in ticket["adjuntos"]:
        return _error(f"El archivo '{nombre}' ya esta adjunto al ticket {ticket['id']}.")
    ticket["adjuntos"].append(nombre)
    _registrar(ticket, usuario_id, "Archivo", f"Adjunto {nombre}")
    return {"ok": True, "ticket_id": ticket["id"], "adjuntos": ticket["adjuntos"]}


def consultar_historial(ticket_id: int, usuario_id: str) -> dict:
    """
    Devuelve el historial completo de eventos de un ticket: creacion, cambios de estado y
    prioridad, comentarios, archivos y asignaciones, con fecha y responsable de cada uno.
    Un usuario solo puede ver el historial de sus propios tickets; soporte, el de todos.

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien consulta.
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    error = _puede_ver_o_tocar(usuario_id, ticket, "consultar")
    if error:
        return error
    return {"ok": True, "ticket_id": ticket["id"], "historial": ticket["historial"]}


def generar_resumen(usuario_id: str) -> dict:
    """
    Genera un resumen de incidencias agrupadas por estado, por categoria y por prioridad.
    Un usuario ve el resumen de sus propios tickets; soporte, el de todos. Sirve para
    preguntas como "cuantos tickets criticos hay pendientes".

    Args:
        usuario_id: identificador de quien pide el resumen.
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    visibles = _tickets_visibles(usuario_id)
    if not visibles:
        return {"ok": True, "total": 0, "mensaje": "No hay tickets para mostrar."}
    resumen = {
        "ok": True,
        "total": len(visibles),
        "por_estado": {e: 0 for e in ESTADOS},
        "por_categoria": {c: 0 for c in CATEGORIAS},
        "por_prioridad": {p: 0 for p in PRIORIDADES},
    }
    for t in visibles:
        resumen["por_estado"][t["estado"]] += 1
        resumen["por_categoria"][t["categoria"]] += 1
        resumen["por_prioridad"][t["prioridad"]] += 1
    return resumen
