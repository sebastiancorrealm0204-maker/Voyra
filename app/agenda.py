"""Agenda de hoy CALCULADA en código: cuánto falta para cada plan con hora, cuánto se tarda en llegar y a qué hora hay que salir.

Es la ÚNICA fuente de esos números: la usan los avisos de salida (scheduler) y el contexto del chat (context.build). Así el
Companion no hace aritmética de horas por su cuenta (donde cualquier modelo se equivoca: «sal ya» para un plan que es en 4 h) y
el aviso del celular y la respuesta del chat nunca se contradicen.
"""
from datetime import datetime, timedelta

from . import db, geo

# Un plan que empezó hace menos de esto sigue contando como «en curso»; después se omite.
EN_CURSO_MIN = 30
# Con más de esto por delante para SALIR, no es momento de salir.
YA_ES_HORA_MIN = 15


def origen(trip: dict) -> tuple[float, float] | None:
    """GPS real si existe; si no, el punto de la zona actual."""
    lat, lng = trip.get("lat_actual"), trip.get("lng_actual")
    if lat is None or lng is None:
        c = geo.zone_coords(trip.get("ciudad", ""), trip.get("zona_actual", ""))
        if c:
            lat, lng = c
    return (lat, lng) if lat and lng else None


def calcular(trip: dict, ahora: datetime, planes: list[dict] | None = None) -> list[dict]:
    """Planes de HOY con hora, ordenados, con: plan_dt, viaje_min, salida_dt, faltan_plan_min, faltan_salida_min, lugar
    (nombre curado o None) y sin_ubicacion (True si el viaje se estimó sin saber de dónde sale). Nunca lanza."""
    if planes is None:
        try:
            planes = db.get_plans(trip["id"])
        except Exception:
            return []
    ciudad = trip.get("ciudad", "")
    hoy = ahora.date().isoformat()
    o = origen(trip)
    try:
        lugares = db.places_for_city(ciudad)
    except Exception:
        lugares = []
    salida = []
    for p in planes:
        if p.get("fecha") != hoy or not p.get("hora") or p.get("tipo") in ("hotel", "vuelo"):
            continue
        try:
            hh, mm = p["hora"].split(":")[:2]
            plan_dt = ahora.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except (ValueError, AttributeError):
            continue
        texto = (p.get("lugar") or p.get("titulo") or "").strip()
        m = db.best_place_match(texto, lugares) if texto and lugares else None
        dist = geo.haversine_km(o[0], o[1], m["lat"], m["lng"]) if (o and m) else None
        viaje = geo.travel_minutes(dist, ciudad)
        salida_dt = plan_dt - timedelta(minutes=viaje)
        salida.append({"plan": p, "plan_dt": plan_dt, "viaje_min": viaje, "salida_dt": salida_dt,
                       "faltan_plan_min": (plan_dt - ahora).total_seconds() / 60,
                       "faltan_salida_min": (salida_dt - ahora).total_seconds() / 60,
                       "lugar": m["name"] if m else None, "sin_ubicacion": dist is None})
    salida.sort(key=lambda x: x["plan_dt"])
    return salida


def _dur(minutos: float) -> str:
    m = max(0, int(round(minutos)))
    h, r = divmod(m, 60)
    return f"{h} h {r:02d} min" if h and r else (f"{h} h" if h else f"{r} min")


def bloque(trip: dict, ahora: datetime, planes: list[dict] | None = None) -> str:
    """Texto para el prompt del chat. '' si hoy no hay planes con hora por delante."""
    lineas = []
    for x in calcular(trip, ahora, planes):
        if x["faltan_plan_min"] < -EN_CURSO_MIN:
            continue
        p = x["plan"]
        titulo = p.get("titulo") or "plan"
        cab = f"- {titulo}, hoy a las {p['hora']}: "
        if x["faltan_plan_min"] <= 0:
            lineas.append(cab + f"ya empezó (hace {_dur(-x['faltan_plan_min'])}).")
            continue
        viaje = (f"viaje estimado ~{x['viaje_min']} min" + (" (sin saber de dónde sale: es un estimado)" if x["sin_ubicacion"] else ""))
        fs = x["faltan_salida_min"]
        if fs <= YA_ES_HORA_MIN:
            estado = (f"ES HORA DE SALIR YA (debía salir a las {x['salida_dt'].strftime('%H:%M')})" if fs <= 0
                      else f"hora de salir {x['salida_dt'].strftime('%H:%M')} (en {_dur(fs)}): ya casi, avísale que se aliste")
        else:
            estado = (f"hora de salir {x['salida_dt'].strftime('%H:%M')} (en {_dur(fs)}). TODAVÍA NO ES HORA DE SALIR: "
                      "no le digas «sal ya»; hasta entonces tiene tiempo libre")
        lineas.append(cab + f"faltan {_dur(x['faltan_plan_min'])}; {viaje}; {estado}.")
    if not lineas:
        return ""
    return ("\n>>> AGENDA DE HOY CALCULADA (números ya resueltos: úsalos TAL CUAL, no hagas la cuenta tú ni los contradigas) <<<\n"
            + "\n".join(lineas) + "\n")
