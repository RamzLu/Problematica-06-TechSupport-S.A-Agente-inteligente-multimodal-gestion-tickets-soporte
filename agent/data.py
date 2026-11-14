import copy
import json
from collections.abc import Mapping
from pathlib import Path

from .repo import get_repo

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
DATASET_PATH = DATASET_DIR / "datos_iniciales.json"

CATEGORIAS: list[str] = []
PRIORIDADES: list[str] = []
ESTADOS: list[str] = []
TRANSICIONES_PERMITIDAS: dict[str, list[str]] = {}
REGLAS_CLASIFICACION: dict[str, dict] = {}
PERMISOS_POR_ROL: dict[str, set[str]] = {}


class _Directorio(Mapping):
    """Vista de solo lectura sobre un diccionario que vive en la base de datos."""

    def __init__(self, obtener):
        self._obtener = obtener

    def __getitem__(self, clave):
        return self._obtener()[clave]

    def __iter__(self):
        return iter(self._obtener())

    def __len__(self):
        return len(self._obtener())


USUARIOS: Mapping = _Directorio(lambda: get_repo().usuarios())
ORGANIZACIONES: Mapping = _Directorio(lambda: get_repo().organizaciones())


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


def cargar_dataset(ruta: Path | str = DATASET_PATH) -> None:
    raw = json.loads(Path(ruta).read_text(encoding="utf-8"))
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

    REGLAS_CLASIFICACION.clear()
    for clave, valor in cfg.get("reglas_clasificacion", {}).items():
        REGLAS_CLASIFICACION[clave] = copy.deepcopy(valor)

    PERMISOS_POR_ROL.clear()
    for rol, perms in cfg.get("permisos_por_rol", {}).items():
        PERMISOS_POR_ROL[rol] = set(perms)


cargar_dataset()
