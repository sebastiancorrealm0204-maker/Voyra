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
    "maps":     _env_int("LIMIT_MAPS_PER_DAY", 6),     # búsquedas Google Places en vivo
    "chat":     _env_int("LIMIT_CHAT_PER_DAY", 20),    # mensajes al LLM
    "document": _env_int("LIMIT_DOCS_PER_DAY", 3),     # documentos extraídos (visión = caro)
    "scan":     _env_int("LIMIT_SCAN_PER_DAY", 1),      # scans Tavily
}

# Tope TOTAL por cuenta durante la etapa de pruebas (todos los días juntos). Es lo que acota el costo por usuario:
# el feedback amplía el tope DIARIO, pero nunca pasa de este total. Más adelante, aquí entra el plan de pago.
TOTAL_LIMITS = {
    "chat":     _env_int("LIMIT_CHAT_TOTAL", 200),
    "maps":     _env_int("LIMIT_MAPS_TOTAL", 50),
    "document": _env_int("LIMIT_DOCS_TOTAL", 10),
    "scan":     _env_int("LIMIT_SCAN_TOTAL", 8),
}

# Mapea cada recurso a su columna de override por-usuario en la tabla `users`.
_LIMIT_COLUMN = {
    "maps": "limit_maps", "chat": "limit_chat",
    "document": "limit_document", "scan": "limit_scan",
}


def bonus(user_id: str, resource: str, day: str | None = None) -> int:
    """Ampliación ganada dando feedback (ver feedback_limite.py): del día para los recursos diarios, en total para los viajes."""
    try:
        with db.conn() as c:
            if resource == "trips":
                r = c.execute("SELECT COALESCE(SUM(extra),0) v FROM limit_bonus WHERE user_id=? AND resource='trips'", (user_id,)).fetchone()
            else:
                r = c.execute("SELECT COALESCE(SUM(extra),0) v FROM limit_bonus WHERE user_id=? AND resource=? AND day=?",
                              (user_id, resource, day or _today_key())).fetchone()
        return int(r["v"] or 0)
    except Exception:
        return 0   # sin tabla todavía (nadie ha dado feedback): sin ampliación


def effective_limit(user_id: str, resource: str) -> int | None:
    """Límite que aplica a ESTE usuario para ESTE recurso.

    Prioridad: el número en su columna de `users` (editable desde Supabase) y,
    si está en NULL, el default del plan (env var FREE_LIMITS). Devuelve None si
    el recurso no está controlado.

    Esto es lo que hace que ajustar un límite en el Table Editor de Supabase
    aplique al instante: cada chequeo de cuota lee el valor vigente del usuario.
    """
    default = FREE_LIMITS.get(resource)
    col = _LIMIT_COLUMN.get(resource)
    if not col:
        return default
    with db.conn() as c:
        r = c.execute(f"SELECT {col} v FROM users WHERE id=?", (user_id,)).fetchone()
    base = int(r["v"]) if r and r["v"] is not None else default
    return base + bonus(user_id, resource) if base is not None else None


def effective_trip_limit(user_id: str) -> int:
    """Tope de viajes creados en total para este usuario (override por-usuario o default)."""
    with db.conn() as c:
        r = c.execute("SELECT limit_trips v FROM users WHERE id=?", (user_id,)).fetchone()
    base = int(r["v"]) if r and r["v"] is not None else MAX_TRIPS_PER_USER
    return base + bonus(user_id, "trips")

# Tope de viajes CREADOS por usuario en el plan gratuito (total, no diario; borrar un viaje no devuelve el cupo).
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
        c.executescript(db._schema_for_postgres(SCHEMA) if db.IS_POSTGRES else SCHEMA)


# ── Conteo ──
def used_total(user_id: str, resource: str) -> int:
    with db.conn() as c:
        r = c.execute("SELECT COUNT(*) n FROM usage_log WHERE user_id=? AND resource=?", (user_id, resource)).fetchone()
    return r["n"]


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
    def __init__(self, resource: str, limit: int, used: int, global_block: bool = False, total_block: bool = False):
        self.resource = resource
        self.limit = limit
        self.used = used
        self.global_block = global_block
        self.total_block = total_block   # llegó al tope TOTAL de la etapa de pruebas (el feedback no lo amplía)
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

    limit = effective_limit(user_id, resource)
    if limit is None:
        # Recurso no controlado: no bloquear, pero igual registrar para métricas.
        record_use(user_id, resource)
        return

    total = TOTAL_LIMITS.get(resource)
    if total is not None:
        gastado = used_total(user_id, resource)
        if gastado >= total:
            raise QuotaError(resource, total, gastado, total_block=True)
    used = used_today(user_id, resource)
    if used >= limit:
        raise QuotaError(resource, limit, used)
    record_use(user_id, resource)


def remaining(user_id: str, resource: str) -> int:
    limit = effective_limit(user_id, resource)
    if limit is None:
        return -1  # ilimitado / no controlado
    return max(0, limit - used_today(user_id, resource))


def usage_summary(user_id: str) -> dict:
    """Resumen para mostrarle al usuario cuánto le queda hoy.
    Usa el límite EFECTIVO de cada usuario (override por-usuario o default)."""
    return {
        res: {
            "usado": used_today(user_id, res),
            "limite": effective_limit(user_id, res),
            "restante": remaining(user_id, res),
            "total_usado": used_total(user_id, res),
            "total_limite": TOTAL_LIMITS.get(res),
        }
        for res in FREE_LIMITS
    }


# ── Trips activos por usuario ──
def trips_count(user_id: str) -> int:
    """Viajes que ya gastaron cupo: los que existen hoy o, si es mayor, los que alguna vez creó (borrar un viaje
    no devuelve el cupo; si no, bastaría borrar y volver a crear el mismo viaje para usarlo sin pagar)."""
    with db.conn() as c:
        r = c.execute("SELECT COUNT(*) n FROM trips WHERE user_id=?", (user_id,)).fetchone()
        u = c.execute("SELECT trips_creados v FROM users WHERE id=?", (user_id,)).fetchone()
    return max(int(r["n"]), int(u["v"] or 0) if u else 0)


def registrar_viaje_creado(user_id: str, antes: int) -> None:
    """Deja anotado que el usuario ya creó `antes + 1` viajes en total."""
    with db.conn() as c:
        c.execute("UPDATE users SET trips_creados=? WHERE id=?", (antes + 1, user_id))


def can_create_trip(user_id: str) -> bool:
    return trips_count(user_id) < effective_trip_limit(user_id)
