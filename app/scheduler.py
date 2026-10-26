"""Scheduler proactivo del Companion.

Despierta al Companion solo, sin que el usuario toque nada. Corre un tick cada
pocos minutos (APScheduler, dentro del mismo proceso de uvicorn) y, para cada
viaje ACTIVO, decide en HORA LOCAL DEL DESTINO si toca disparar:

- check-in matutino  (~8am)         → conversación cálida con el plan del día
- check-in nocturno  (~8pm)         → confirma/pregunta planes de mañana
- recordatorio de vuelo de regreso  (mañana del día 'fin') → push operativo
- aviso_hora_de_salir (1h y 15min antes de cada plan con hora) → push operativo
- recomendación por gustos (mediodía, tarde y noche) → un lugar curado que calza con sus gustos

Dedup: cada evento se registra en db.proactive_log y se dispara UNA vez por día
local. Así, aunque el tick corra varias veces dentro de la ventana, no repite.
"""
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import db, geo, search, timeutil, watchers

log = logging.getLogger("companion.scheduler")

MATUTINO  = (8.0, 10.0)
NOCTURNO  = (20.0, 22.0)
# Recomendaciones por gustos (watchers.recomendar_por_gustos): mediodía, tarde y noche.
RECO_MEDIODIA = (12.0, 13.5)
RECO_TARDE    = (16.0, 17.5)
RECO_NOCHE    = (19.0, 20.0)
TICK_MINUTES = 15
# Viajes procesados en paralelo por tick. Cada check-in es una llamada LLM de
# varios segundos; en serie, con muchos viajes activos un tick podía durar más
# de 15 min y APScheduler (max_instances=1) se saltaba los siguientes.
TICK_WORKERS = int(os.environ.get("SCHEDULER_WORKERS", "4"))
# Evita que un tick manual (/scheduler/tick) y el automático corran a la vez en
# el mismo proceso. Entre procesos/réplicas, el dedup atómico de proactive_log
# (UNIQUE + ON CONFLICT) ya impide disparos dobles.
_tick_lock = threading.Lock()

# Ventana de tolerancia para disparar un aviso puntual (±TICK_MINUTES/2)
_HALF_TICK = TICK_MINUTES / 2


def _activo(trip: dict, hoy_local: date) -> bool:
    try:
        ini = datetime.strptime(trip["inicio"], "%Y-%m-%d").date()
        fin = datetime.strptime(trip["fin"],    "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return False
    return ini <= hoy_local <= fin


def _en_ventana(h: float, ventana: tuple[float, float]) -> bool:
    return ventana[0] <= h < ventana[1]


def _disparar(trip_id: str, kind: str, local_date: str, fn) -> None:
    """Marca-y-dispara. La marca va ANTES (atómica: si dos réplicas corren el
    tick a la vez solo una gana). Si el disparo FALLA, se desmarca para que el
    próximo tick lo reintente — antes un LLM caído a las 8am hacía perder el
    check-in matutino de todo el día."""
    if not db.mark_proactive_sent(trip_id, kind, local_date):
        return
    try:
        fn(trip_id)
    except Exception:
        log.exception("disparo_fallo kind=%s trip=%s — se reintenta en el próximo tick", kind, trip_id)
        try:
            db.unmark_proactive_sent(trip_id, kind, local_date)
        except Exception:
            log.exception("unmark_fallo kind=%s trip=%s", kind, trip_id)


def _plan_coords(plan: dict, city: str) -> tuple[float | None, float | None]:
    """Busca las coordenadas del lugar del plan en destination_places (curación).
    El matching normaliza tildes, mayúsculas y espacios, así que 'ElCielo',
    'El Cielo' y 'el cielo' se resuelven al mismo lugar."""
    lugar = (plan.get("lugar") or plan.get("titulo") or "").strip()
    if not lugar:
        return None, None
    match = db.best_place_match(lugar, db.places_for_city(city))
    if match:
        return match["lat"], match["lng"]
    return None, None


def _aviso_planes(trip: dict, ahora: datetime) -> list[str]:
    """Revisa los planes de HOY que tienen hora y dispara push de salida si
    falta ~1h o ~15min (calculado con tiempo de viaje estimado)."""
    disparados = []
    tid = trip["id"]
    city = trip.get("ciudad", "")
    hoy_iso = ahora.date().isoformat()

    try:
        planes = db.get_plans(tid)
    except Exception:
        return disparados

    # Origen: GPS real si existe, si no zona_actual
    orig_lat = trip.get("lat_actual")
    orig_lng = trip.get("lng_actual")
    if orig_lat is None or orig_lng is None:
        coords = geo.zone_coords(city, trip.get("zona_actual", ""))
        if coords:
            orig_lat, orig_lng = coords

    for plan in planes:
        if plan.get("fecha") != hoy_iso or not plan.get("hora"):
            continue
        try:
            hh, mm = plan["hora"].split(":")
            plan_dt = ahora.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            continue

        # Coordenadas del destino
        dest_lat, dest_lng = _plan_coords(plan, city)

        # Tiempo de viaje estimado
        dist_km = None
        if orig_lat and dest_lat:
            dist_km = geo.haversine_km(orig_lat, orig_lng, dest_lat, dest_lng)
        viaje_min = geo.travel_minutes(dist_km, city)

        # Hora en que debería salir
        salida_dt = plan_dt - timedelta(minutes=viaje_min)

        # Minutos que faltan HASTA la hora de salida
        faltan = (salida_dt - ahora).total_seconds() / 60

        plan_id_slug = plan.get("id", "")[:8]
        titulo = plan.get("titulo", "tu plan")

        # Aviso 1h antes (cuando falta ~60 min para la hora de salida)
        if abs(faltan - 60) <= _HALF_TICK:
            kind = f"salida_1h_{plan_id_slug}_{hoy_iso}"
            if db.mark_proactive_sent(tid, kind, hoy_iso):
                dist_txt = f" (~{round(dist_km, 1)} km)" if dist_km else ""
                watchers.aviso_hora_de_salir(
                    tid, titulo,
                    minutos_antes=viaje_min,
                    extra_msg=f"Tienes {titulo} a las {plan['hora']}. "
                              f"El viaje{dist_txt} toma ~{viaje_min} min — "
                              f"sal cerca de las {salida_dt.strftime('%H:%M')}. Aún tienes una hora.",
                )
                disparados.append(f"1h_antes:{titulo}")

        # Aviso 15min antes de la hora de salida
        if abs(faltan - 15) <= _HALF_TICK:
            kind = f"salida_15m_{plan_id_slug}_{hoy_iso}"
            if db.mark_proactive_sent(tid, kind, hoy_iso):
                dist_txt = f" (~{round(dist_km, 1)} km)" if dist_km else ""
                watchers.aviso_hora_de_salir(
                    tid, titulo,
                    minutos_antes=15,
                    extra_msg=f"¡Es hora de salir para {titulo} a las {plan['hora']}! "
                              f"El trayecto{dist_txt} toma ~{viaje_min} min — "
                              f"sal ya para llegar puntual.",
                )
                disparados.append(f"15m_antes:{titulo}")

    return disparados


def _procesar_trip(trip: dict, disparos: list) -> None:
    """Todo lo proactivo de UN viaje en este tick. Nunca lanza."""
    try:
        ahora    = timeutil.now_local(trip)
        hoy      = ahora.date()
        h        = ahora.hour + ahora.minute / 60
        ld       = hoy.isoformat()

        if not _activo(trip, hoy):
            return

        tid = trip["id"]

        if _en_ventana(h, MATUTINO):
            _disparar(tid, "matutino", ld,
                      lambda t: disparos.append(("matutino", t, watchers.check_in_matutino(t))))
            # Destination Scanner (Tavily): una pasada diaria por viaje en la
            # ventana matutina. Solo corre si Tavily está configurado (en mock
            # devuelve lista vacía, así que no gasta ni ensucia). El dedup por
            # fecha local evita que se repita aunque el tick corra varias veces.
            if search.MODE == "real":
                _disparar(tid, "scan", ld,
                          lambda t: disparos.append(("scan", t, watchers.scan_destination(t))))

        if trip.get("gustos"):
            for slot, ventana in (("mediodia", RECO_MEDIODIA), ("tarde", RECO_TARDE), ("noche", RECO_NOCHE)):
                if _en_ventana(h, ventana):
                    _disparar(tid, f"reco_{slot}", ld,
                              lambda t, s=slot: disparos.append((f"reco_{s}", t, watchers.recomendar_por_gustos(t, s))))

        if _en_ventana(h, NOCTURNO):
            _disparar(tid, "nocturno", ld,
                      lambda t: disparos.append(("nocturno", t, watchers.check_in(t))))

        try:
            fin = datetime.strptime(trip["fin"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            fin = None
        if fin == hoy and _en_ventana(h, MATUTINO) and trip.get("vuelo_regreso"):
            _disparar(tid, "vuelo_regreso", ld,
                      lambda t: disparos.append(("vuelo_regreso", t, watchers.recordatorio_vuelo_regreso(t))))

        # Avisos de salida para planes de hoy con hora
        plan_disparos = _aviso_planes(trip, ahora)
        for d in plan_disparos:
            disparos.append(("aviso_salida", tid, d))

    except Exception:
        log.exception("tick_trip_error trip=%s", trip.get("id"))


def tick() -> dict:
    disparos: list = []  # list.append es atómico en CPython: seguro entre threads
    if not _tick_lock.acquire(blocking=False):
        log.info("tick_omitido — ya hay un tick corriendo en este proceso")
        return {"trips": 0, "disparos": [], "omitido": True}
    t0 = time.monotonic()
    try:
        trips = db.list_trips()
        if TICK_WORKERS > 1 and len(trips) > 1:
            with ThreadPoolExecutor(max_workers=TICK_WORKERS, thread_name_prefix="companion-tick") as ex:
                list(ex.map(lambda t: _procesar_trip(t, disparos), trips))
        else:
            for trip in trips:
                _procesar_trip(trip, disparos)
        _limpieza()
    finally:
        _tick_lock.release()
    log.info("tick_ok trips=%d disparos=%d dur_s=%.2f", len(trips), len(disparos),
             time.monotonic() - t0)
    return {"trips": len(trips), "disparos": [(k, t) for (k, t, _) in disparos]}


def _limpieza() -> None:
    """Higiene barata en cada tick: sesiones y tokens vencidos (antes crecían
    para siempre). Nunca rompe el tick."""
    try:
        from . import auth
        auth.cleanup_expired()
    except Exception:
        log.exception("limpieza_error")


_scheduler = None


def start() -> bool:
    global _scheduler
    if _scheduler is not None:
        return True
    if os.environ.get("SCHEDULER_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        # Útil con varias réplicas/workers: dejar el scheduler en una sola.
        log.info("scheduler desactivado por SCHEDULER_ENABLED")
        return False
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        log.warning("scheduler APScheduler no instalado — modo proactivo desactivado")
        return False
    _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    # misfire_grace_time: si el proceso estuvo ocupado/dormido, un tick atrasado
    # aún corre (coalesce=True los colapsa en uno solo).
    _scheduler.add_job(tick, "interval", minutes=TICK_MINUTES, id="companion_proactive",
                       max_instances=1, coalesce=True, misfire_grace_time=TICK_MINUTES * 60)
    _scheduler.start()
    log.info("scheduler activo — tick cada %d min", TICK_MINUTES)
    return True


def running() -> bool:
    return _scheduler is not None and getattr(_scheduler, "running", False)


def shutdown() -> None:
    """Apaga el scheduler en el shutdown de la app (sin esperar al tick en curso:
    el dedup + unmark garantizan que lo pendiente se retome en el próximo tick)."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            log.exception("scheduler_shutdown_error")
        _scheduler = None
