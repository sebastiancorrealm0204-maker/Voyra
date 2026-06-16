"""Cuotas por usuario + circuit breaker global — el control de gasto.

Dos defensas independientes:

1) CUOTA POR USUARIO/DÍA: cada usuario tiene topes diarios por recurso. El día
   se cuenta en hora de Colombia (America/Bogota) y se resetea solo (la cuota se
   calcula contando el uso de hoy, no con un cron). Esto evita que UN usuario
   queme la API.

2) CIRCUIT BREAKER GLOBAL DE MAPS: aunque las cuotas por usuario estén bien, si
   muchas cuentas (legítimas o no) pegan a Google Maps el mismo día, hay un tope
   AGREGADO. Pasado ese tope, la búsqueda en vivo se apaga para todos hasta el
   reset. Es el seguro contra el cobro sorpresa: pase lo que pase con las
   cuentas, Google nunca te cobra más de N búsquedas/día.

Implementación: una tabla `usage_log` (user_id, resource, day, ...). Contar es
una query barata. Sin tablas nuevas raras ni Redis: SQLite alcanza de sobra para
el MVP.

Para subir/bajar topes no hay que tocar código: son variables de entorno.
"""
import os
from datetime import datetime, timezone, timedelta

from . import db

# Colombia = UTC-5 fijo (no tiene horario de verano). Simple y correcto.
_BOGOTA = timezone(timedelta(hours=-5))


def _today_key() -> str:
    """'YYYY-MM-DD' en hora de Bogotá. El día de la cuota arranca a medianoche CO."""
    return datetime.now(_BOGOTA).strftime("%Y-%m-%d")


# ── Recursos controlados y su tope diario (free tier) ──
# Editable por variable de entorno sin tocar código.
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


FREE_LIMITS = {
    "maps":     _env_int("LIMIT_MAPS_PER_DAY", 15),    # búsquedas Google Places en vivo
    "chat":     _env_int("LIMIT_CHAT_PER_DAY", 40),    # mensajes al LLM
    "document": _env_int("LIMIT_DOCS_PER_DAY", 10),    # documentos extraídos (visión = caro)
    "scan":     _env_int("LIMIT_SCAN_PER_DAY", 5),     # scans Tavily
}

# Tope de trips ACTIVOS simultáneos por usuario (no es diario; es un total vivo).
MAX_TRIPS_PER_USER = _env_int("LIMIT_TRIPS_PER_USER", 2)

# Circuit breaker global de Maps: tope AGREGADO de todo el sistema por día.
GLOBAL_MAPS_PER_DAY = _env_int("GLOBAL_MAPS_PER_DAY", 500)

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  resource TEXT NOT NULL,        -- maps | chat | document | scan
  day TEXT NOT NULL,             -- YYYY-MM-DD en hora Bogotá
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user_day ON usage_log(user_id, resource, day);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_log(resource, day);
"""


def init_limits():
    with db.conn() as c:
        c.executescript(SCHEMA)


# ── Conteo ──
def used_today(user_id: str, resource: str) -> int:
    with db.conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n FROM usage_log WHERE user_id=? AND resource=? AND day=?",
            (user_id, resource, _today_key()),
        ).fetchone()
    return r["n"]


def global_used_today(resource: str) -> int:
    with db.conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n FROM usage_log WHERE resource=? AND day=?",
            (resource, _today_key()),
        ).fetchone()
    return r["n"]


def record_use(user_id: str, resource: str):
    db.insert("usage_log", {"user_id": user_id, "resource": resource, "day": _today_key()})


# ── Chequeos ──
class QuotaError(Exception):
    """Se lanza cuando un usuario excede su cuota. Lleva detalle para el front."""
    def __init__(self, resource: str, limit: int, used: int, global_block: bool = False):
        self.resource = resource
        self.limit = limit
        self.used = used
        self.global_block = global_block
        super().__init__(f"quota:{resource}")


def check_and_consume(user_id: str, resource: str):
    """Verifica la cuota del usuario (y el circuit breaker global si aplica) y,
    si hay margen, registra el uso. Lanza QuotaError si no hay margen.

    Llamar JUSTO ANTES de hacer la llamada a la API externa. Así solo se cuenta
    lo que de verdad se va a gastar.
    """
    # Circuit breaker global: solo aplica a Maps (el recurso que se paga por uso).
    if resource == "maps":
        if global_used_today("maps") >= GLOBAL_MAPS_PER_DAY:
            raise QuotaError("maps", GLOBAL_MAPS_PER_DAY, global_used_today("maps"),
                             global_block=True)

    limit = FREE_LIMITS.get(resource)
    if limit is None:
        # Recurso no controlado: no bloquear, pero igual registrar para métricas.
        record_use(user_id, resource)
        return

    used = used_today(user_id, resource)
    if used >= limit:
        raise QuotaError(resource, limit, used)
    record_use(user_id, resource)


def remaining(user_id: str, resource: str) -> int:
    limit = FREE_LIMITS.get(resource)
    if limit is None:
        return -1  # ilimitado / no controlado
    return max(0, limit - used_today(user_id, resource))


def usage_summary(user_id: str) -> dict:
    """Resumen para mostrarle al usuario cuánto le queda hoy."""
    return {
        res: {
            "usado": used_today(user_id, res),
            "limite": limit,
            "restante": remaining(user_id, res),
        }
        for res, limit in FREE_LIMITS.items()
    }


# ── Trips activos por usuario ──
def trips_count(user_id: str) -> int:
    with db.conn() as c:
        r = c.execute("SELECT COUNT(*) n FROM trips WHERE user_id=?", (user_id,)).fetchone()
    return r["n"]


def can_create_trip(user_id: str) -> bool:
    return trips_count(user_id) < MAX_TRIPS_PER_USER
