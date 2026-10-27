"""Configuración ajustable desde el panel de administración, sin tocar Railway ni redesplegar.

Cada clave guarda un valor en la tabla `app_config` (queda también en Supabase). Si una clave no tiene valor guardado, manda
el default del código / de la variable de entorno. Se lee con un caché corto, así que un cambio aplica en segundos.
"""
import time

from . import db

_TTL_S = 5.0
_cache: dict = {"t": 0.0, "datos": {}}
_lista: set[str] = set()


def _tabla() -> None:
    clave = "pg" if db.IS_POSTGRES else db.DB_PATH
    if clave in _lista:
        return
    with db.conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)")
    _lista.add(clave)


def _todo() -> dict:
    ahora = time.monotonic()
    if ahora - _cache["t"] < _TTL_S and _cache.get("db") == (db.DB_PATH, db.IS_POSTGRES):
        return _cache["datos"]
    datos: dict = {}
    try:
        _tabla()
        with db.conn() as c:
            datos = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM app_config").fetchall()}
    except Exception:
        datos = {}
    _cache.update(t=ahora, datos=datos, db=(db.DB_PATH, db.IS_POSTGRES))
    return datos


def get(key: str, default=None):
    return _todo().get(key, default)


def get_int(key: str, default: int | None) -> int | None:
    v = _todo().get(key)
    try:
        return int(float(v)) if v is not None else default
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    v = _todo().get(key)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def set_(key: str, value: str | None) -> None:
    """Guarda un valor; None borra el ajuste (vuelve al default)."""
    _tabla()
    with db.conn() as c:
        c.execute("DELETE FROM app_config WHERE key=?", (key,))
        if value is not None:
            c.execute("INSERT INTO app_config (key, value, updated_at) VALUES (?,?,?)", (key, str(value), time.time()))
    _cache["t"] = 0.0   # el cambio aplica de inmediato en este proceso
