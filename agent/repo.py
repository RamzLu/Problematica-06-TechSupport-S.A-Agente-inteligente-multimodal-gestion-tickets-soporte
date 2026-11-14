import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

TTL_USUARIOS_SEG = 30
TTL_TOKEN_SEG = 30
BUCKET_ARCHIVOS = "ticket-archivos"
CARPETA_COMPARTIDA = "compartidos"


class RepoError(Exception):
    """Fallo de infraestructura (red, Supabase caido, configuracion)."""


def _zona_horaria():
    nombre = os.getenv("TS_TIMEZONE", "America/Argentina/Buenos_Aires")
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(nombre)
    except Exception:
        return timezone(timedelta(hours=-3))


_TZ = _zona_horaria()


def formatear_fecha(valor) -> str:
    """Devuelve 'YYYY-MM-DD HH:MM' en la zona horaria de la organizacion."""
    if not valor:
        return ""
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return valor
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(_TZ).strftime("%Y-%m-%d %H:%M")


class RepoSupabase:
    def __init__(self, url: str, service_key: str, publishable_key: str):
        from supabase import create_client

        self._crear_cliente = create_client
        self._url = url
        self._publishable = publishable_key
        self.db = create_client(url, service_key)
        self._verificador = create_client(url, publishable_key)
        self._lock = threading.Lock()
        self._cache_usuarios: Optional[dict] = None
        self._cache_usuarios_ts = 0.0
        self._cache_tokens: dict[str, tuple[str, float]] = {}

    @staticmethod
    def _usuario(fila: dict) -> dict:
        return {
            "auth_id": str(fila["id"]),
            "nombre": fila["nombre"],
            "rol": fila["rol"],
            "email": fila.get("email", ""),
            "organizacion_id": fila.get("organizacion_id", "org_central"),
            "area": fila.get("area", "General"),
            "cargo": fila.get("cargo", ""),
            "activo": bool(fila.get("activo", True)),
        }

    def usuarios(self, forzar: bool = False) -> dict:
        ahora = time.monotonic()
        with self._lock:
            vencido = ahora - self._cache_usuarios_ts > TTL_USUARIOS_SEG
            if forzar or self._cache_usuarios is None or vencido:
                try:
                    filas = self.db.table("profiles").select("*").execute().data
                except Exception as exc:
                    raise RepoError(f"No se pudo leer el directorio de usuarios: {exc}") from exc
                self._cache_usuarios = {f["codigo"]: self._usuario(f) for f in filas}
                self._cache_usuarios_ts = ahora
            return self._cache_usuarios

    def organizaciones(self) -> dict:
        try:
            filas = self.db.table("organizaciones").select("*").execute().data
        except Exception as exc:
            raise RepoError(f"No se pudieron leer las organizaciones: {exc}") from exc
        return {f["id"]: f for f in filas}

    def _perfil_por_identificador(self, ident: str) -> Optional[dict]:
        columna = "email" if "@" in ident else "codigo"
        try:
            filas = (
                self.db.table("profiles").select("*").eq(columna, ident).limit(1).execute().data
            )
        except Exception as exc:
            raise RepoError(f"No se pudo consultar el perfil: {exc}") from exc
        return filas[0] if filas else None

    @staticmethod
    def _es_error_de_red(exc: Exception) -> bool:
        """Red caida, timeout o Supabase inaccesible (no es culpa del usuario)."""
        if type(exc).__name__ == "AuthRetryableError" or getattr(exc, "status", None) == 0:
            return True
        return isinstance(exc, (OSError, TimeoutError)) or type(exc).__module__.startswith("httpx")

    @classmethod
    def _es_credencial_invalida(cls, exc: Exception) -> bool:
        """Credencial o token rechazado por Supabase Auth."""
        if cls._es_error_de_red(exc):
            return False
        status = getattr(exc, "status", None)
        return status in (400, 401, 403, 422) or type(exc).__name__.startswith("Auth")

    def autenticar(self, identificador: str, password: str) -> Optional[dict]:
        """Inicia sesion con Supabase Auth. Devuelve tokens + codigo, o None si es invalido."""
        ident = (identificador or "").strip().lower()
        perfil = self._perfil_por_identificador(ident)
        if not perfil or not perfil.get("activo", True):
            return None
        cliente = self._crear_cliente(self._url, self._publishable)
        try:
            res = cliente.auth.sign_in_with_password(
                {"email": perfil["email"], "password": password}
            )
        except Exception as exc:
            if self._es_credencial_invalida(exc):
                return None
            raise RepoError(f"No se pudo contactar a Supabase Auth: {exc}") from exc
        sesion = getattr(res, "session", None)
        if not sesion:
            return None
        self.usuarios(forzar=True)
        return {
            "codigo": perfil["codigo"],
            "access_token": sesion.access_token,
            "refresh_token": sesion.refresh_token,
            "expires_in": sesion.expires_in,
        }

    def renovar_sesion(self, refresh_token: str) -> Optional[dict]:
        cliente = self._crear_cliente(self._url, self._publishable)
        try:
            res = cliente.auth.refresh_session(refresh_token)
        except Exception as exc:
            if self._es_credencial_invalida(exc):
                return None
            raise RepoError(f"No se pudo renovar la sesion: {exc}") from exc
        sesion = getattr(res, "session", None)
        if not sesion:
            return None
        codigo = self._codigo_por_auth_id(str(res.user.id))
        if not codigo:
            return None
        return {
            "codigo": codigo,
            "access_token": sesion.access_token,
            "refresh_token": sesion.refresh_token,
            "expires_in": sesion.expires_in,
        }

    def _codigo_por_auth_id(self, auth_id: str) -> Optional[str]:
        for intento in (False, True):
            for codigo, u in self.usuarios(forzar=intento).items():
                if u["auth_id"] == auth_id:
                    return codigo if u["activo"] else None
        return None

    def usuario_desde_token(self, token: str) -> Optional[str]:
        """Valida el JWT contra Supabase Auth y devuelve el codigo del usuario (o None)."""
        if not token:
            return None
        ahora = time.monotonic()
        en_cache = self._cache_tokens.get(token)
        if en_cache and en_cache[1] > ahora:
            return en_cache[0]
        try:
            res = self._verificador.auth.get_user(token)
        except Exception as exc:
            if self._es_credencial_invalida(exc):
                self._cache_tokens.pop(token, None)
                return None
            raise RepoError(f"No se pudo verificar la sesion: {exc}") from exc
        usuario = getattr(res, "user", None) if res else None
        if not usuario:
            return None
        codigo = self._codigo_por_auth_id(str(usuario.id))
        if not codigo:
            return None
        if len(self._cache_tokens) > 500:
            self._cache_tokens.clear()
        self._cache_tokens[token] = (codigo, ahora + TTL_TOKEN_SEG)
        return codigo

    def cerrar_sesion(self, token: str) -> None:
        self._cache_tokens.pop(token, None)
        try:
            self.db.auth.admin.sign_out(token, "local")
        except Exception:
            pass

    @staticmethod
    def _evento(fila: dict) -> dict:
        return {
            "fecha": formatear_fecha(fila["fecha"]),
            "usuario": fila["usuario"],
            "tipo": fila["tipo"],
            "detalle": fila["detalle"],
        }

    def _ticket(self, fila: dict) -> dict:
        historial = sorted(fila.get("ticket_historial") or [], key=lambda e: e["id"])
        return {
            "id": int(fila["id"]),
            "creado": formatear_fecha(fila["creado"]),
            "solicitante": fila["solicitante"],
            "organizacion_id": fila["organizacion_id"],
            "area": fila["area"],
            "titulo": fila["titulo"],
            "descripcion": fila["descripcion"],
            "categoria": fila["categoria"],
            "prioridad": fila["prioridad"],
            "adjuntos": list(fila.get("adjuntos") or []),
            "estado": fila["estado"],
            "responsable": fila.get("responsable"),
            "historial": [self._evento(e) for e in historial],
        }

    def tickets_todos(self) -> list[dict]:
        try:
            filas = (
                self.db.table("tickets").select("*, ticket_historial(*)").order("id").execute().data
            )
        except Exception as exc:
            raise RepoError(f"No se pudieron leer los tickets: {exc}") from exc
        return [self._ticket(f) for f in filas]

    def ticket(self, ticket_id: int) -> Optional[dict]:
        try:
            filas = (
                self.db.table("tickets")
                .select("*, ticket_historial(*)")
                .eq("id", int(ticket_id))
                .limit(1)
                .execute()
                .data
            )
        except Exception as exc:
            raise RepoError(f"No se pudo leer el ticket: {exc}") from exc
        return self._ticket(filas[0]) if filas else None

    def crear_ticket(self, datos: dict, usuario: str) -> dict:
        try:
            fila = self.db.rpc(
                "crear_ticket_con_historial", {"p_datos": datos, "p_usuario": usuario}
            ).execute().data
        except Exception as exc:
            raise RepoError(f"No se pudo crear el ticket: {exc}") from exc
        creado = self.ticket(fila["id"])
        if creado is None:
            raise RepoError("El ticket se creo pero no se pudo leer de vuelta.")
        return creado

    def aplicar_cambio(
        self, ticket_id: int, campos: dict, usuario: str, tipo: str, detalle: str
    ) -> dict:
        """Aplica el cambio al ticket y registra el evento en una sola transaccion."""
        try:
            fila = self.db.rpc(
                "aplicar_cambio_ticket",
                {
                    "p_ticket_id": int(ticket_id),
                    "p_campos": campos,
                    "p_usuario": usuario,
                    "p_tipo": tipo,
                    "p_detalle": detalle,
                },
            ).execute().data
        except Exception as exc:
            raise RepoError(f"No se pudo guardar el cambio: {exc}") from exc
        return self._evento(fila)


    @staticmethod
    def _no_existe(exc: Exception) -> bool:
        texto = str(exc).lower()
        return "not found" in texto or "404" in texto or "object not found" in texto

    def archivo_guardar(self, carpeta: str, nombre: str, datos: bytes, mime: str) -> None:
        try:
            self.db.storage.from_(BUCKET_ARCHIVOS).upload(
                f"{carpeta}/{nombre}",
                datos,
                {"content-type": mime or "application/octet-stream", "upsert": "true"},
            )
        except Exception as exc:
            raise RepoError(f"No se pudo guardar el archivo: {exc}") from exc

    def archivo_leer(self, carpeta: str, nombre: str) -> Optional[bytes]:
        try:
            return self.db.storage.from_(BUCKET_ARCHIVOS).download(f"{carpeta}/{nombre}")
        except Exception as exc:
            if self._no_existe(exc):
                return None
            raise RepoError(f"No se pudo leer el archivo: {exc}") from exc

    def archivo_listar(self, carpeta: str) -> list[str]:
        try:
            filas = self.db.storage.from_(BUCKET_ARCHIVOS).list(carpeta, {"limit": 1000})
        except Exception as exc:
            raise RepoError(f"No se pudieron listar los archivos: {exc}") from exc
        return sorted(
            f["name"] for f in (filas or []) if f.get("name") and not f["name"].startswith(".")
        )


_repo = None
_lock_repo = threading.Lock()


def _construir():
    modo = os.getenv("TS_BACKEND", "").strip().lower()
    if modo == "memoria":
        from .repo_memoria import RepoMemoria

        return RepoMemoria()

    try:
        from dotenv import load_dotenv

        load_dotenv(RAIZ_PROYECTO / ".env")
    except ImportError:
        pass

    url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    publishable = (
        os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
    ).strip()
    faltan = [
        n
        for n, v in (
            ("SUPABASE_URL", url),
            ("SUPABASE_SERVICE_ROLE_KEY", service_key),
            ("SUPABASE_PUBLISHABLE_KEY", publishable),
        )
        if not v
    ]
    if faltan:
        raise RuntimeError(
            "Falta configurar Supabase en el archivo .env: "
            + ", ".join(faltan)
            + ". Copie .env.example a .env y complete los valores."
        )
    return RepoSupabase(url, service_key, publishable)


def get_repo():
    global _repo
    if _repo is None:
        with _lock_repo:
            if _repo is None:
                _repo = _construir()
    return _repo


def usar_repo(repo) -> None:
    """Reemplaza el repositorio activo (lo usan los tests)."""
    global _repo
    _repo = repo
