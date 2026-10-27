"""Feedback a cambio de más límite (etapa de pruebas): cuando un usuario llega a un tope, la app le pide un «me gusta /
no me gusta» y el porqué, y a cambio se amplía el límite sin costo.

La idea de esta etapa es aprender, no cobrar. Reglas (ajustables por variables de entorno):
- Recursos diarios (chat, maps, document, scan): una ampliación por día, que suma LIMIT_BONUS_FACTOR × el tope normal
  (por defecto ×1: el tope se duplica ese día). Máximo LIMIT_BONUS_MAX_DIAS días por usuario.
- Viajes: cada feedback desde el tope de viajes suma 1 viaje, hasta LIMIT_BONUS_TRIPS_MAX veces por usuario.
- El texto del porqué es obligatorio (mínimo MIN_MOTIVO caracteres): es lo que de verdad buscamos.
Todo queda guardado en nuestra base y se copia (si hay clave) a la tabla `feedback_limite` de Supabase.
"""
import logging
import os
import threading
from datetime import datetime, timezone

from . import db, limits, net

log = logging.getLogger("companion.feedback_limite")

RECURSOS = ("chat", "maps", "document", "scan", "trips")
MIN_MOTIVO = 8
MAX_MOTIVO = 500
MAX_POR_DIA = 5   # feedbacks guardados por usuario y día (frena el spam; solo el primero amplía)

BONUS_FACTOR = float(os.environ.get("LIMIT_BONUS_FACTOR", "1"))
BONUS_MAX_DIAS = int(os.environ.get("LIMIT_BONUS_MAX_DIAS", "14"))
BONUS_TRIPS_MAX = int(os.environ.get("LIMIT_BONUS_TRIPS_MAX", "2"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

_TABLAS = (
    "CREATE TABLE IF NOT EXISTS feedback_limite (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, trip_id TEXT, recurso TEXT NOT NULL, "
    "gusta INTEGER NOT NULL, motivo TEXT NOT NULL, day TEXT NOT NULL, created_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS limit_bonus (user_id TEXT NOT NULL, resource TEXT NOT NULL, day TEXT NOT NULL, extra INTEGER NOT NULL, "
    "PRIMARY KEY (user_id, resource, day))",
)


def asegurar_tablas() -> None:
    with db.conn() as c:
        for sql in _TABLAS:
            c.execute(sql)


def _hoy() -> str:
    return limits._today_key()


def dias_ampliados(user_id: str) -> int:
    """Cuántos días distintos ya recibió ampliación de recursos diarios."""
    asegurar_tablas()
    with db.conn() as c:
        r = c.execute("SELECT COUNT(DISTINCT day) n FROM limit_bonus WHERE user_id=? AND resource<>'trips'", (user_id,)).fetchone()
    return int(r["n"])


def viajes_ampliados(user_id: str) -> int:
    asegurar_tablas()
    with db.conn() as c:
        r = c.execute("SELECT COALESCE(SUM(extra),0) n FROM limit_bonus WHERE user_id=? AND resource='trips'", (user_id,)).fetchone()
    return int(r["n"])


def _enviar_a_supabase(fila: dict) -> None:
    try:
        r = net.request(
            "POST", f"{SUPABASE_URL}/rest/v1/feedback_limite", params={"on_conflict": "id"}, retries=1, timeout=8,
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=fila)
        if r.status_code >= 300:
            log.warning("supabase_feedback_fallo status=%s body=%s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("supabase_feedback_error error=%s", type(e).__name__)


def registrar(user_id: str, *, recurso: str, gusta: bool, motivo: str, trip_id: str | None = None) -> dict:
    """Guarda el feedback y, si corresponde, amplía el límite. Devuelve {ok, ampliado, razon}. ok=False si el feedback
    no es válido (recurso desconocido o motivo muy corto)."""
    motivo = (motivo or "").strip()[:MAX_MOTIVO]
    if recurso not in RECURSOS:
        return {"ok": False, "razon": "recurso"}
    if len(motivo) < MIN_MOTIVO:
        return {"ok": False, "razon": "motivo_corto"}
    asegurar_tablas()
    hoy = _hoy()
    with db.conn() as c:
        hechos = c.execute("SELECT COUNT(*) n FROM feedback_limite WHERE user_id=? AND day=?", (user_id, hoy)).fetchone()["n"]
    if hechos >= MAX_POR_DIA:
        return {"ok": True, "ampliado": False, "razon": "demasiados_hoy"}
    row = {"user_id": user_id, "trip_id": trip_id, "recurso": recurso, "gusta": int(bool(gusta)), "motivo": motivo, "day": hoy}
    db.insert("feedback_limite", row)

    ampliado, razon = False, ""
    if recurso == "trips":
        if viajes_ampliados(user_id) < BONUS_TRIPS_MAX:
            with db.conn() as c:
                c.execute("INSERT INTO limit_bonus (user_id, resource, day, extra) VALUES (?,?,?,?) "
                          "ON CONFLICT (user_id, resource, day) DO UPDATE SET extra = limit_bonus.extra + 1",
                          (user_id, "trips", hoy, 1))
            ampliado = True
        else:
            razon = "tope_viajes"
    else:
        with db.conn() as c:
            ya_hoy = c.execute("SELECT 1 FROM limit_bonus WHERE user_id=? AND day=? AND resource<>'trips'", (user_id, hoy)).fetchone()
        if ya_hoy:
            razon = "ya_ampliado_hoy"
        elif dias_ampliados(user_id) >= BONUS_MAX_DIAS:
            razon = "tope_dias"
        else:
            with db.conn() as c:
                for res, base in limits.FREE_LIMITS.items():
                    c.execute("INSERT INTO limit_bonus (user_id, resource, day, extra) VALUES (?,?,?,?) "
                              "ON CONFLICT (user_id, resource, day) DO NOTHING",
                              (user_id, res, hoy, max(1, round(base * BONUS_FACTOR))))
            ampliado = True

    if SUPABASE_URL and SUPABASE_KEY:
        fila_sb = {**row, "id": f"{user_id}:{datetime.now(timezone.utc).timestamp()}", "gusta": bool(gusta),
                   "ampliado": ampliado, "created_at": datetime.now(timezone.utc).isoformat()}
        threading.Thread(target=_enviar_a_supabase, args=(fila_sb,), daemon=True).start()
    return {"ok": True, "ampliado": ampliado, "razon": razon}
