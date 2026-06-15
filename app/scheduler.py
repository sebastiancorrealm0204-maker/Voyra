"""Scheduler proactivo del Companion.

Despierta al Companion solo, sin que el usuario toque nada. Corre un tick cada
pocos minutos (APScheduler, dentro del mismo proceso de uvicorn) y, para cada
viaje ACTIVO, decide en HORA LOCAL DEL DESTINO si toca disparar:

- check-in matutino  (~8am)         → conversación cálida con el plan del día
- check-in nocturno  (~8pm)         → confirma/pregunta planes de mañana
- recordatorio de vuelo de regreso  (mañana del día 'fin') → push operativo
- aviso_hora_de_salir (1h y 15min antes de cada plan con hora) → push operativo

Dedup: cada evento se registra en db.proactive_log y se dispara UNA vez por día
local. Así, aunque el tick corra varias veces dentro de la ventana, no repite.
"""
import traceback
from datetime import date, datetime, timedelta

from . import db, geo, timeutil, watchers

MATUTINO  = (8.0, 10.0)
NOCTURNO  = (20.0, 22.0)
TICK_MINUTES = 15

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
    if not db.mark_proactive_sent(trip_id, kind, local_date):
        return
    fn(trip_id)


def _plan_coords(plan: dict, city: str) -> tuple[float | None, float | None]:
    """Busca las coordenadas del lugar del plan en destination_places (curación).
    Primero por nombre exacto (case-insensitive), luego coincidencia parcial."""
    lugar = (plan.get("lugar") or plan.get("titulo") or "").strip().lower()
    if not lugar:
        return None, None
    for p in db.places_for_city(city):
        if lugar in p["name"].lower() or p["name"].lower() in lugar:
            return p["lat"], p["lng"]
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


def tick() -> dict:
    disparos = []
    for trip in db.list_trips():
        try:
            ahora    = timeutil.now_local(trip)
            hoy      = ahora.date()
            h        = ahora.hour + ahora.minute / 60
            ld       = hoy.isoformat()

            if not _activo(trip, hoy):
                continue

            tid = trip["id"]

            if _en_ventana(h, MATUTINO):
                _disparar(tid, "matutino", ld,
                          lambda t: disparos.append(("matutino", t, watchers.check_in_matutino(t))))

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
            traceback.print_exc()
            continue

    return {"trips": len(db.list_trips()), "disparos": [(k, t) for (k, t, _) in disparos]}


_scheduler = None


def start() -> bool:
    global _scheduler
    if _scheduler is not None:
        return True
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[scheduler] APScheduler no instalado — modo proactivo desactivado.")
        return False
    _scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
    _scheduler.add_job(tick, "interval", minutes=TICK_MINUTES, id="voyra_proactive",
                       max_instances=1, coalesce=True)
    _scheduler.start()
    print(f"[scheduler] activo — tick cada {TICK_MINUTES} min")
    return True
