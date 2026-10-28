"""Agenda de hoy CALCULADA en código: cuánto falta para cada plan con hora, cuánto se tarda en llegar y a qué hora hay que salir.

Es la ÚNICA fuente de esos números: la usan los avisos de salida (scheduler), el contexto del chat (context.build) y las
recomendaciones por hueco. Así el Companion no hace aritmética de horas por su cuenta (donde cualquier modelo se equivoca) y
el aviso del celular y la respuesta del chat nunca se contradicen.

El viaje a cada plan se calcula DESDE DONDE ESTARÁ el usuario: si antes tiene otro plan, desde ese lugar (no desde el hotel).
Caso real: tras Monserrate tenía el tour por La Candelaria, a ~10 min, y le dijeron que saliera 44 min antes porque se
calculó desde el hotel.
"""
from datetime import datetime, timedelta

from . import db, geo

# Un plan que empezó hace menos de esto sigue contando como «en curso»; después se omite.
EN_CURSO_MIN = 30
# Con menos de esto por delante para SALIR, es «ya casi / ya».
YA_ES_HORA_MIN = 15
# Cuánto dura un plan cuando no lo sabemos.
DURACION_PLAN = {"actividad": 120, "restaurante": 90, "transporte": 45, "vuelo": 180, "otro": 60}
# Viaje entre dos planes cuando no sabemos dónde fue el anterior (típico dentro de la ciudad, no «desde el hotel»).
VIAJE_ENTRE_PLANES_MIN = 25
# Cuánto puede apretar el itinerario antes de avisarlo.
TOLERANCIA_APRETADO_MIN = 10


def origen(trip: dict) -> tuple[float, float] | None:
    """GPS real si existe; si no, el punto de la zona actual."""
    lat, lng = trip.get("lat_actual"), trip.get("lng_actual")
    if lat is None or lng is None:
        c = geo.zone_coords(trip.get("ciudad", ""), trip.get("zona_actual", ""))
        if c:
            lat, lng = c
    return (lat, lng) if lat and lng else None


def duracion(plan: dict) -> int:
    return DURACION_PLAN.get(plan.get("tipo") or "otro", 60)


def calcular(trip: dict, ahora: datetime, planes: list[dict] | None = None) -> list[dict]:
    """Planes de HOY con hora, ordenados, con: plan_dt, viaje_min, desde (nombre del lugar anterior o None),
    salida_dt, faltan_plan_min, faltan_salida_min, lugar (nombre curado o None), coords (del lugar o None),
    sin_ubicacion (el viaje es un estimado sin ubicación real) y apretado_min (>0: tendría que salir ANTES de terminar el
    plan anterior). Nunca lanza."""
    if planes is None:
        try:
            planes = db.get_plans(trip["id"])
        except Exception:
            return []
    ciudad = trip.get("ciudad", "")
    hoy = ahora.date().isoformat()
    try:
        lugares = db.places_for_city(ciudad)
    except Exception:
        lugares = []

    items = []
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
        items.append((plan_dt, p, m))
    items.sort(key=lambda x: x[0])

    actual = origen(trip)
    salida = []
    prev = None  # (plan_dt, plan, match) del plan anterior de hoy
    for plan_dt, p, m in items:
        desde, apretado = None, 0
        if prev is not None:
            # El usuario estará en el lugar del plan anterior: de ahí sale.
            p_dt, p_plan, p_m = prev
            if p_m and m:
                dist = geo.haversine_km(p_m["lat"], p_m["lng"], m["lat"], m["lng"])
                viaje, sin_ub, desde = geo.travel_minutes(dist, ciudad), False, p_m["name"]
            else:
                viaje, sin_ub = VIAJE_ENTRE_PLANES_MIN, True
            fin_anterior = p_dt + timedelta(minutes=duracion(p_plan))
            apretado = max(0, round((fin_anterior - (plan_dt - timedelta(minutes=viaje))).total_seconds() / 60))
            if apretado <= TOLERANCIA_APRETADO_MIN:
                apretado = 0
        else:
            dist = geo.haversine_km(actual[0], actual[1], m["lat"], m["lng"]) if (actual and m) else None
            viaje, sin_ub = geo.travel_minutes(dist, ciudad), dist is None
        salida_dt = plan_dt - timedelta(minutes=viaje)
        salida.append({"plan": p, "plan_dt": plan_dt, "viaje_min": viaje, "desde": desde, "salida_dt": salida_dt,
                       "faltan_plan_min": (plan_dt - ahora).total_seconds() / 60,
                       "faltan_salida_min": (salida_dt - ahora).total_seconds() / 60,
                       "lugar": m["name"] if m else None, "coords": (m["lat"], m["lng"]) if m else None,
                       "sin_ubicacion": sin_ub, "apretado_min": apretado,
                       "prev_titulo": prev[1].get("titulo") if prev else None})
        prev = (plan_dt, p, m)
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
        if x["desde"]:
            viaje = f"viaje estimado ~{x['viaje_min']} min desde {x['desde']} (su plan anterior, donde estará)"
        elif x["prev_titulo"]:
            viaje = f"viaje estimado ~{x['viaje_min']} min desde su plan anterior ({x['prev_titulo']})"
        else:
            viaje = f"viaje estimado ~{x['viaje_min']} min" + (" (sin saber de dónde sale: es un estimado)" if x["sin_ubicacion"] else "")
        fs = x["faltan_salida_min"]
        if fs <= YA_ES_HORA_MIN:
            estado = (f"ES HORA DE SALIR YA (debía salir a las {x['salida_dt'].strftime('%H:%M')})" if fs <= 0
                      else f"hora de salir {x['salida_dt'].strftime('%H:%M')} (en {_dur(fs)}): ya casi, avísale que se aliste")
        else:
            estado = (f"hora de salir {x['salida_dt'].strftime('%H:%M')} (en {_dur(fs)}). TODAVÍA NO ES HORA DE SALIR: "
                      "no le digas «sal ya»; hasta entonces tiene tiempo libre")
        linea = cab + f"faltan {_dur(x['faltan_plan_min'])}; {viaje}; {estado}."
        if x["apretado_min"]:
            linea += (f" OJO: para llegar a tiempo tendría que salir de «{x['prev_titulo']}» unos {x['apretado_min']} min ANTES de "
                      "lo previsto: el itinerario está apretado; díselo con honestidad para que decida qué priorizar.")
        lineas.append(linea)
    if not lineas:
        return ""
    return ("\n>>> AGENDA DE HOY CALCULADA (números ya resueltos: úsalos TAL CUAL, no hagas la cuenta tú ni los contradigas) <<<\n"
            + "\n".join(lineas) + "\n")
