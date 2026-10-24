"""Motor de relevancia del Companion.

Pipeline de dos etapas:
1. Filtro determinístico (costo cero): mata el ~80% de las señales sin tocar AI.
2. Scoring con LLM: score 0-100 → push (≥70) / feed (40-69) / silencio (<40).

Cada decisión se guarda en `decisions` con su razón: ese log ES el dataset de
entrenamiento futuro del scorer propio (principio acordado: guardar todo desde
el día uno como si fuéramos a entrenar mañana).
"""
import json
import logging

from . import context, db, llm, timeutil

log = logging.getLogger("voyra.engine")

# Avisos mesurados: el Companion avisa, pero no satura. Los operativos (vuelo,
# seguridad) no cuentan para el presupuesto y llegan siempre.
PRESUPUESTO_DIARIO = 3
# Si el usuario escribió hace menos de esto, está en la app: lo no urgente se
# queda en el feed in-app en vez de sonarle el celular.
EN_APP_MINUTOS = 15
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


_TITULOS_FALLBACK = {
    "vuelo": "Novedad de tu vuelo",
    "seguridad": "Alerta en tu destino",
    "itinerario": "Recordatorio de tu plan",
    "clima": "Cambio de clima",
}


def _score_fallback(category: str, operational: bool, payload: str) -> dict:
    """Scoring determinístico cuando el LLM falla (caído, timeout, JSON roto).

    Lo crítico es que un evento OPERATIVO (vuelo, seguridad, hora de salir)
    llegue SIEMPRE: antes, una falla del proveedor reventaba el ingest con un
    500 y el aviso de retraso de vuelo se perdía. Lo no operativo baja a feed
    (score 50): queda visible en la app sin gastar push con un texto genérico.
    """
    titulo = _TITULOS_FALLBACK.get(category, "Novedad de tu viaje")
    return {"score": 90 if operational else 50,
            "razon": "Scoring LLM no disponible: decisión determinística de respaldo.",
            "titulo": titulo, "mensaje": (payload or "")[:280],
            "accion": "Ver detalle" if operational else None, "_mode": "fallback"}


def _score(system_fn, source: str, payload: str, category: str, operational: bool,
           extra: dict | None) -> dict:
    """llm.score_event con validación de la salida y fallback determinístico.
    `system_fn` es perezoso: en modo mock el system prompt (~40K caracteres)
    no se usa, así que ni se construye."""
    try:
        mock = llm.SCORING_PROVIDER == "mock" or not llm._available()
        r = llm.score_event("" if mock else system_fn(), source, payload, category, extra=extra)
        r["score"] = max(0, min(100, int(float(r["score"]))))
        if not str(r.get("titulo") or "").strip() or not str(r.get("mensaje") or "").strip():
            raise ValueError("salida LLM sin titulo/mensaje")
        r.setdefault("_mode", "real")
        return r
    except Exception as e:
        log.warning("scoring_fallback category=%s operational=%s error=%s",
                    category, operational, type(e).__name__)
        return _score_fallback(category, operational, payload)


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

    # El idioma del viaje viaja aparte (clave privada, no se persiste en la notificación): el título y el mensaje
    # de la señal deben salir en el idioma del usuario, no en el del prompt.
    r = _score(lambda: context.build(trip), source, payload, category, operational,
               {**(extra or {}), "_idioma": (trip.get("idioma") or "es").lower()})
    score = max(int(r["score"]), 90) if operational else int(r["score"])
    decision = "push" if score >= 70 else "feed" if score >= 40 else "silence"
    if decision == "push" and not operational and db.user_active_within(trip_id, EN_APP_MINUTOS):
        decision = "feed"

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
        # Se despacha en background: el request (o el tick del scheduler) no
        # espera a FCM/APNs, que pueden tardar varios segundos por dispositivo.
        if decision == "push":
            try:
                from . import push
                push.send_to_trip_async(trip_id, r["titulo"], r["mensaje"],
                                        data={"notification_id": nid, "category": category,
                                              "action": r.get("accion")})
            except Exception:
                log.exception("push_dispatch_error trip=%s", trip_id)

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
