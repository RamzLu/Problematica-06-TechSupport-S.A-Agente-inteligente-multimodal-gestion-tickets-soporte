import re
import unicodedata
from datetime import datetime
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

from ..data import (
    CATEGORIAS,
    ESTADOS,
    PERMISOS_POR_ROL,
    PRIORIDADES,
    TICKETS,
    TRANSICIONES_PERMITIDAS,
    USUARIOS,
)


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _sin_tildes(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def _normalizar(valor: str, catalogo: list):
    for oficial in catalogo:
        if _sin_tildes(oficial) == _sin_tildes(valor):
            return oficial
    return None


def _registrar(ticket: dict, usuario_id: str, tipo: str, detalle: str) -> None:
    ticket["historial"].append(
        {"fecha": _ahora(), "usuario": usuario_id, "tipo": tipo, "detalle": detalle}
    )


def _error(mensaje: str) -> dict:
    return {"ok": False, "error": mensaje}


_PATRONES_SQLI = re.compile(
    r"("
    r"(\bunion\s+(all\s+)?select\b)|"
    r"(\bselect\b.+\bfrom\b)|"
    r"(\binsert\s+into\b)|"
    r"(\bupdate\s+\w+\s+set\b)|"
    r"(\bdelete\s+from\b)|"
    r"(\bdrop\s+(table|database|view|index)\b)|"
    r"(\balter\s+table\b)|"
    r"(\btruncate\s+table\b)|"
    r"(\bexec(ute)?\s*\()|"
    r"(\bxp_cmdshell\b)|"
    r"(['\"]\s*(or|and)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+)|"
    r"(['\"]\s*(or|and)\s+['\"][^'\"]+['\"]\s*=\s*['\"]?[^'\"]+)|"
    r"(['\"];\s*--)|"
    r"(/\*.*?\*/)|"
    r"(;\s*(drop|delete|insert|update|alter|truncate)\b)"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_PATRON_ID_SEGURO = re.compile(r"^[a-zA-Z0-9_-]{1,30}$")
_PATRON_ARCHIVO_SEGURO = re.compile(r"^[a-zA-Z0-9._-]{1,120}$")


def sanitizar_texto(valor: str, campo: str = "texto", max_largo: int = 2000) -> tuple[str | None, dict | None]:
    if valor is None:
        return "", None
    limpio = str(valor).replace("\x00", "").strip()
    if len(limpio) > max_largo:
        return None, _error(f"El campo '{campo}' supera el largo maximo permitido ({max_largo} caracteres).")
    if _PATRONES_SQLI.search(limpio):
        return None, _error(
            f"Entrada bloqueada por seguridad: se detecto un patron sospechoso de inyeccion SQL en '{campo}'."
        )
    return limpio, None


def sanitizar_identificador(valor: str, campo: str = "usuario_id") -> tuple[str | None, dict | None]:
    limpio, err = sanitizar_texto(valor, campo=campo, max_largo=30)
    if err:
        return None, err
    if not limpio or not _PATRON_ID_SEGURO.match(limpio):
        return None, _error(
            f"Identificador invalido en '{campo}': solo se permiten letras, numeros, guion y guion bajo."
        )
    return limpio, None


def _validar_usuario(usuario_id: str):
    uid, err = sanitizar_identificador(usuario_id, campo="usuario_id")
    if err:
        return err
    if uid not in USUARIOS:
        return _error(f"Usuario '{uid}' no existe en el directorio.")
    return None


def _tiene_permiso(usuario_id: str, permiso: str) -> bool:
    rol = USUARIOS[usuario_id]["rol"]
    return permiso in PERMISOS_POR_ROL[rol]


def _puede_ver_o_tocar(usuario_id: str, ticket: dict, accion: str):
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
    if _tiene_permiso(usuario_id, "consultar_todos"):
        return list(TICKETS.values())
    return [t for t in TICKETS.values() if t["solicitante"] == usuario_id]


VECTOR_STORE_DIR = str(Path(__file__).resolve().parent.parent.parent / "vector_store")
COLECCION_TICKETS = "tickets_soporte"
UMBRAL_DUPLICADO = 0.55

_vector_store = None
_ids_indexados = set()


def _obtener_vector_store() -> Chroma:
    global _vector_store, _ids_indexados
    if _vector_store is None:
        client = chromadb.PersistentClient(path=VECTOR_STORE_DIR)
        try:
            client.delete_collection(COLECCION_TICKETS)
        except Exception:
            pass
        _vector_store = Chroma(
            client=client,
            collection_name=COLECCION_TICKETS,
            embedding_function=OllamaEmbeddings(model="nomic-embed-text"),
        )
        _ids_indexados = set()

    faltantes = [t for tid, t in TICKETS.items() if tid not in _ids_indexados]
    if faltantes:
        docs = [
            Document(
                page_content=f"{t['titulo']}. {t['descripcion']}",
                metadata={"ticket_id": t["id"], "categoria": t["categoria"]},
            )
            for t in faltantes
        ]
        _vector_store.add_documents(docs, ids=[str(t["id"]) for t in faltantes])
        _ids_indexados.update(t["id"] for t in faltantes)

    return _vector_store


def _buscar_similares(texto: str, k: int = 3, umbral: float = UMBRAL_DUPLICADO) -> list:
    vs = _obtener_vector_store()
    resultados = vs.similarity_search_with_relevance_scores(query=texto, k=k)
    coincidencias = []
    for doc, score in resultados:
        tid = int(doc.metadata["ticket_id"])
        ticket = TICKETS.get(tid)
        if ticket and score >= umbral:
            coincidencias.append(
                {
                    **_resumen_corto(ticket),
                    "descripcion": ticket["descripcion"],
                    "similitud": round(float(score), 4),
                }
            )
    return coincidencias


def buscar_duplicados(consulta: str) -> dict:
    """
    Busca en ChromaDB si ya existen tickets similares a la incidencia descrita.

    Args:
        consulta: titulo y/o descripcion del problema reportado por el usuario.
    """
    if not consulta or not consulta.strip():
        return _error("Falta el texto de la incidencia para buscar duplicados.")
    consulta_limpia, err = sanitizar_texto(consulta, campo="consulta", max_largo=1500)
    if err:
        return err
    try:
        coincidencias = _buscar_similares(consulta_limpia)
    except Exception as e:
        return _error(f"Error al consultar la base vectorial de duplicados: {e}")

    if not coincidencias:
        return {
            "ok": True,
            "posibles_duplicados": 0,
            "coincidencias": [],
            "mensaje": "No se encontraron tickets similares.",
        }
    return {
        "ok": True,
        "posibles_duplicados": len(coincidencias),
        "coincidencias": coincidencias,
        "mensaje": (
            "Se encontraron tickets similares. Advierte al usuario y solicita su "
            "confirmacion antes de crear un ticket nuevo."
        ),
    }


def crear_ticket(
    usuario_id: str,
    titulo: str,
    descripcion: str,
    categoria: str,
    prioridad: str,
    confirmar_duplicado: bool = False,
) -> dict:
    """
    Crea un ticket nuevo de soporte tecnico. Verifica duplicados en ChromaDB antes de crear.

    Args:
        usuario_id: identificador del usuario solicitante (por ejemplo "u001").
        titulo: titulo corto de la incidencia.
        descripcion: descripcion del problema, sin inventar datos.
        categoria: Hardware, Software, Redes y conectividad, Sistemas y aplicaciones,
            Accesos y permisos, Infraestructura u Otros.
        prioridad: Baja, Media, Alta o Critica.
        confirmar_duplicado: False por defecto; True solo si el usuario confirmo crear pese al duplicado.
    """
    error = _validar_usuario(usuario_id) or _exigir_permiso(usuario_id, "crear", "crear tickets")
    if error:
        return error
    titulo_limpio, err = sanitizar_texto(titulo, campo="titulo", max_largo=200)
    if err:
        return err
    if not titulo_limpio:
        return _error("Falta el titulo del ticket.")
    desc_limpia, err = sanitizar_texto(descripcion, campo="descripcion", max_largo=2000)
    if err:
        return err
    if not desc_limpia:
        return _error("Falta la descripcion del ticket.")
    categoria_ok = _normalizar(categoria, CATEGORIAS)
    if categoria_ok is None:
        return _error(f"Categoria invalida '{categoria}'. Opciones: {CATEGORIAS}")
    prioridad_ok = _normalizar(prioridad, PRIORIDADES)
    if prioridad_ok is None:
        return _error(f"Prioridad invalida '{prioridad}'. Opciones: {PRIORIDADES}")

    if not confirmar_duplicado:
        try:
            similares = [
                c
                for c in _buscar_similares(f"{titulo_limpio}. {desc_limpia}")
                if c["estado"] != "Cerrado"
            ]
        except Exception:
            similares = []
        if similares:
            return {
                "ok": False,
                "duplicado_detectado": True,
                "coincidencias": similares,
                "error": (
                    "Se detectaron tickets similares ya registrados. NO se creo el ticket. "
                    "Informa al usuario sobre estas coincidencias y preguntale si desea "
                    "crear el ticket igualmente (en ese caso vuelve a llamar a crear_ticket "
                    "con confirmar_duplicado=True) o si prefiere consultar/actualizar el existente."
                ),
            }

    nuevo_id = max(TICKETS) + 1 if TICKETS else 1001
    ticket = {
        "id": nuevo_id,
        "creado": _ahora(),
        "solicitante": usuario_id,
        "titulo": titulo_limpio,
        "descripcion": desc_limpia,
        "categoria": categoria_ok,
        "prioridad": prioridad_ok,
        "adjuntos": [],
        "estado": "Abierto",
        "responsable": None,
        "historial": [],
    }
    _registrar(ticket, usuario_id, "Creacion", "Ticket creado")
    TICKETS[nuevo_id] = ticket
    try:
        _obtener_vector_store()
    except Exception:
        pass
    return {"ok": True, "ticket": ticket}


def consultar_ticket(ticket_id: int, usuario_id: str) -> dict:
    """
    Devuelve todos los datos de un ticket a partir de su identificador numerico.

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
    Busca tickets filtrando por solicitante, estado, categoria y/o prioridad.

    Args:
        usuario_id: identificador de quien hace la busqueda.
        solicitante: filtrar por solicitante (vacio = todos los visibles).
        estado: Abierto, En revision, En progreso, Pendiente, Resuelto o Cerrado.
        categoria: una de las categorias oficiales.
        prioridad: Baja, Media, Alta o Critica.
    """
    error = _validar_usuario(usuario_id)
    if error:
        return error
    filtros = {}
    if solicitante:
        sol_ok, err = sanitizar_identificador(solicitante, campo="solicitante")
        if err:
            return err
        if sol_ok not in USUARIOS:
            return _error(f"Usuario '{sol_ok}' no existe en el directorio.")
        filtros["solicitante"] = sol_ok
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
    Cambia el estado de un ticket respetando las transiciones permitidas (solo soporte).

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
    Modifica la prioridad de un ticket (solo soporte).

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
    Asigna o reasigna un ticket a un integrante de soporte (solo soporte).

    Args:
        ticket_id: numero del ticket.
        usuario_id: identificador de quien realiza la asignacion.
        responsable_id: identificador del integrante de soporte asignado.
    """
    error = _validar_usuario(usuario_id) or _exigir_permiso(
        usuario_id, "asignar", "asignar responsables"
    )
    if error:
        return error
    ticket, error = _obtener_ticket(ticket_id)
    if error:
        return error
    resp_ok, err = sanitizar_identificador(responsable_id, campo="responsable_id")
    if err:
        return err
    if resp_ok not in USUARIOS:
        return _error(f"Usuario '{resp_ok}' no existe en el directorio.")
    if USUARIOS[resp_ok]["rol"] != "soporte":
        return _error(f"{resp_ok} no pertenece al personal de soporte.")
    anterior = ticket["responsable"]
    if anterior == resp_ok:
        return _error(f"El ticket {ticket['id']} ya esta asignado a {resp_ok}.")
    ticket["responsable"] = resp_ok
    tipo = "Asignacion" if anterior is None else "Reasignacion"
    _registrar(ticket, usuario_id, tipo, f"{anterior or 'sin asignar'} -> {resp_ok}")
    return {"ok": True, "ticket": _resumen_corto(ticket)}


def agregar_comentario(ticket_id: int, usuario_id: str, comentario: str) -> dict:
    """
    Agrega un comentario a un ticket y lo registra en el historial.

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
    comentario_limpio, err = sanitizar_texto(comentario, campo="comentario", max_largo=1500)
    if err:
        return err
    _registrar(ticket, usuario_id, "Comentario", comentario_limpio)
    return {"ok": True, "ticket_id": ticket["id"], "mensaje": "Comentario agregado."}


def adjuntar_archivo(ticket_id: int, usuario_id: str, nombre_archivo: str) -> dict:
    """
    Registra un archivo adjunto en un ticket.

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
    nombre, err = sanitizar_texto(nombre_archivo, campo="nombre_archivo", max_largo=120)
    if err:
        return err
    nombre = Path(nombre).name
    if not _PATRON_ARCHIVO_SEGURO.match(nombre):
        return _error("Nombre de archivo invalido: contiene caracteres no permitidos.")
    if nombre in ticket["adjuntos"]:
        return _error(f"El archivo '{nombre}' ya esta adjunto al ticket {ticket['id']}.")
    ticket["adjuntos"].append(nombre)
    _registrar(ticket, usuario_id, "Archivo", f"Adjunto {nombre}")
    return {"ok": True, "ticket_id": ticket["id"], "adjuntos": ticket["adjuntos"]}


def consultar_historial(ticket_id: int, usuario_id: str) -> dict:
    """
    Devuelve el historial completo de eventos de un ticket.

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
    Genera un resumen de incidencias agrupadas por estado, categoria y prioridad.

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


ARCHIVOS_DIR = Path(__file__).resolve().parent.parent.parent / "archivos"
EXTENSIONES_TEXTO = {".txt", ".log"}


def listar_archivos() -> dict:
    """
    Lista los archivos disponibles en la carpeta 'archivos/' del proyecto.
    """
    if not ARCHIVOS_DIR.exists():
        return {"ok": True, "archivos": [], "mensaje": "La carpeta 'archivos/' no existe."}
    nombres = sorted(p.name for p in ARCHIVOS_DIR.iterdir() if p.is_file())
    return {"ok": True, "cantidad": len(nombres), "archivos": nombres}


def leer_archivo(nombre_archivo: str) -> dict:
    """
    Lee y devuelve el contenido de un archivo .txt o .log ubicado en 'archivos/'.

    Args:
        nombre_archivo: nombre del archivo dentro de 'archivos/'.
    """
    if not nombre_archivo or not nombre_archivo.strip():
        return _error("Falta indicar el nombre del archivo.")
    nombre_san, err = sanitizar_texto(nombre_archivo, campo="nombre_archivo", max_largo=120)
    if err:
        return err
    limpio = Path(nombre_san).name
    if not _PATRON_ARCHIVO_SEGURO.match(limpio):
        return _error("Nombre de archivo invalido: contiene caracteres no permitidos.")
    ruta = ARCHIVOS_DIR / limpio
    if not ruta.exists() or not ruta.is_file():
        return _error(f"El archivo '{limpio}' no existe en la carpeta 'archivos/'.")

    ext = ruta.suffix.lower()
    if ext not in EXTENSIONES_TEXTO:
        return _error(
            f"Limitacion: la herramienta leer_archivo solo interpreta archivos .txt y .log "
            f"(recibio '{ext}'). Si es una imagen o captura de pantalla, el usuario debe "
            f"adjuntarla directamente en el mensaje del chat para analizarla con vision."
        )

    try:
        contenido = ruta.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error(
            f"Limitacion: no se pudo interpretar '{limpio}' porque no es texto UTF-8 valido."
        )
    except OSError as e:
        return _error(f"No se pudo leer '{limpio}': {e}")

    if not contenido.strip():
        return _error(f"Limitacion: el archivo '{limpio}' esta vacio.")

    return {
        "ok": True,
        "archivo": limpio,
        "tipo": ext.lstrip(".").upper(),
        "contenido": contenido,
    }
