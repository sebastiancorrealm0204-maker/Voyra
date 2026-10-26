"""Utilidades de red compartidas: cliente HTTP con pool, reintentos, caché TTL
y circuit breaker.

Por qué existe:
- Antes cada llamada externa hacía `httpx.post(...)` suelto: abría una conexión
  TCP + TLS nueva cada vez (100-300 ms extra por llamada a Groq/DeepSeek/Google).
  Un `httpx.Client` compartido reutiliza conexiones (keep-alive) y es seguro de
  usar desde varios threads (los endpoints sync de FastAPI corren en threadpool).
- Reintentos con backoff exponencial + jitter en UN solo lugar, en vez de
  reimplementarlos (o no tenerlos) en cada módulo.
- TTLCache: memoiza lookups externos repetidos (Tavily, Google Places) sin
  dependencias nuevas.
- CircuitBreaker: si un proveedor está caído, dejamos de esperarle timeouts
  durante un rato y saltamos directo al respaldo / fallback.

Todo es stdlib + httpx (que ya estaba en requirements).
"""
import logging
import os
import random
import threading
import time
from collections import OrderedDict

import httpx

log = logging.getLogger("companion.net")

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
_LIMITS = httpx.Limits(
    max_connections=int(os.environ.get("HTTP_MAX_CONNECTIONS", "50")),
    max_keepalive_connections=int(os.environ.get("HTTP_MAX_KEEPALIVE", "20")),
    keepalive_expiry=30.0,
)

_client: httpx.Client | None = None
_client_lock = threading.Lock()


def client() -> httpx.Client:
    """Cliente HTTP compartido del proceso (perezoso, thread-safe)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
    return _client


def close() -> None:
    """Cierra el cliente compartido (shutdown de la app)."""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            finally:
                _client = None


RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})


def backoff(intento: int, base: float = 0.5, tope: float = 6.0) -> float:
    """Backoff exponencial con jitter (evita que N reintentos peguen a la vez)."""
    return min(tope, base * (2 ** intento)) * (0.75 + random.random() * 0.5)


def request(method: str, url: str, *, retries: int = 1, timeout=None,
            retry_statuses=RETRY_STATUSES, deadline_s: float | None = None,
            **kw) -> httpx.Response:
    """Request con reintentos ante errores transitorios (red, timeout, 5xx, 429).

    - Devuelve la última respuesta aunque sea un status de error (el caller
      decide qué hacer con un 4xx); solo LANZA si la última falla fue de red.
    - `deadline_s`: tope total de tiempo; no se programa un reintento que
      excedería ese tope (nunca congelamos al usuario indefinidamente).
    """
    inicio = time.monotonic()
    ultima_exc: Exception | None = None
    resp: httpx.Response | None = None
    for intento in range(retries + 1):
        try:
            resp = client().request(method, url, timeout=timeout or _DEFAULT_TIMEOUT, **kw)
            if resp.status_code not in retry_statuses or intento >= retries:
                return resp
            espera = backoff(intento)
            ra = resp.headers.get("retry-after")
            if ra:
                try:
                    espera = min(float(ra), 6.0)
                except ValueError:
                    pass
        except (httpx.TimeoutException, httpx.TransportError) as e:
            ultima_exc = e
            resp = None
            if intento >= retries:
                raise
            espera = backoff(intento)
        if deadline_s is not None and (time.monotonic() - inicio + espera) > deadline_s:
            break
        time.sleep(espera)
    if resp is not None:
        return resp
    if ultima_exc:
        raise ultima_exc
    raise RuntimeError("request sin respuesta")  # inalcanzable


class TTLCache:
    """Caché en memoria con expiración y tamaño máximo (LRU). Thread-safe.

    Es por proceso: con varios workers cada uno tiene la suya, lo cual está bien
    para lo que se cachea aquí (datos externos que toleran minutos de frescura).
    """

    _MISS = object()

    def __init__(self, ttl_s: float, maxsize: int = 512):
        self.ttl = ttl_s
        self.maxsize = maxsize
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            item = self._data.get(key, self._MISS)
            if item is self._MISS:
                return default
            exp, val = item
            if exp < time.monotonic():
                self._data.pop(key, None)
                return default
            self._data.move_to_end(key)
            return val

    def set(self, key, val, ttl_s: float | None = None):
        with self._lock:
            self._data[key] = (time.monotonic() + (ttl_s if ttl_s is not None else self.ttl), val)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def pop(self, key):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        with self._lock:
            return len(self._data)


class CircuitBreaker:
    """Circuit breaker mínimo por clave (p. ej. por proveedor LLM).

    Tras `umbral` fallas consecutivas, la clave queda "abierta" `enfriamiento_s`
    segundos: `permitido()` devuelve False y el caller salta al respaldo sin
    esperar otro timeout. Un éxito la cierra de nuevo.
    """

    def __init__(self, umbral: int = 3, enfriamiento_s: float = 30.0):
        self.umbral = umbral
        self.enfriamiento = enfriamiento_s
        self._fallas: dict[str, int] = {}
        self._abierto_hasta: dict[str, float] = {}
        self._lock = threading.Lock()

    def permitido(self, key: str) -> bool:
        with self._lock:
            return self._abierto_hasta.get(key, 0.0) <= time.monotonic()

    def exito(self, key: str) -> None:
        with self._lock:
            self._fallas.pop(key, None)
            self._abierto_hasta.pop(key, None)

    def falla(self, key: str) -> None:
        with self._lock:
            n = self._fallas.get(key, 0) + 1
            self._fallas[key] = n
            if n >= self.umbral:
                self._abierto_hasta[key] = time.monotonic() + self.enfriamiento
                log.warning("circuit_open key=%s fallas=%d enfriamiento_s=%.0f",
                            key, n, self.enfriamiento)

    def estado(self) -> dict:
        ahora = time.monotonic()
        with self._lock:
            return {k: ("open" if v > ahora else "closed")
                    for k, v in self._abierto_hasta.items()}

    def reset(self) -> None:
        with self._lock:
            self._fallas.clear()
            self._abierto_hasta.clear()
