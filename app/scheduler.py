"""Scheduler proactivo del Companion.

Despierta al Companion solo, sin que el usuario toque nada. Corre un tick cada
pocos minutos (APScheduler, dentro del mismo proceso de uvicorn) y, para cada
viaje ACTIVO, decide en HORA LOCAL DEL DESTINO si toca disparar:

- check-in matutino  (~8am)         → conversación cálida con el plan del día
- check-in nocturno  (~8pm)         → confirma/pregunta planes de mañana
- recordatorio de vuelo de regreso  (mañana del día 'fin') → push operativo

Dedup: cada evento se registra en db.proactive_log y se dispara UNA vez por día
local. Así, aunque el tick corra varias veces dentro de la ventana, no repite.

Por qué APScheduler en-proceso y no un cron externo: no expone endpoints
públicos que haya que proteger, arranca con la app y no depende de terceros.
Con una sola instancia en Railway es la opción más simple. (Si algún día se
escala a varias instancias, habría que mover el scheduler a un worker único.)

El tick es defensivo: si un viaje falla, se loguea y sigue con el resto; nunca
debe tumbar el proceso web.
"""
import traceback
from datetime import date, datetime, timedelta

from . import db, timeutil, watchers

# Ventanas de disparo en hora local (hora float). El tick chequea si la hora
# local cae dentro de la ventana; el dedup por día evita repetir.
MATUTINO = (8.0, 10.0)    # entre 8 y 10am
NOCTURNO = (20.0, 22.0)   # entre 8 y 10pm

TICK_MINUTES = 15


def _activo(trip: dict, hoy_local: date) -> bool:
    """¿El viaje está en curso hoy (hora del destino)? Tolera fechas faltantes."""
    try:
        ini = datetime.strptime(trip["inicio"], "%Y-%m-%d").date()
        fin = datetime.strptime(trip["fin"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return False
    return ini <= hoy_local <= fin


def _en_ventana(h: float, ventana: tuple[float, float]) -> bool:
    return ventana[0] <= h < ventana[1]


def _disparar(trip_id: str, kind: str, local_date: str, fn) -> None:
    """Ejecuta fn() solo si este (trip, kind) no se disparó hoy. Atómico vía UNIQUE."""
    if not db.mark_proactive_sent(trip_id, kind, local_date):
        return  # ya se envió hoy
    fn(trip_id)


def tick() -> dict:
    """Un ciclo del scheduler. Devuelve un resumen (útil para /scheduler/tick en dev)."""
    disparos = []
    for trip in db.list_trips():
        try:
            ahora = timeutil.now_local(trip)
            hoy = ahora.date()
            h = ahora.hour + ahora.minute / 60
            ld = hoy.isoformat()

            if not _activo(trip, hoy):
                # Caso especial: el día del REGRESO suele ser el último día del viaje.
                # _activo ya lo cubre (ini <= hoy <= fin), pero si el viaje terminó
                # ayer no disparamos nada. Seguimos al siguiente trip.
                continue

            tid = trip["id"]

            if _en_ventana(h, MATUTINO):
                before = len(disparos)
                _disparar(tid, "matutino", ld, lambda t: disparos.append(("matutino", t, watchers.check_in_matutino(t))))
                if len(disparos) == before:
                    pass

            if _en_ventana(h, NOCTURNO):
                _disparar(tid, "nocturno", ld, lambda t: disparos.append(("nocturno", t, watchers.check_in(t))))

            # Recordatorio de vuelo de regreso: el día 'fin' del viaje, en la mañana.
            try:
                fin = datetime.strptime(trip["fin"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                fin = None
            if fin == hoy and _en_ventana(h, MATUTINO) and trip.get("vuelo_regreso"):
                _disparar(tid, "vuelo_regreso", ld, lambda t: disparos.append(("vuelo_regreso", t, watchers.recordatorio_vuelo_regreso(t))))

        except Exception:
            traceback.print_exc()
            continue

    return {"trips": len(db.list_trips()), "disparos": [(k, t) for (k, t, _) in disparos]}


_scheduler = None


def start() -> bool:
    """Arranca APScheduler en background. Devuelve False si no está instalado
    (la app sigue corriendo igual; el scheduler es opcional para el MVP)."""
    global _scheduler
    if _scheduler is not None:
        return True
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[scheduler] APScheduler no instalado — modo proactivo desactivado. "
              "Instala 'apscheduler' para activarlo.")
        return False
    _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    _scheduler.add_job(tick, "interval", minutes=TICK_MINUTES, id="voyra_proactive",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    print(f"[scheduler] activo — tick cada {TICK_MINUTES} min")
    return True
