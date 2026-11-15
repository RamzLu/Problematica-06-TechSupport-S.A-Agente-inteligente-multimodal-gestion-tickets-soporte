"""Historial de conversaciones del chat, por usuario.

Guarda lo que se ve en pantalla (mensajes del usuario, respuestas del agente y avisos) para que
no se pierda al recargar la pagina ni al reiniciar el servidor, y permite tener varias
conversaciones por usuario. El contexto que usa el modelo vive aparte (sesiones de ADK); aqui
solo se guarda a que sesion corresponde cada conversacion.

Todas las consultas filtran por usuario: una persona nunca ve las conversaciones de otra.
"""

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

TITULO_POR_DEFECTO = "Nueva conversación"
MAX_CONVERSACIONES_LISTADAS = 50
MAX_LARGO_TITULO = 40


class AlmacenConversaciones:
    def __init__(self, ruta: Optional[Path] = None):
        """ruta=None guarda en memoria (tests o TS_SESIONES=memoria)."""
        if ruta is not None:
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(str(ruta) if ruta is not None else ":memory:", check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        with self._lock:
            self._con.executescript(
                """
                create table if not exists conversaciones (
                    id text primary key,
                    usuario_id text not null,
                    titulo text not null,
                    session_id text not null default '',
                    creada real not null,
                    actualizada real not null
                );
                create index if not exists idx_conv_usuario on conversaciones (usuario_id, actualizada desc);
                create table if not exists mensajes (
                    id integer primary key autoincrement,
                    conversacion_id text not null references conversaciones (id) on delete cascade,
                    rol text not null,
                    texto text not null,
                    duracion_ms integer,
                    ts real not null
                );
                create index if not exists idx_msg_conv on mensajes (conversacion_id, id);
                """
            )
            self._con.commit()

    def crear(self, usuario_id: str, titulo: str = TITULO_POR_DEFECTO) -> dict:
        ahora = time.time()
        conv = {"id": uuid.uuid4().hex[:12], "usuario_id": usuario_id, "titulo": titulo,
                "session_id": "", "creada": ahora, "actualizada": ahora}
        with self._lock:
            self._con.execute(
                "insert into conversaciones (id, usuario_id, titulo, session_id, creada, actualizada) "
                "values (:id, :usuario_id, :titulo, :session_id, :creada, :actualizada)",
                conv,
            )
            self._con.commit()
        return conv

    def obtener(self, usuario_id: str, conv_id: str) -> Optional[dict]:
        with self._lock:
            fila = self._con.execute(
                "select * from conversaciones where id = ? and usuario_id = ?", (conv_id, usuario_id)
            ).fetchone()
        return dict(fila) if fila else None

    def listar(self, usuario_id: str) -> list:
        with self._lock:
            filas = self._con.execute(
                "select c.id, c.titulo, c.actualizada, "
                "(select count(*) from mensajes m where m.conversacion_id = c.id) as mensajes "
                "from conversaciones c where c.usuario_id = ? and exists "
                "(select 1 from mensajes m where m.conversacion_id = c.id) "
                "order by c.actualizada desc limit ?",
                (usuario_id, MAX_CONVERSACIONES_LISTADAS),
            ).fetchall()
        return [dict(f) for f in filas]

    def mensajes(self, conv_id: str) -> list:
        with self._lock:
            filas = self._con.execute(
                "select rol, texto, duracion_ms, ts from mensajes where conversacion_id = ? order by id",
                (conv_id,),
            ).fetchall()
        return [dict(f) for f in filas]

    def agregar_mensaje(self, conv_id: str, rol: str, texto: str, duracion_ms: Optional[int] = None) -> None:
        ahora = time.time()
        with self._lock:
            self._con.execute(
                "insert into mensajes (conversacion_id, rol, texto, duracion_ms, ts) values (?, ?, ?, ?, ?)",
                (conv_id, rol, texto, duracion_ms, ahora),
            )
            self._con.execute("update conversaciones set actualizada = ? where id = ?", (ahora, conv_id))
            self._con.commit()

    def fijar_titulo(self, conv_id: str, titulo: str) -> None:
        titulo = " ".join(str(titulo).split())[:MAX_LARGO_TITULO] or TITULO_POR_DEFECTO
        with self._lock:
            self._con.execute("update conversaciones set titulo = ? where id = ?", (titulo, conv_id))
            self._con.commit()

    def fijar_session(self, conv_id: str, session_id: str) -> None:
        with self._lock:
            self._con.execute("update conversaciones set session_id = ? where id = ?", (session_id, conv_id))
            self._con.commit()

    def eliminar(self, usuario_id: str, conv_id: str) -> Optional[dict]:
        """Borra la conversacion y sus mensajes. Devuelve la conversacion borrada, o None si no es del usuario."""
        conv = self.obtener(usuario_id, conv_id)
        if conv is None:
            return None
        with self._lock:
            self._con.execute("delete from mensajes where conversacion_id = ?", (conv_id,))
            self._con.execute("delete from conversaciones where id = ? and usuario_id = ?", (conv_id, usuario_id))
            self._con.commit()
        return conv

    def eliminar_si_vacia(self, conv_id: str) -> None:
        with self._lock:
            self._con.execute(
                "delete from conversaciones where id = ? and not exists "
                "(select 1 from mensajes where conversacion_id = ?)",
                (conv_id, conv_id),
            )
            self._con.commit()
