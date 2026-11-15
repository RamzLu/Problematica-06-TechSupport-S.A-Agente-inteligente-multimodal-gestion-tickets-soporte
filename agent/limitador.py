"""Limitador de intentos en memoria (ventana deslizante).

Sirve para frenar pruebas masivas de contrasenas y el abuso del chat. Vive en el proceso del
servidor: si se reinicia, los contadores empiezan de cero. Con varios procesos o servidores
haria falta un almacen compartido (por ejemplo Redis).
"""

import threading
import time
from collections import deque


class Limitador:
    def __init__(self, max_claves: int = 5000) -> None:
        self._lock = threading.Lock()
        self._eventos: dict[str, deque] = {}
        self._max_claves = max_claves

    def registrar(self, clave: str) -> None:
        """Anota un intento para `clave`."""
        ahora = time.monotonic()
        with self._lock:
            if len(self._eventos) >= self._max_claves:
                self._eventos.clear()
            self._eventos.setdefault(clave, deque()).append(ahora)

    def bloqueado(self, clave: str, maximo: int, ventana_seg: float) -> int:
        """Devuelve 0 si `clave` puede seguir, o los segundos que debe esperar."""
        ahora = time.monotonic()
        with self._lock:
            eventos = self._eventos.get(clave)
            if not eventos:
                return 0
            while eventos and ahora - eventos[0] > ventana_seg:
                eventos.popleft()
            if not eventos:
                self._eventos.pop(clave, None)
                return 0
            if len(eventos) < maximo:
                return 0
            return max(1, int(ventana_seg - (ahora - eventos[0])) + 1)

    def olvidar(self, clave: str) -> None:
        with self._lock:
            self._eventos.pop(clave, None)

    def reiniciar(self) -> None:
        with self._lock:
            self._eventos.clear()


limitador = Limitador()
