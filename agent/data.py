import copy
import hashlib
import hmac
import json
import secrets
from pathlib import Path

DATASET_PATH = Path(__file__).resolve().parent.parent / "dataset" / "datos_iniciales.json"

CATEGORIAS: list[str] = []
PRIORIDADES: list[str] = []
ESTADOS: list[str] = []
TRANSICIONES_PERMITIDAS: dict[str, list[str]] = {}
PERMISOS_POR_ROL: dict[str, set[str]] = {}
ORGANIZACIONES: dict[str, dict] = {}
USUARIOS: dict[str, dict] = {}
TICKETS: dict[int, dict] = {}


def hashear_password(password: str, salt: str | None = None, iteraciones: int = 100000) -> str:
    if salt is None:
        salt = secrets.token_hex(8)
    derivado = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iteraciones,
    ).hex()
    return f"pbkdf2_sha256${iteraciones}${salt}${derivado}"


def verificar_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    partes = password_hash.split("$")
    if len(partes) != 4 or partes[0] != "pbkdf2_sha256":
        return False
    try:
        iteraciones = int(partes[1])
    except ValueError:
        return False
    salt = partes[2]
    esperado = partes[3]
    calculado = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iteraciones,
    ).hex()
    return hmac.compare_digest(calculado, esperado)


def usuario_publico(uid: str) -> dict | None:
    datos = USUARIOS.get(uid)
    if not datos:
        return None
    org_id = datos.get("organizacion_id", "")
    org_nombre = ORGANIZACIONES.get(org_id, {}).get("nombre", org_id)
    return {
        "id": uid,
        "nombre": datos["nombre"],
        "rol": datos["rol"],
        "email": datos.get("email", ""),
        "organizacion_id": org_id,
        "organizacion": org_nombre,
        "area": datos.get("area", "General"),
        "cargo": datos.get("cargo", ""),
    }


def autenticar_credenciales(identificador: str, password: str) -> dict | None:
    if not identificador or not password:
        return None
    ident = identificador.strip().lower()
    uid_encontrado = None
    for uid, datos in USUARIOS.items():
        if uid.lower() == ident or datos.get("email", "").lower() == ident:
            uid_encontrado = uid
            break
    if not uid_encontrado:
        return None
    hash_guardado = USUARIOS[uid_encontrado].get("password_hash", "")
    if not verificar_password(password, hash_guardado):
        return None
    return usuario_publico(uid_encontrado)


def cargar_dataset(ruta: Path | str = DATASET_PATH) -> None:
    ruta_path = Path(ruta)
    raw = json.loads(ruta_path.read_text(encoding="utf-8"))
    cfg = raw.get("configuracion", {})

    CATEGORIAS.clear()
    CATEGORIAS.extend(cfg.get("categorias", []))

    PRIORIDADES.clear()
    PRIORIDADES.extend(cfg.get("prioridades", []))

    ESTADOS.clear()
    ESTADOS.extend(cfg.get("estados", []))

    TRANSICIONES_PERMITIDAS.clear()
    for est, dests in cfg.get("transiciones_permitidas", {}).items():
        TRANSICIONES_PERMITIDAS[est] = list(dests)

    PERMISOS_POR_ROL.clear()
    for rol, perms in cfg.get("permisos_por_rol", {}).items():
        PERMISOS_POR_ROL[rol] = set(perms)

    ORGANIZACIONES.clear()
    for oid, odata in raw.get("organizaciones", {}).items():
        ORGANIZACIONES[oid] = copy.deepcopy(odata)

    USUARIOS.clear()
    for uid, udata in raw.get("usuarios", {}).items():
        USUARIOS[uid] = copy.deepcopy(udata)

    TICKETS.clear()
    for t in raw.get("tickets", []):
        tid = int(t["id"])
        copia = copy.deepcopy(t)
        copia["id"] = tid
        TICKETS[tid] = copia


cargar_dataset()
