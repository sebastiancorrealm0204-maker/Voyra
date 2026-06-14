"""Motor de relevancia del Companion.

Pipeline de dos etapas:
1. Filtro determinístico (costo cero): mata el ~80% de las señales sin tocar AI.
2. Scoring con LLM: score 0-100 → push (≥70) / feed (40-69) / silencio (<40).

Cada decisión se guarda en `decisions` con su razón: ese log ES el dataset de
entrenamiento futuro del scorer propio (principio acordado: guardar todo desde
el día uno como si fuéramos a entrenar mañana).
"""
import json

from . import context, db, llm, timeutil

PRESUPUESTO_DIARIO = 4
MAX_POR_CATEGORIA_DIA = 2
QUIET_START, QUIET_END = 22.5, 7.0  # 10:30pm – 7:00am hora local del destino
CATEGORIAS_BLOQUEADAS = {"promo_no_solicitada", "marketing"}


def _hora_local(trip: dict) -> float:
    # Hora real del destino (timezone IANA por ciudad), no la hora UTC del servidor.
    return timeutil.hour_float(trip)


def _filtro(trip: dict, category: str, operational: bool) -> str:
    """Devuelve 'pass' o la razón por la que la señal muere. Costo: cero."""
    if category in CATEGORIAS_BLOQUEADAS:
        return "categoria_bloqueada"
    if operational:
        return "pass"  # operativo: prioridad absoluta, no aplica presupuesto ni quiet hours
    if category in trip.get("categorias_silenciadas", []):
        return "categoria_silenciada_por_usuario"
    h = _hora_local(trip)
    if h >= QUIET_START or h < QUIET_END:
        return "quiet_hours"
    hoy = db.pushes_today(trip["id"])
    if len(hoy) >= PRESUPUESTO_DIARIO:
        return "presupuesto_agotado"
    if sum(1 for n in hoy if n["category"] == category) >= MAX_POR_CATEGORIA_DIA:
        return "tope_de_categoria"
    return "pass"


def ingest(trip_id: str, source: str, category: str, operational: bool, payload: str,
           extra: dict | None = None) -> dict:
    """Punto de entrada de toda señal. Devuelve la decisión completa.

    `extra`: datos adicionales calculados por el backend (no por el LLM) que se
    adjuntan a la notificación si hay push/feed — p.ej. maps_link, place_name,
    distancia_km (ver geo.py + watchers.nearby_recommendations). Esto evita que
    el modelo tenga que "inventar" coordenadas o URLs.
    """
    trip = db.get_trip(trip_id)
    if not trip:
        raise KeyError(f"trip {trip_id} no existe")

    event_id = db.insert("events", {
        "trip_id": trip_id, "source": source, "category": category,
        "operational": int(operational), "payload": payload,
    })

    filtro = _filtro(trip, category, operational)
    if filtro != "pass":
        decision = "feed" if filtro in ("presupuesto_agotado", "quiet_hours") else "silence"
        did = db.insert("decisions", {
            "trip_id": trip_id, "event_id": event_id, "filter_result": filtro,
            "score": None, "reason": None, "decision": decision, "llm_mode": "none",
        })
        return {"decision_id": did, "filter": filtro, "score": None, "decision": decision, "notification": None}

    system = context.build(trip)
    r = llm.score_event(system, source, payload, category, extra=extra)
    score = max(int(r["score"]), 90) if operational else int(r["score"])
    decision = "push" if score >= 70 else "feed" if score >= 40 else "silence"

    did = db.insert("decisions", {
        "trip_id": trip_id, "event_id": event_id, "filter_result": "pass",
        "score": score, "reason": r.get("razon"), "decision": decision, "llm_mode": r["_mode"],
    })

    notification = None
    if decision in ("push", "feed"):
        nid = db.insert("notifications", {
            "trip_id": trip_id, "decision_id": did, "kind": decision,
            "title": r["titulo"], "body": r["mensaje"],
            "action": r.get("accion"), "operational": int(operational), "category": category,
            "feedback": None, "extra": json.dumps(extra) if extra else None,
        })
        notification = {"id": nid, "kind": decision, "title": r["titulo"], "body": r["mensaje"], "action": r.get("accion")}
        if extra:
            notification.update(extra)
        # Despacho del push REAL al celular: solo para decisiones 'push' (las 'feed'
        # se quedan en el feed in-app). Web Push (VAPID). Si no hay claves VAPID
        # configuradas, push.send_to_trip no hace nada y la notificación igual
        # quedó persistida y consultable. El envío nunca debe romper el ingest.
        if decision == "push":
            try:
                from . import push
                push.send_to_trip(trip_id, r["titulo"], r["mensaje"],
                                  data={"notification_id": nid, "category": category, "action": r.get("accion")})
            except Exception:
                pass

    return {"decision_id": did, "filter": "pass", "score": score, "reason": r.get("razon"),
            "decision": decision, "notification": notification}


def feedback(notification_id: str, fb: str) -> dict:
    """tapped | dismissed | not_interested. Dos not_interested de una categoría → se silencia para este viaje."""
    n = db.set_feedback(notification_id, fb)
    if not n:
        raise KeyError(notification_id)
    silenced = False
    if fb == "not_interested" and not n["operational"]:
        if db.not_interested_count(n["trip_id"], n["category"]) >= 2:
            trip = db.get_trip(n["trip_id"])
            cats = set(trip.get("categorias_silenciadas", []))
            if n["category"] not in cats:
                cats.add(n["category"])
                db.update_trip(n["trip_id"], {"categorias_silenciadas": sorted(cats)})
                silenced = True
    return {"notification_id": notification_id, "feedback": fb, "category_silenced": silenced}
