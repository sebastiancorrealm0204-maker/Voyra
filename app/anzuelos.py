"""Notificaciones «señuelo» para que el usuario vuelva a la app: ganchos CIERTOS (datos reales de la curación y de su agenda), no
urgencia inventada. Hoy:

- vispera: la noche anterior a un plan con hora, un dato de local sobre ESE lugar («sube temprano: la fila crece desde las 10»)
  y la invitación a planear cómo llegar. Toca → abre Planes.

Comparte el tope diario de pushes «de interés» con las recomendaciones (watchers.ENGAGEMENT_MAX_DIA) para no saturar.
Todo es determinista (sin LLM).
"""
import logging
from datetime import datetime, timedelta

from . import db, watchers

log = logging.getLogger("companion.anzuelos")

VENTANA_VISPERA = (19.5, 21.5)   # hora local en la que se manda el aviso de víspera
# Campos de la ficha local, en orden de interés para un aviso (el más accionable primero).
_CAMPOS_TIP = ("momento", "ojo", "dato", "clave")

_TITULO = {"es": "🗓️ Mañana: {plan}", "en": "🗓️ Tomorrow: {plan}", "pt": "🗓️ Amanhã: {plan}",
           "fr": "🗓️ Demain : {plan}", "de": "🗓️ Morgen: {plan}", "it": "🗓️ Domani: {plan}"}
_SIN_TIP = {"es": "A las {hora} · toca y lo planeamos", "en": "At {hora} · tap and we'll plan it",
            "pt": "Às {hora} · toque e planejamos", "fr": "À {hora} · touche, on le planifie",
            "de": "Um {hora} · tippen, wir planen es", "it": "Alle {hora} · tocca e lo pianifichiamo"}
_CHAT = {
    "es": ("Mañana tienes {plan} a las {hora}. {tip}¿Quieres que te diga a qué hora salir y cómo llegar?", "Tip de local: {tip} "),
    "en": ("Tomorrow you have {plan} at {hora}. {tip}Want me to tell you when to leave and how to get there?", "Local tip: {tip} "),
    "pt": ("Amanhã você tem {plan} às {hora}. {tip}Quer que eu diga a que horas sair e como chegar?", "Dica de local: {tip} "),
    "fr": ("Demain tu as {plan} à {hora}. {tip}Je te dis quand partir et comment y aller ?", "Astuce de local : {tip} "),
    "de": ("Morgen hast du {plan} um {hora}. {tip}Soll ich dir sagen, wann du losmusst und wie du hinkommst?", "Tipp von Einheimischen: {tip} "),
    "it": ("Domani hai {plan} alle {hora}. {tip}Ti dico quando partire e come arrivare?", "Consiglio da locale: {tip} "),
}


def _tip(lugar: dict | None) -> str:
    local = (lugar or {}).get("local")
    if not isinstance(local, dict):
        return ""
    for campo in _CAMPOS_TIP:
        v = (local.get(campo) or "").strip()
        if v:
            return v
    return ""


def vispera(trip: dict, ahora: datetime, planes: list[dict] | None = None) -> list[str]:
    """Si mañana hay un plan con hora, manda UN aviso de víspera (el más temprano). Nunca lanza."""
    tid = trip["id"]
    try:
        h = ahora.hour + ahora.minute / 60
        if not (VENTANA_VISPERA[0] <= h < VENTANA_VISPERA[1]):
            return []
        hoy = ahora.date().isoformat()
        manana = (ahora.date() + timedelta(days=1)).isoformat()
        if planes is None:
            planes = db.get_plans(tid)
        siguientes = sorted((p for p in planes if p.get("fecha") == manana and p.get("hora") and p.get("tipo") not in ("hotel", "vuelo")),
                            key=lambda p: p["hora"])
        if not siguientes or db.engagement_count(tid, hoy) >= watchers.ENGAGEMENT_MAX_DIA:
            return []
        plan = siguientes[0]
        kind = f"vispera_{(plan.get('id') or plan['hora'])[:8]}"
        if not db.mark_proactive_sent(tid, kind, hoy):
            return []
        try:
            _enviar(trip, plan)
        except Exception:
            log.exception("vispera_fallo trip=%s", tid)
            db.unmark_proactive_sent(tid, kind, hoy)
            return []
        return [kind]
    except Exception:
        log.exception("anzuelos_error trip=%s", tid)
        return []


def _enviar(trip: dict, plan: dict) -> None:
    from . import traducir
    tid = trip["id"]
    idioma = (trip.get("idioma") or "es").lower()
    if idioma not in _CHAT:
        idioma = "es"
    texto_lugar = (plan.get("lugar") or plan.get("titulo") or "").strip()
    lugares = db.places_for_city(trip.get("ciudad", ""))
    lugar = db.best_place_match(texto_lugar, lugares) if texto_lugar else None
    tip = _tip(lugar)
    if tip and idioma != "es":
        try:
            tip = traducir.traducir([tip], idioma)[0]
        except Exception:
            pass
    datos = {"plan": plan.get("titulo") or "…", "hora": plan["hora"]}
    chat_base, chat_tip = _CHAT[idioma]
    texto = " ".join(chat_base.format(**datos, tip=chat_tip.format(tip=tip) if tip else "").split())
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": texto})
    gancho = f"💡 {tip}" if tip else _SIN_TIP[idioma].format(**datos)
    watchers._push_checkin(tid, _TITULO[idioma].format(**datos), texto, abrir="planes", gancho=gancho)
