"""Recomendación por HUECO: el Companion NO espera a que le pregunten; si ve tiempo libre, propone algo que valga la pena.

Cuando el usuario tiene tiempo libre antes de su próximo plan con hora (hoy), se evalúan TODOS los lugares curados de lo que le gusta y
se elige la mejor COMBINACIÓN de tres ángulos, cada uno con su razón (que es lo que se le cuenta):
  · «cerca»  : lo que le gusta, a pocos minutos de DONDE ESTÁ.
  · «plan»   : lo que le gusta, junto a su SIGUIENTE actividad (va y espera/llega con calma ahí al lado).
  · «paso»   : la mejor experiencia que le queda DE PASO para su siguiente actividad (desvío mínimo).
Siempre que: abierto cuando llegaría, y ir + visitar + llegar al siguiente plan cabe con margen. El puntaje premia lo que calza con
sus gustos (etiqueta fuerte), la calidad de la experiencia y cada ángulo que cumple; gana la mejor combinación.

El punto de partida es DONDE ESTARÁ: si acaba de terminar un plan (p. ej. Monserrate), se parte de ese lugar, no del hotel.
Sale como push corto (un gancho con la razón) y el mensaje completo queda en el chat; al tocar abre «Cómo llegar» a ese lugar.
Todo es determinista (sin LLM): lugar, horas y minutos salen de la curación y del cálculo. Comparte el motor con el chat
(estrategia.candidatos) y el tope diario de avisos «de interés» (watchers.ENGAGEMENT_MAX_DIA).
"""
import logging
from datetime import datetime, timedelta

from . import agenda, db, estrategia, geo, watchers

log = logging.getLogger("companion.huecos")

VENTANA = (9.5, 20.5)        # hora local en la que se ofrecen huecos
MAX_POR_DIA = 2
SEPARACION_MIN = 120         # entre una recomendación y la siguiente
HUECO_MIN, HUECO_MAX = 60, 360   # minutos libres hasta el próximo plan: menos no alcanza; más lo cubren las ideas del día
MARGEN_MIN = estrategia.MARGEN_MIN
RECIEN_TERMINADO_MIN = 45    # si un plan terminó hace menos de esto, se asume que el usuario sigue ahí
CERCA_DE_TI_MIN = 10         # «cerca de ti»: a esta distancia o menos
CERCA_DEL_PLAN_MIN = 10      # «junto a tu siguiente plan»: a esta distancia o menos
_EMOJI = watchers.EMOJI_CATEGORIA

_TITULO = {"es": "⏳ Libre hasta las {hasta}", "en": "⏳ Free until {hasta}", "pt": "⏳ Livre até às {hasta}",
           "fr": "⏳ Libre jusqu'à {hasta}", "de": "⏳ Frei bis {hasta}", "it": "⏳ Libero fino alle {hasta}"}
# Cada ángulo: (frase corta para el push, frase para el chat). {min} = minutos desde donde está; {plan} = su siguiente actividad.
_ANGULO = {
    "cerca": {
        "es": ("a {min} min de ti", "Te queda a ~{min} min de donde estás."),
        "en": ("{min} min from you", "It's ~{min} min from where you are."),
        "pt": ("a {min} min de você", "Fica a ~{min} min de onde você está."),
        "fr": ("à {min} min de toi", "C'est à ~{min} min d'où tu es."),
        "de": ("{min} Min. von dir", "Es ist ~{min} Min. von dir entfernt."),
        "it": ("a {min} min da te", "È a ~{min} min da dove sei."),
    },
    "plan": {
        "es": ("junto a tu {plan}", "Queda justo al lado de tu {plan}: te sirve para esperar o llegar con calma."),
        "en": ("next to your {plan}", "It's right next to your {plan}: perfect to wait or arrive relaxed."),
        "pt": ("junto ao seu {plan}", "Fica ao lado do seu {plan}: serve para esperar ou chegar tranquilo."),
        "fr": ("près de ton {plan}", "C'est juste à côté de ton {plan} : parfait pour attendre ou arriver tranquille."),
        "de": ("neben deinem {plan}", "Es liegt direkt neben deinem {plan}: ideal zum Warten oder entspannt Ankommen."),
        "it": ("vicino al tuo {plan}", "È proprio accanto al tuo {plan}: ideale per aspettare o arrivare con calma."),
    },
    "paso": {
        "es": ("de paso a tu {plan}", "Te queda DE PASO para tu {plan}: casi no te desvía."),
        "en": ("on the way to your {plan}", "It's ON THE WAY to your {plan}: barely a detour."),
        "pt": ("no caminho do seu {plan}", "Fica NO CAMINHO do seu {plan}: quase não desvia."),
        "fr": ("sur la route de ton {plan}", "C'est SUR LA ROUTE de ton {plan} : à peine un détour."),
        "de": ("auf dem Weg zu deinem {plan}", "Es liegt AUF DEM WEG zu deinem {plan}: kaum ein Umweg."),
        "it": ("di strada verso il tuo {plan}", "È DI STRADA verso il tuo {plan}: quasi nessuna deviazione."),
    },
}
_CHAT = {
    "es": "Veo que tienes un hueco hasta las {hasta} (tu {plan} es a las {hora}). Como marcaste que te gusta «{gusto}», aprovecha para ir a "
          "{name} ({zona}). {clave} {angulo} ¿Te cuento cómo llegar?",
    "en": "You have a gap until {hasta} (your {plan} is at {hora}). You said you like “{gusto}”, so make the most of it and go to "
          "{name} ({zona}). {clave} {angulo} Want directions?",
    "pt": "Você tem um intervalo até às {hasta} (seu {plan} é às {hora}). Como você marcou que gosta de «{gusto}», aproveite para ir a "
          "{name} ({zona}). {clave} {angulo} Quer saber como chegar?",
    "fr": "Tu as un créneau libre jusqu'à {hasta} (ton {plan} est à {hora}). Comme tu aimes « {gusto} », profites-en pour aller à "
          "{name} ({zona}). {clave} {angulo} Je t'explique comment y aller ?",
    "de": "Du hast eine Lücke bis {hasta} (dein {plan} ist um {hora}). Da du „{gusto}“ magst, nutze sie für {name} ({zona}). "
          "{clave} {angulo} Soll ich dir den Weg zeigen?",
    "it": "Hai un buco fino alle {hasta} (il tuo {plan} è alle {hora}). Visto che ti piace «{gusto}», approfittane per andare a "
          "{name} ({zona}). {clave} {angulo} Ti dico come arrivare?",
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
        fin = ini + timedelta(minutes=agenda.duracion(p))
        if ini <= ahora < fin:
            return None
        if ini > ahora:
            siguientes.append((ini, p))
    if not siguientes:
        return None
    ini, plan = min(siguientes, key=lambda x: x[0])
    return {"plan": plan, "inicio": ini, "libre_min": (ini - ahora).total_seconds() / 60}


def punto_de_partida(trip: dict, ahora: datetime, hoy: list[dict]) -> tuple[float, float] | None:
    """Dónde ESTARÁ el usuario: si acaba de terminar un plan con lugar conocido, ahí; si no, su ubicación actual."""
    for x in sorted(hoy, key=lambda x: x["plan_dt"], reverse=True):
        fin = x["plan_dt"] + timedelta(minutes=agenda.duracion(x["plan"]))
        if x["coords"] and timedelta(0) <= ahora - fin <= timedelta(minutes=RECIEN_TERMINADO_MIN):
            return x["coords"]
    return agenda.origen(trip) or geo.resolve_origin(trip)


def elegir(trip: dict, hueco: dict, ahora: datetime, planes: list[dict]) -> dict | None:
    """La mejor combinación para este hueco: {'lugar', 'gusto', 'min', 'angulo', 'vuelta'} o None."""
    gustos = [g for g in (trip.get("gustos") or []) if g]
    if not gustos:
        return None
    ciudad = trip.get("ciudad", "")
    lugares = db.places_for_city(ciudad)
    hoy = agenda.calcular(trip, ahora, planes)
    pos = punto_de_partida(trip, ahora, hoy)
    if not pos:
        return None
    sig = next((x for x in hoy if x["plan"] is hueco["plan"] or x["plan"].get("id") == hueco["plan"].get("id")), None)
    destino = sig["coords"] if sig else None
    ya = db.recently_recommended(trip["id"], hours=24 * 60)

    cats = estrategia._categorias_abiertas(gustos, ahora.hour + ahora.minute / 60, lugares)
    mejores = []
    for c in estrategia.candidatos(trip, ahora, cats, pos, hoy):
        p = c["p"]
        gusto = (c["calzan"] or c["debiles"] or [None])[0]
        if p["name"] in ya or not gusto:
            continue  # un aviso proactivo solo vale si es personal: tiene que calzar con algo que él marcó
        vuelta = (geo.travel_minutes(geo.haversine_km(p["lat"], p["lng"], destino[0], destino[1]), ciudad)
                  if destino else c["ida"])  # sin saber dónde es su siguiente plan: se asume que vuelve de donde vino
        directo = geo.travel_minutes(geo.haversine_km(pos[0], pos[1], destino[0], destino[1]), ciudad) if destino else 0
        if c["ida"] + c["visita"] + vuelta + MARGEN_MIN > hueco["libre_min"]:
            continue
        cerca_de_ti = c["ida"] <= CERCA_DE_TI_MIN
        junto_plan = bool(destino) and vuelta <= CERCA_DEL_PLAN_MIN
        de_paso = bool(destino) and max(0, c["ida"] + vuelta - directo) <= estrategia.DESVIO_DE_PASO_MIN
        puntaje = (c["calidad"] + (0.6 if c["calzan"] else 0) + 0.4 * cerca_de_ti + 0.4 * junto_plan + 0.5 * de_paso)
        # El ángulo que se le cuenta: el más específico que cumple (si solo es «cerca de ti», se dice eso).
        angulo = "plan" if (junto_plan and not cerca_de_ti) else "paso" if (de_paso and not cerca_de_ti) else "cerca"
        mejores.append((puntaje, -c["ida"], p["name"], p, gusto, c["ida"], angulo))
    if not mejores:
        return None
    mejores.sort(key=lambda x: x[:3], reverse=True)
    _, _, _, lugar, gusto, ida, angulo = mejores[0]
    return {"lugar": lugar, "gusto": gusto, "min": ida, "angulo": angulo}


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
        if not libre or db.recommended_within(tid, SEPARACION_MIN) or db.engagement_count(tid, hoy) >= watchers.ENGAGEMENT_MAX_DIA:
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
    corta, larga = _ANGULO[eleccion["angulo"]][idioma]
    datos = {"hasta": hueco["inicio"].strftime("%H:%M"), "hora": plan.get("hora") or "", "plan": plan.get("titulo") or "…",
             "gusto": gusto, "name": p["name"], "zona": p.get("zona", ""), "clave": clave, "min": eleccion["min"]}
    texto = " ".join(_CHAT[idioma].format(**datos, angulo=larga.format(**datos)).split())
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": texto})
    db.mark_recommended(tid, p["name"])
    # Gancho: el lugar + POR QUÉ ahora («☕ Tropicalia Coffee · de paso a tu tour»).
    gancho = f"{_EMOJI.get(p.get('category'), '📍')} {p['name']} · {corta.format(**datos)}"
    watchers._push_checkin(tid, _TITULO[idioma].format(**datos), texto, abrir="como_llegar", dest=p["name"], gancho=gancho)
