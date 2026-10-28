"""Razonamiento estratégico para CUALQUIER pedido de «ve a un lugar / qué hago»: el CÓDIGO decide y calcula, el modelo solo lo
redacta con naturalidad.

El razonamiento (es el mismo para un café, un museo, una vista, un parque, comprar algo o «tengo tiempo libre, ¿qué hago?»):
  1. ¿Qué quiere? → categorías de lugar (por lo que pide; si es abierto, por lo que marcó que le gusta en el onboarding).
  2. ¿Dónde está y cuánto tiempo tiene? → ubicación actual + tiempo hasta su próximo plan con hora (agenda.calcular).
  3. OPCIONES CERCA: 1-2 lugares abiertos a la hora en que llegaría, priorizando lo que calza con sus gustos.
  4. MEJOR EXPERIENCIA: si le sobra tiempo, el mejor lugar de la ciudad para eso que SÍ cabe (ir + visita + llegar a su próximo plan con
     margen), diciendo si le queda DE PASO para ese plan o si implica un desvío. Si no hay tiempo, NO se la propone.
Es aritmética (distancias, horarios, desvíos), así que se resuelve aquí con los datos reales de la curación y se le entrega al
modelo ya hecha (ver context.build): ningún modelo, por grande que sea, hace esa cuenta de forma fiable por su cuenta.
"""
import re
from datetime import datetime, timedelta

from . import agenda, db, geo, horarios, transporte, watchers

# (id, patrón del mensaje, categorías de lugar, etiqueta para el prompt, emoji). El primero que calce gana.
TEMAS = [
    ("cafe", re.compile(r"\b(caf[eé]s?|cafecito|coffee|tinto|espresso|capuchino|cappuccino|latte|cafeter[ií]as?)\b", re.I),
     {"cafe"}, "un café", "☕"),
    ("comida", re.compile(r"\b(almorzar|almuerzo|comer|cenar|cena|restaurantes?|hambre|lunch|dinner|eat|desayun\w*|brunch|antojo)\b", re.I),
     {"restaurante"}, "comer", "🍽️"),
    ("bar", re.compile(r"\b(tragos?|cervezas?|bares?|cocteles?|cocktails?|beers?|drinks?|rumba)\b", re.I),
     {"bar"}, "un trago", "🍸"),
    ("mirador", re.compile(r"\b(mirador(?:es)?|vistas?|atardecer|sunset|views?|panor[aá]mic\w*)\b", re.I),
     {"mirador"}, "una vista", "🌄"),
    ("parque", re.compile(r"\b(parques?|naturaleza|caminar|caminata|senderismo|aire libre|parks?|hike|walk)\b", re.I),
     {"parque"}, "algo al aire libre", "🌿"),
    ("compras", re.compile(r"\b(comprar|compras|souvenirs?|artesan[ií]as?|regalos?|mercados?|shopping|gifts?)\b", re.I),
     {"compras", "mercado"}, "comprar algo", "🛍️"),
    ("experiencia", re.compile(r"\b(tours?|experiencias?|grafiti|graffiti|talleres?|clases?|actividad(?:es)?|aventura|bicicleta|bici)\b", re.I),
     {"experiencia"}, "una experiencia", "✨"),
    ("cultura", re.compile(r"\b(museos?|museum|galer[ií]as?|historia|history|arte|cultura|catedral|iglesia|monumentos?|conocer|visitar|sightseeing|atracci[oó]n)\b", re.I),
     {"atraccion"}, "algo de cultura", "📍"),
]
# Pedido ABIERTO («qué hago», «tengo tiempo libre»): no pide un tipo; se decide por sus gustos y la hora.
ABIERTO = re.compile(
    r"(qu[eé] (?:hago|puedo hacer|me recomiendas|hay para hacer|hacemos)|algo (?:para|que) hacer|tiempo libre|me aburro|aburrid[oa]|"
    r"un plan\b|plan para|ideas?\b|what (?:should|can|do) i do|something to do|free time|bored|any ideas|recom[ié]ndame algo)", re.I)

CERCA_MIN = 15          # «cerca»: a esta distancia (min) o menos
VISITA_MIN = {"cafe": 45, "restaurante": 75, "bar": 60, "parque": 60, "mirador": 45, "mercado": 60, "compras": 45,
              "atraccion": 75, "experiencia": 90}
MARGEN_MIN = 15
DESVIO_DE_PASO_MIN = 15  # desvío extra respecto a ir directo al plan; menos que esto = «de paso»
MEJORA_MIN = 0.15        # cuánto mejor (puntaje) debe ser la «mejor experiencia» que la mejor cercana para valer el trayecto

# Las calificaciones de Google están saturadas (casi todo es 4.8-4.9) y no distinguen «la mejor experiencia». Lo que sí la
# distingue está en la ficha curada: prestigio comprobable y oferta de experiencia.
_PRESTIGIO = re.compile(r"#\s?\d+|del mundo|mundial|campe[oó]n|campeones|premi|ranking|el mejor|la mejor|imperdible|ic[oó]nic[oa]|patrimonio", re.I)
_EXPERIENCIA = re.compile(r"\bcata(?:s|ci[oó]n)?\b|talleres?|tostador|trabaja directo|origen(?:es)?|filtrad|experiencias?|guiad[oa]s?|\btour\b", re.I)


def tema_del_mensaje(mensaje: str):
    """(id, patrón, categorías, etiqueta, emoji) del tipo de lugar que pide, o None."""
    for t in TEMAS:
        if t[1].search(mensaje or ""):
            return t
    return None


def _categorias_abiertas(gustos: list[str], hora: float, lugares: list[dict]) -> set[str]:
    """Para un pedido abierto: las categorías de lo que le gusta (onboarding), por el mapeo gusto→categoría y por las etiquetas de los
    lugares; si no marcó nada, un menú variado. Comida y bares solo cuando corresponde a la hora."""
    cats: set[str] = set()
    for g in gustos:
        cats |= watchers._GUSTO_CATEGORIAS.get(g, set())
    cats |= {p.get("category") for p in lugares if set(p.get("tags") or []) & set(gustos)}
    cats = (cats or {"atraccion", "parque", "mirador", "cafe", "experiencia", "mercado"}) - {"excursion", "compras", None}
    if not (12 <= hora < 15 or 18.5 <= hora < 22):
        cats.discard("restaurante")
    if hora < 18:
        cats.discard("bar")
    return cats or {"atraccion", "cafe", "parque"}


# Lugares que solo existen un día («Ciclovía dominical»): no se ofrecen los demás días.
_SOLO_DOMINGO = re.compile(r"dominical|solo (?:los )?domingos|domingos (?:de|y festivos)", re.I)


def _no_es_hoy(p: dict, ahora: datetime) -> bool:
    texto = f"{p.get('name', '')} {(p.get('local') or {}).get('clave', '')}"
    return bool(_SOLO_DOMINGO.search(texto)) and ahora.weekday() != 6


def _calidad(p: dict) -> float:
    """Qué tan buena EXPERIENCIA es: calificación + dato local curado + prestigio + oferta de experiencia."""
    local = p.get("local") or {}
    texto = " ".join(str(local.get(k) or "") for k in ("clave", "dato", "pedir", "ver"))
    return (float(p.get("rating") or 0) + (0.4 if local.get("clave") else 0)
            + (1.0 if _PRESTIGIO.search(texto) else 0) + (0.4 if _EXPERIENCIA.search(texto) else 0))


def _clave(p: dict, n: int = 130) -> str:
    c = ((p.get("local") or {}).get("clave") or "").strip()
    return c if len(c) <= n else c[: n - 1].rsplit(" ", 1)[0] + "…"


def _abierto(p: dict, llegada: datetime, visita: int = 0) -> tuple[bool, str]:
    """(sirve, marca). Cerrado a esa hora, o que cierra antes de poder disfrutarlo → no sirve. Horario desconocido → sirve, sin marca."""
    e = horarios.estado(p.get("place_id"), llegada)
    if not e:
        return True, ""
    if not e["abierto"]:
        return False, ""
    if e["24h"] or not e.get("cierra"):
        return True, " · abierto 24 h" if e["24h"] else ""
    hh, mm = e["cierra"].split(":")
    cierre = llegada.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if cierre < llegada:
        cierre += timedelta(days=1)  # cierra pasada la medianoche
    if cierre < llegada + timedelta(minutes=visita * 0.7):
        return False, ""
    return True, f" · abierto hasta {e['cierra']}"


def candidatos(trip: dict, ahora: datetime, cats: set[str], pos: tuple[float, float], hoy: list[dict]) -> list[dict]:
    """Lugares curados de esas categorías, abiertos a la hora en que llegaría, con: ida (min), visita (min), calzan (gustos con ETIQUETA
    del lugar: la señal fuerte), debiles (gustos que solo calzan por categoría), calidad y marca de horario. Los lugares de SUS planes de
    hoy no entran (ya los tiene: sería «recomendarle» su propio tour). Lo usan el chat (bloque) y las recomendaciones por hueco (huecos)."""
    ciudad = trip.get("ciudad", "")
    gustos = [g for g in (trip.get("gustos") or []) if g]
    en_plan = {x["lugar"] for x in hoy if x["lugar"]}
    texto_planes = " ".join(f"{x['plan'].get('titulo', '')} {x['plan'].get('lugar') or ''}" for x in hoy).lower()
    cand = []
    for p in db.places_for_city(ciudad):
        if p.get("category") not in cats or p["name"] in en_plan or p["name"].lower() in texto_planes or _no_es_hoy(p, ahora):
            continue
        d = geo.haversine_km(pos[0], pos[1], p["lat"], p["lng"])
        if d is None:
            continue
        ida = geo.travel_minutes(d, ciudad)
        visita = VISITA_MIN.get(p["category"], 60)
        ok, marca = _abierto(p, ahora + timedelta(minutes=ida), visita)
        if not ok:
            continue
        # Lo que calza con sus gustos pesa: a igual calidad, gana lo que él marcó. La ETIQUETA del lugar es la señal fuerte; que solo
        # coincida la categoría es débil y no se presenta como «calza con» (una caminata no es «grafiti»).
        fuertes = sorted(set(p.get("tags") or []) & set(gustos))
        debiles = [g for g in watchers.gustos_que_calzan(p, gustos) if g not in fuertes]
        cand.append({"p": p, "ida": ida, "marca": marca, "visita": visita, "calzan": fuertes, "debiles": debiles,
                     "calidad": _calidad(p) + min(0.6, 0.3 * len(fuertes)) + min(0.2, 0.1 * len(debiles))})
    return cand


def bloque(trip: dict, mensaje: str, ahora: datetime, planes: list[dict] | None = None) -> str:
    """Bloque para el prompt: opciones cercas + (si hay tiempo) la mejor experiencia de paso. '' si no aplica."""
    tema = tema_del_mensaje(mensaje)
    gustos = [g for g in (trip.get("gustos") or []) if g]
    hora = ahora.hour + ahora.minute / 60
    if tema:
        _, _, cats, etiqueta, emoji = tema
    elif ABIERTO.search(mensaje or ""):
        cats, etiqueta, emoji = _categorias_abiertas(gustos, hora, db.places_for_city(trip.get("ciudad", ""))), "un plan", "✨"
    else:
        return ""
    pos = agenda.origen(trip) or geo.resolve_origin(trip)
    if not pos:
        return ""
    ciudad = trip.get("ciudad", "")
    # Si nombra un destino CONCRETO («cómo llego al Museo del Oro»), no es «recomiéndame»: es una ruta y la resuelve el bloque de rutas.
    if transporte.detectar_destino(mensaje, db.places_for_city(ciudad)) or transporte.destino_libre(mensaje):
        return ""
    # ¿Esto que pide es de lo que marcó que le gusta? (por etiqueta o por categoría)
    gustos_rel = [g for g in gustos if watchers._GUSTO_CATEGORIAS.get(g, set()) & cats]

    # Siguiente plan de hoy y cuánto tiempo hay hasta tener que estar ahí.
    hoy = agenda.calcular(trip, ahora, planes)
    sig = next((x for x in hoy if x["faltan_plan_min"] > 0), None)

    cand = candidatos(trip, ahora, cats, pos, hoy)
    if not cand:
        return ""

    # 1) Cerca: lo mejor entre lo cercano, con variedad de tipo si el pedido es abierto.
    cercanos = sorted((c for c in cand if c["ida"] <= CERCA_MIN), key=lambda c: (-round(c["calidad"], 1), c["ida"]))
    if not cercanos:
        cercanos = sorted(cand, key=lambda c: c["ida"])
    cerca = cercanos[:1]
    if len(cercanos) > 1:
        segundo = cercanos[1]
        if not tema:  # pedido abierto: que el segundo sea de otro tipo, para dar a elegir
            segundo = next((c for c in cercanos[1:] if c["p"]["category"] != cerca[0]["p"]["category"]), segundo)
        cerca.append(segundo)
    nombres_cerca = {c["p"]["name"] for c in cerca}

    # 2) Mejor experiencia: la de mayor calidad que cabe en el tiempo libre (y, si hay plan después, que le quede de paso).
    mejor, de_paso, libre = None, False, None
    mejor_cerca = max((c["calidad"] for c in cerca), default=0)
    for c in sorted(cand, key=lambda c: (-c["calidad"], c["ida"])):
        if c["calidad"] < mejor_cerca + MEJORA_MIN:
            break  # ordenado por calidad: ninguno de los que siguen sería mejor que la mejor cercana
        if c["p"]["name"] in nombres_cerca:
            continue
        if sig:
            destino = sig["coords"]
            vuelta = (geo.travel_minutes(geo.haversine_km(c["p"]["lat"], c["p"]["lng"], destino[0], destino[1]), ciudad)
                      if destino else c["ida"])
            directo = geo.travel_minutes(geo.haversine_km(pos[0], pos[1], destino[0], destino[1]), ciudad) if destino else 0
            if c["ida"] + c["visita"] + vuelta + MARGEN_MIN > sig["faltan_plan_min"]:
                continue
            mejor = {**c, "desvio": max(0, c["ida"] + vuelta - directo)}
            libre = round(sig["faltan_plan_min"])
        else:
            if c["ida"] > 25:
                continue
            mejor = {**c, "desvio": None}
        de_paso = mejor["desvio"] is not None and mejor["desvio"] <= DESVIO_DE_PASO_MIN
        break

    L = ["\n>>> ESTRATEGIA PARA ESTE PEDIDO (calculada con datos reales; razona con ella y respóndele natural, sin recitar números) <<<",
         f"El usuario busca {etiqueta}."]
    if gustos_rel:
        L.append("IMPORTANTE: en el onboarding marcó que le gusta " + ", ".join(f"«{g}»" for g in gustos_rel[:3]) +
                 ": es de lo que MÁS le importa del viaje. Trátalo así: no le des la primera opción que aparezca; cuéntale por qué "
                 "vale la pena PARA ÉL.")
    L.append(f"OPCIONES CERCA ({emoji}, a pie o en un trayecto corto):")
    for i, c in enumerate(cerca, 1):
        pl = c["p"]
        calza = f" [calza con: {', '.join(c['calzan'][:2])}]" if c["calzan"] else ""
        L.append(f"  {i}) {pl['name']} [{pl['category']}] ({pl.get('zona', '')}) — a ~{c['ida']} min{c['marca']}{calza}"
                 + (f" · {_clave(pl)}" if _clave(pl) else ""))
    if mejor:
        pl = mejor["p"]
        L.append(f"MEJOR EXPERIENCIA SI TIENE TIEMPO: {pl['name']} [{pl['category']}] ({pl.get('zona', '')}) — a ~{mejor['ida']} min"
                 f"{mejor['marca']}" + (f" · {_clave(pl)}" if _clave(pl) else ""))
        if sig:
            plan = sig["plan"]
            L.append(f"  Tiempo: faltan {libre} min para su «{plan.get('titulo')}» ({plan['hora']}). Ir a {pl['name']}, quedarse "
                     f"~{mejor['visita']} min y llegar a «{plan.get('titulo')}» cabe con margen. "
                     + ("Le queda DE PASO para ese plan: dilo así." if de_paso
                        else f"Implica un desvío de ~{mejor['desvio']} min: dilo con honestidad y que decida."))
        else:
            L.append("  No tiene un plan con hora pronto que lo apure: si no tiene afán, vale el trayecto.")
    elif sig:
        L.append(f"No propongas un lugar lejano: faltan {round(sig['faltan_plan_min'])} min para su «{sig['plan'].get('titulo')}» "
                 "y no hay una opción mejor que quepa con margen.")
    L.append("CÓMO RESPONDER (OBLIGATORIO para este pedido): tus opciones son SOLO las de este bloque, aunque la lista general de LUGARES "
             "traiga más. Da las OPCIONES CERCA (1-2) y, aparte, la MEJOR EXPERIENCIA solo si aparece arriba, con el razonamiento de "
             "tiempo («tienes X min libres antes de tu plan; esa queda de paso»). No afirmes que algo «queda de paso» ni des tiempos o "
             "distancias que no estén aquí; no recomiendes el lugar de uno de sus planes de hoy. Cierra con una pregunta corta "
             "(«¿te llevo a alguna?»).\n")
    return "\n".join(L)
