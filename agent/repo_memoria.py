import copy
import csv
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset"
USUARIOS_DEMO_PATH = DATASET_DIR / "usuarios_demo_memoria.json"
TICKETS_CSV_PATH = DATASET_DIR / "tickets_iniciales.csv"
ARCHIVOS_DEMO_DIR = Path(__file__).resolve().parent.parent / "archivos"


def hashear_password(password: str, salt: Optional[str] = None, iteraciones: int = 100000) -> str:
    if salt is None:
        salt = secrets.token_hex(8)
    derivado = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iteraciones
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
    calculado = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), partes[2].encode("utf-8"), iteraciones
    ).hex()
    return hmac.compare_digest(calculado, partes[3])


def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class RepoMemoria:
    def __init__(self):
        self.reiniciar()

    def reiniciar(self) -> None:
        raw = json.loads(USUARIOS_DEMO_PATH.read_text(encoding="utf-8"))
        self._organizaciones = copy.deepcopy(raw["organizaciones"])
        self._usuarios = {}
        for codigo, u in raw["usuarios"].items():
            datos = copy.deepcopy(u)
            datos["auth_id"] = f"auth-{codigo}"
            datos.setdefault("activo", True)
            self._usuarios[codigo] = datos
        self._tickets: dict[int, dict] = {}
        with TICKETS_CSV_PATH.open(encoding="utf-8", newline="") as f:
            for fila in csv.DictReader(f):
                tid = int(fila["id"])
                self._tickets[tid] = {
                    "id": tid,
                    "creado": fila["creado"],
                    "solicitante": fila["solicitante"],
                    "organizacion_id": fila.get("organizacion_id", "org_central"),
                    "area": fila.get("area", "General"),
                    "titulo": fila["titulo"],
                    "descripcion": fila["descripcion"],
                    "categoria": fila["categoria"],
                    "prioridad": fila["prioridad"],
                    "adjuntos": json.loads((fila.get("adjuntos") or "[]").strip()),
                    "estado": fila["estado"],
                    "responsable": (fila.get("responsable") or "").strip() or None,
                    "historial": json.loads((fila.get("historial") or "[]").strip()),
                }
        self._tokens: dict[str, str] = {}
        self._refresh: dict[str, str] = {}
        self._archivos: dict[str, dict[str, bytes]] = {"compartidos": {}}
        if ARCHIVOS_DEMO_DIR.is_dir():
            for p in sorted(ARCHIVOS_DEMO_DIR.iterdir()):
                if p.is_file():
                    self._archivos["compartidos"][p.name] = p.read_bytes()

    def archivo_guardar(self, carpeta: str, nombre: str, datos: bytes, mime: str) -> None:
        self._archivos.setdefault(carpeta, {})[nombre] = bytes(datos)

    def archivo_leer(self, carpeta: str, nombre: str):
        return self._archivos.get(carpeta, {}).get(nombre)

    def archivo_listar(self, carpeta: str) -> list[str]:
        return sorted(self._archivos.get(carpeta, {}))

    def usuarios(self, forzar: bool = False) -> dict:
        return self._usuarios

    def organizaciones(self) -> dict:
        return self._organizaciones

    def _emitir(self, codigo: str) -> dict:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self._tokens[access] = codigo
        self._refresh[refresh] = codigo
        return {
            "codigo": codigo,
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
        }

    def autenticar(self, identificador: str, password: str) -> Optional[dict]:
        ident = (identificador or "").strip().lower()
        for codigo, u in self._usuarios.items():
            if ident in (codigo.lower(), u.get("email", "").lower()):
                if u.get("activo", True) and verificar_password(
                    password, u.get("password_hash", "")
                ):
                    return self._emitir(codigo)
                return None
        return None

    def renovar_sesion(self, refresh_token: str) -> Optional[dict]:
        codigo = self._refresh.pop(refresh_token, None)
        return self._emitir(codigo) if codigo else None

    def usuario_desde_token(self, token: str) -> Optional[str]:
        codigo = self._tokens.get(token or "")
        if codigo and self._usuarios.get(codigo, {}).get("activo", True):
            return codigo
        return None

    def cerrar_sesion(self, token: str) -> None:
        self._tokens.pop(token, None)

    def tickets_todos(self) -> list[dict]:
        return [copy.deepcopy(t) for _, t in sorted(self._tickets.items())]

    def ticket(self, ticket_id: int) -> Optional[dict]:
        t = self._tickets.get(int(ticket_id))
        return copy.deepcopy(t) if t else None

    def crear_ticket(self, datos: dict, usuario: str) -> dict:
        nuevo_id = max(max(self._tickets, default=1054) + 1, 1055)
        ahora = _ahora()
        ticket = {
            "id": nuevo_id,
            "creado": ahora,
            "solicitante": usuario,
            "organizacion_id": datos["organizacion_id"],
            "area": datos["area"],
            "titulo": datos["titulo"],
            "descripcion": datos["descripcion"],
            "categoria": datos["categoria"],
            "prioridad": datos["prioridad"],
            "adjuntos": [],
            "estado": "Abierto",
            "responsable": None,
            "historial": [
                {"fecha": ahora, "usuario": usuario, "tipo": "Creacion", "detalle": "Ticket creado"}
            ],
        }
        self._tickets[nuevo_id] = ticket
        return copy.deepcopy(ticket)

    def aplicar_cambio(
        self, ticket_id: int, campos: dict, usuario: str, tipo: str, detalle: str
    ) -> dict:
        ticket = self._tickets[int(ticket_id)]
        for campo in ("estado", "prioridad", "responsable"):
            if campo in campos:
                ticket[campo] = campos[campo]
        if "adjuntos" in campos:
            ticket["adjuntos"] = list(campos["adjuntos"])
        evento = {"fecha": _ahora(), "usuario": usuario, "tipo": tipo, "detalle": detalle}
        ticket["historial"].append(evento)
        return dict(evento)
