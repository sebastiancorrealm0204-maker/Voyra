"""Recomendación por HUECO: «veo que tienes un hueco, aprovecha para ir a X».

Cuando el usuario tiene tiempo libre antes de su próximo plan con hora (hoy), se le propone UN lugar curado que:
  · cruza con lo que marcó que le gusta en el onboarding (etiquetas o categoría),
  · está abierto cuando llegaría (y sigue abierto un rato),
  · cabe en el hueco: ir + visitar + llegar al siguiente plan con margen.
Sale como push corto (un gancho: «Café de especialidad → Tropicalia Coffee») y el mensaje completo queda en el chat; al
tocar abre «Cómo llegar» a ese lugar. Máximo 2 por día, y no se junta con otra recomendación reciente.
Todo es determinista (sin LLM): el lugar, las horas y los minutos salen de la curación y del cálculo, no de un modelo.
"""
import logging
from datetime import datetime, timedelta

from . import db, geo, horarios, watchers

log = logging.getLogger("companion.huecos")

VENTANA = (10.0, 19.5)       # hora local en la que se ofrecen huecos
MAX_POR_DIA = 2
SEPARACION_MIN = 120         # entre una recomendación y la siguiente
HUECO_MIN, HUECO_MAX = 90, 360   # minutos libres hasta el próximo plan: menos no alcanza; más lo cubren las ideas del día
MARGEN_MIN = 15              # colchón antes del siguiente plan
# Cuánto dura un plan cuando no sabemos (para saber si está EN un plan ahora mismo).
_DURACION_PLAN = {"actividad": 120, "restaurante": 90, "transporte": 45, "vuelo": 180, "otro": 60}
_VISITA_MIN = {"cafe": 45, "mirador": 45, "mercado": 60, "parque": 60, "atraccion": 75, "experiencia": 90,
               "restaurante": 75, "bar": 60}
_EMOJI = {"cafe": "☕", "restaurante": "🍽️", "bar": "🍸", "parque": "🌿", "mirador": "🌄", "mercado": "🛍️",
          "atraccion": "📍", "experiencia": "✨"}

_TITULO = {"es": "⏳ Libre hasta las {hasta}", "en": "⏳ Free until {hasta}", "pt": "⏳ Livre até às {hasta}",
           "fr": "⏳ Libre jusqu'à {hasta}", "de": "⏳ Frei bis {hasta}", "it": "⏳ Libero fino alle {hasta}"}
_CUERPO = {"es": "{emoji} {gusto} → {name} (a {min} min)", "en": "{emoji} {gusto} → {name} ({min} min away)",
           "pt": "{emoji} {gusto} → {name} (a {min} min)", "fr": "{emoji} {gusto} → {name} (à {min} min)",
           "de": "{emoji} {gusto} → {name} ({min} Min. entfernt)", "it": "{emoji} {gusto} → {name} (a {min} min)"}
_CHAT = {
    "es": "Veo que tienes un hueco hasta las {hasta} (tu {plan} es a las {hora}). Como marcaste que te gusta «{gusto}», "
          "aprovecha para ir a {name} ({zona}). {clave} Queda a ~{min} min y alcanzas a volver con tiempo. ¿Te cuento cómo llegar?",
    "en": "You have a gap until {hasta} (your {plan} is at {hora}). You said you like “{gusto}”, so make the most of it and "
          "go to {name} ({zona}). {clave} It's ~{min} min away and you'll be back in time. Want directions?",
    "pt": "Você tem um intervalo até às {hasta} (seu {plan} é às {hora}). Como você marcou que gosta de «{gusto}», aproveite "
          "para ir a {name} ({zona}). {clave} Fica a ~{min} min e dá tempo de voltar. Quer saber como chegar?",
    "fr": "Tu as un créneau libre jusqu'à {hasta} (ton {plan} est à {hora}). Comme tu aimes « {gusto} », profites-en pour aller à "
          "{name} ({zona}). {clave} C'est à ~{min} min et tu rentres à temps. Je t'explique comment y aller ?",
    "de": "Du hast eine Lücke bis {hasta} (dein {plan} ist um {hora}). Da du „{gusto}“ magst, nutze sie für {name} ({zona}). "
          "{clave} Es ist ~{min} Min. entfernt und du bist rechtzeitig zurück. Soll ich dir den Weg zeigen?",
    "it": "Hai un buco fino alle {hasta} (il tuo {plan} è alle {hora}). Visto che ti piace «{gusto}», approfittane per andare a "
          "{name} ({zona}). {clave} È a ~{min} min e fai in tempo a tornare. Ti dico come arrivare?",
}


def _a_datetime(ahora: datetime, hora: str) -> datetime | None:
    try:
        hh, mm = hora.split(":")[:2]
        return ahora.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    except (ValueError, AttributeError):
        return None


def hueco_actual(planes: list[dict], ahora: datetime) -> dict | None:
    """El tiempo libre de AHORA hasta el próximo plan con hora de hoy: {'plan', 'inicio', 'libre_min'}.
    None si ahora está dentro de un plan o no queda ningún plan con hora hoy."""
    hoy = ahora.date().isoformat()
    siguientes = []
    for p in planes:
        if p.get("fecha") != hoy or not p.get("hora") or p.get("tipo") == "hotel":
            continue
        ini = _a_datetime(ahora, p["hora"])
        if ini is None:
            continue
        fin = ini + timedelta(minutes=_DURACION_PLAN.get(p.get("tipo") or "otro", 60))
        if ini <= ahora < fin:
            return None
        if ini > ahora:
            siguientes.append((ini, p))
    if not siguientes:
        return None
    ini, plan = min(siguientes, key=lambda x: x[0])
    return {"plan": plan, "inicio": ini, "libre_min": (ini - ahora).total_seconds() / 60}


def _categorias(hora_llegada: float) -> set[str]:
    cats = {"cafe", "atraccion", "parque", "mercado", "experiencia", "mirador"}
    if 12 <= hora_llegada < 15 or 18.5 <= hora_llegada < 22:
        cats.add("restaurante")
    if hora_llegada >= 18:
        cats.add("bar")
    return cats


def elegir(trip: dict, hueco: dict, ahora: datetime, planes: list[dict]) -> dict | None:
    """El mejor lugar curado para este hueco: {'lugar', 'gusto', 'min'} o None."""
    gustos = [g for g in (trip.get("gustos") or []) if g]
    origen = geo.resolve_origin(trip)
    if not gustos or not origen:
        return None
    ciudad = trip.get("ciudad", "")
    lugares = db.places_for_city(ciudad)
    ya = db.recently_recommended(trip["id"], hours=24 * 60)
    en_plan = " ".join(f"{p.get('titulo', '')} {p.get('lugar') or ''}" for p in planes).lower()
    # Dónde es el siguiente plan (si es un lugar curado): hay que poder llegar a él desde el lugar recomendado.
    sig = hueco["plan"]
    texto_sig = (sig.get("lugar") or sig.get("titulo") or "").strip()
    m = db.best_place_match(texto_sig, lugares) if texto_sig else None
    destino = (m["lat"], m["lng"]) if m else None

    mejores = []
    for p in lugares:
        if p["name"] in ya or p["name"].lower() in en_plan:
            continue
        calzan = watchers.gustos_que_calzan(p, gustos)
        if not calzan:
            continue
        d = geo.haversine_km(origen[0], origen[1], p["lat"], p["lng"])
        if d is None:
            continue
        ida = geo.travel_minutes(d, ciudad)
        llegada = ahora + timedelta(minutes=ida)
        if p.get("category") not in _categorias(llegada.hour + llegada.minute / 60):
            continue
        visita = _VISITA_MIN.get(p.get("category"), 60)
        if destino:
            vuelta = geo.travel_minutes(geo.haversine_km(p["lat"], p["lng"], destino[0], destino[1]), ciudad)
        else:
            vuelta = ida  # no sabemos dónde es el siguiente plan: se asume que se vuelve de donde se vino
        if ida + visita + vuelta + MARGEN_MIN > hueco["libre_min"]:
            continue
        est = horarios.estado(p.get("place_id"), llegada)
        if est and not est["abierto"]:
            continue
        if est and est.get("cierra"):  # que no cierre antes de terminar la visita
            hh, mm = est["cierra"].split(":")
            cierre = llegada.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if cierre < llegada:
                cierre += timedelta(days=1)  # cierra pasada la medianoche
            if cierre < llegada + timedelta(minutes=visita):
                continue
        mejores.append((-len(calzan), ida // 10, -float(p.get("rating") or 0), p["name"], p, calzan[0], ida))
    if not mejores:
        return None
    mejores.sort(key=lambda x: x[:4])
    _, _, _, _, lugar, gusto, ida = mejores[0]
    return {"lugar": lugar, "gusto": gusto, "min": ida}


def revisar(trip: dict, ahora: datetime, planes: list[dict] | None = None) -> list[str]:
    """Si hay un hueco que aprovechar, manda UNA recomendación. Devuelve los kinds enviados. Nunca lanza."""
    tid = trip["id"]
    try:
        h = ahora.hour + ahora.minute / 60
        if not trip.get("gustos") or not (VENTANA[0] <= h < VENTANA[1]):
            return []
        hoy = ahora.date().isoformat()
        kinds = [f"hueco_{i}" for i in range(1, MAX_POR_DIA + 1)]
        libre = [k for k in kinds if not db.proactive_already_sent(tid, k, hoy)]
        if not libre or db.recommended_within(tid, SEPARACION_MIN):
            return []
        if planes is None:
            planes = db.get_plans(tid)
        hueco = hueco_actual(planes, ahora)
        if not hueco or not (HUECO_MIN <= hueco["libre_min"] <= HUECO_MAX):
            return []
        eleccion = elegir(trip, hueco, ahora, planes)
        if not eleccion:
            return []
        if not db.mark_proactive_sent(tid, libre[0], hoy):
            return []
        try:
            _enviar(trip, hueco, eleccion)
        except Exception:
            log.exception("hueco_fallo trip=%s", tid)
            db.unmark_proactive_sent(tid, libre[0], hoy)
            return []
        return [libre[0]]
    except Exception:
        log.exception("huecos_error trip=%s", tid)
        return []


def _enviar(trip: dict, hueco: dict, eleccion: dict) -> None:
    from . import traducir
    tid = trip["id"]
    idioma = (trip.get("idioma") or "es").lower()
    if idioma not in _CHAT:
        idioma = "es"
    p, gusto = eleccion["lugar"], eleccion["gusto"]
    local = p.get("local") if isinstance(p.get("local"), dict) else {}
    clave = (local.get("clave") or "").strip()
    if idioma != "es":
        try:
            trad = traducir.traducir([x for x in (clave, gusto) if x], idioma)
            gusto = trad[-1]
            clave = trad[0] if clave else ""
        except Exception:
            pass
    plan = hueco["plan"]
    datos = {"hasta": hueco["inicio"].strftime("%H:%M"), "hora": plan.get("hora") or "", "plan": plan.get("titulo") or "…",
             "gusto": gusto, "name": p["name"], "zona": p.get("zona", ""), "clave": clave, "min": eleccion["min"],
             "emoji": _EMOJI.get(p.get("category"), "📍")}
    texto = " ".join(_CHAT[idioma].format(**datos).split())
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": texto})
    db.mark_recommended(tid, p["name"])
    watchers._push_checkin(tid, _TITULO[idioma].format(**datos), texto, abrir="como_llegar", dest=p["name"],
                           gancho=_CUERPO[idioma].format(**datos))
