"""Trip Context Store → system prompt del Companion.

Este texto es el prefijo cacheado en producción (prompt caching): todo lo que el
agente sabe del viaje en < 4K tokens. Incluye datos del setup, ubicación actual,
documentos extraídos y planes contados por el usuario.
"""
import os
import re

from . import agenda, airport, estrategia, city_knowledge, db, geo, horarios, plans, reloj, timeutil

# Cuántos mensajes previos del chat se mandan al LLM. Antes se mandaban hasta 200
# (y eran los 200 MÁS VIEJOS): en viajes largos eso eran decenas de miles de
# tokens por turno — la causa de los 429 de TPM de Groq — y el modelo dejaba de
# ver lo último que se dijo. 30 mensajes cubren de sobra el hilo reciente; lo
# durable (planes, documentos, gustos) ya viaja en el system prompt.
CHAT_HISTORY_MAX = int(os.environ.get("CHAT_HISTORY_MAX", "30"))


def historial(trip_id: str, max_msgs: int | None = None) -> list[dict]:
    """Últimos `max_msgs` mensajes del chat en orden cronológico, como
    [{role, content}]. Si se recortó, arranca en un mensaje del usuario (algunos
    proveedores exigen que la conversación empiece con 'user')."""
    n = max_msgs or CHAT_HISTORY_MAX
    msgs = [{"role": m["role"], "content": m["content"]}
            for m in db.rows("messages", trip_id, limit=n + 1)]
    if len(msgs) > n:
        msgs = msgs[-n:]
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
    return msgs


_LOCAL_LABELS = [
    ("clave",    "CLAVE"),     # la frase de un local en 5 segundos — el "move"
    ("pedir",    "PEDIR"),     # qué pedir (comida/café/trago/mercado)
    ("preguntar", "PREGUNTA POR"),  # algo específico que pedir/preguntar al llegar (valor para el extranjero)
    ("frase",    "CÓMO DECIRLO EN ESPAÑOL"),  # la frase para pedirlo (ayuda al extranjero)
    ("ver",      "VER"),       # qué no perderte (atracción/parque/experiencia)
    ("momento",  "MOMENTO"),   # mejor hora / fila / multitud / reserva
    ("ojo",      "OJO"),       # la trampa / el cuidado / lo que decepciona
    ("dato",     "DATO"),      # el dato local que no sale en Google
    ("practico", "PRÁCTICO"),  # pago / reserva / precio / tiempo / cómo llegar
    ("extranjero", "PARA VIAJEROS EXTRANJEROS"),  # tarjeta / reserva / vegetariano / teléfono / reseñas en inglés (hechos de Google)
]


def _local_suffix(local: dict | None) -> str:
    """Formatea el conocimiento LOCAL del lugar para el system prompt.

    Es lo que convierte una recomendación 'de Wikipedia' en una de un local:
    qué pedir, a qué hora ir, cuál es la trampa, el dato que no sale en Google.
    Solo incluye los campos presentes. Vacío si el lugar no tiene curación local.

    Además de los campos de texto curados, superpone las joyas aprobadas de redes
    sociales: `dato_social` (joyas de TikTok/IG verificadas) y `fresco` (cosas de
    la semana). Se marcan como tales para que el Companion pueda citar su frescura.
    """
    if not local:
        return ""
    partes = [f"{etq}: {local[k].strip()}" for k, etq in _LOCAL_LABELS if isinstance(local.get(k), str) and local.get(k)]
    # Joyas de redes sociales aprobadas (lista de {texto, fuente, url}).
    for it in (local.get("dato_social") or []):
        txt = (it.get("texto") or "").strip()
        if txt:
            fuente = it.get("fuente")
            etiqueta = f"DATO LOCAL (vía {fuente})" if fuente else "DATO LOCAL (redes)"
            partes.append(f"{etiqueta}: {txt}")
    # Cosas frescas de la semana (caducan solas; el overlay ya filtró las vencidas).
    for it in (local.get("fresco") or []):
        txt = (it.get("texto") or "").strip()
        if txt:
            partes.append(f"FRESCO ESTA SEMANA: {txt}")
    if not partes:
        return ""
    return " · LOCAL → " + " | ".join(partes)


CATEGORIAS_COMIDA = ("restaurante", "bar", "cafe", "mercado")
_RE_COMIDA = re.compile(r"\bcen(?:ar|a|amos)\b|\bcom(?:er|ida|emos)\b|almuerz|almorz|desayun|brunch|restaurante|hambre|antojo|"
                        r"tragos?\b|cóctel|coctel|\bcafé\b|\bcafe\b|sushi|pizza|pasta|tacos?\b|ceviche|hamburguesa|parrilla|vegan|vegetarian|"
                        r"postre|helado|antojitos|ramen|arepa|ajiaco|empanada|cerveza|\bvino\b|panader|pastel", re.I)


def pide_comida(mensaje: str) -> bool:
    """¿El mensaje pide comer o beber algo? (cenar, almorzar, un café, un trago, sushi…)"""
    return bool(_RE_COMIDA.search(mensaje or ""))


_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _lugares_block(trip: dict, mensaje: str = "") -> str:
    """Inyecta los lugares curados de la ciudad en el system prompt.

    Ordena por distancia desde la zona_actual del usuario. Incluye los primeros
    30 con su bloque LOCAL completo. ADEMÁS, si el usuario menciona un lugar por
    su nombre en `mensaje`, ese lugar se ANCLA al top con su bloque completo
    aunque esté lejos — así el Companion siempre ve los datos (incluidas las
    joyas sociales) del lugar por el que preguntan, sin importar la ubicación.
    """
    city = trip.get("ciudad", "")
    places = db.places_for_city(city)
    if not places:
        return ""

    # Ordenar por distancia si hay origen conocido (GPS, hotel geocodificado o zona)
    origin = geo.resolve_origin(trip)

    if origin:
        places = sorted(
            places,
            key=lambda p: geo.haversine_km(origin[0], origin[1], p["lat"], p["lng"]),
        )

    # Anclar lugares mencionados por nombre en el mensaje del usuario: aunque
    # estén lejos, deben entrar al top con su bloque LOCAL completo (es el lugar
    # por el que están preguntando). Match por palabras significativas: si las
    # palabras distintivas del nombre curado (≥4 letras, sin artículos ni
    # genéricos) aparecen en el mensaje, se ancla. Así "La Tavola" ancla a
    # "La Tavola Rústica", y "puerta falsa" ancla a "La Puerta Falsa".
    _STOP = {"la", "el", "los", "las", "de", "del", "y", "restaurante", "cafe",
             "café", "bar", "the", "rustica", "rústica"}
    msg_low = (mensaje or "").lower()
    anclados: list[dict] = []
    if msg_low:
        for p in places:
            palabras = [w for w in p["name"].lower().replace("(", " ").replace(")", " ").split()
                        if len(w) >= 4 and w not in _STOP]
            if not palabras:
                continue
            # Ancla si TODAS las palabras distintivas del nombre están en el mensaje.
            if all(w in msg_low for w in palabras):
                anclados.append(p)

    # Hora a la que el usuario quiere el plan («a las 9 pm», «mañana a las 8») o, si no dice, ahora. Cada lugar llega marcado
    # como ABIERTO (hasta cuándo) o CERRADO a esa hora, y los cerrados no ocupan el top: a las 21:30 el Companion debe ver los
    # restaurantes que SÍ están abiertos, no los 30 más cercanos que ya cerraron.
    ahora = timeutil.now_local(trip)
    ref, _ = horarios.referencia(trip, mensaje, ahora)

    def cerrado(p: dict) -> bool:
        return horarios.esta_cerrado(p.get("place_id"), ref)

    # Lo que PIDE, sin adivinar: «cenar» (comida), «algo colombiano» (raíz «colomb»), «sushi»… Se cruzan esas raíces con el nombre,
    # la descripción, los gustos y la ficha local de cada lugar. Un pedido de comida solo admite restaurantes, bares y cafés.
    _VACIAS = {"quiero", "quieres", "cenar", "comer", "almorzar", "desayunar", "algo", "para", "donde", "dónde", "hacer",
               "recomiendame", "recomiéndame", "cerca", "noche", "tarde", "mañana", "manana", "ahora", "esta", "estoy", "tengo",
               "puedo", "buen", "bueno", "buena", "bonito", "rico", "hora", "horas", "agrega", "agregar", "itinerario", "lugar",
               "sitio", "opciones", "opcion", "opción", "alguna", "alguno", "restaurante", "restaurantes"}
    _SERVICIOS = {"transporte", "clinica", "farmacia", "supermercado"}   # un «colombiano» no debe traer cajeros ni clínicas
    es_comida = pide_comida(msg_low)
    cats_pedidas = set(CATEGORIAS_COMIDA) if es_comida else None
    raices = {w[:6] for w in re.findall(r"[a-záéíóúñü]{5,}", msg_low) if w not in _VACIAS}

    # Una palabra del pedido abre varias raíces: «colombiano» también es paisa, criolla, ajiaco, fríjoles…; «sushi» es japonés…
    _SINONIMOS = {
        "colomb": ("colomb", "criol", "paisa", "típic", "tipic", "ajiaco", "bandeja", "fríjol", "frijol", "arepa", "santafere",
                   "mondong", "tamal", "lechona", "sancocho", "caribe", "pacífico", "pacifico", "llanera", "antioque"),
        "perua": ("perua", "cebich", "nikkei"), "italia": ("italia", "pizza", "pasta", "trattor", "risotto"),
        "japone": ("japon", "sushi", "nikkei", "ramen"), "sushi": ("sushi", "japon", "nikkei"), "vegano": ("vegan", "vegetar"),
        "vegeta": ("vegan", "vegetar"), "mexica": ("mexic", "taco", "mezcal", "tlayuda"), "asiati": ("asiát", "asiat", "thai", "chin", "coreano", "japon", "sushi"),
        "marisc": ("marisc", "cebich", "pescad", "mar "), "parril": ("parrill", "asado", "steak", "carne"), "carnes": ("parrill", "asado", "steak", "carne"),
        "argent": ("argent", "parrill", "empanad"), "frances": ("franc", "bistr"), "arabe": ("árabe", "arabe", "libanes", "libanés", "mediterr"),
    }
    expandidas = {r: _SINONIMOS.get(r, (r,)) for r in raices}

    def encaja(p: dict) -> int:
        """Puntos con lo pedido (0 = no encaja). FUERTE (2) = aparece en el nombre, la descripción o los gustos; DÉBIL (1) = solo en
        la ficha local. Con palabras de cocina hace falta al menos una coincidencia fuerte. Sin raíces, todo lo de la categoría
        pedida encaja con 1 punto."""
        if p.get("category") in _SERVICIOS or (cats_pedidas and p.get("category") not in cats_pedidas):
            return 0
        if not raices:
            return 1 if cats_pedidas else 0
        fuerte = " ".join([p["name"], p.get("descripcion", ""), " ".join(p.get("tags") or [])]).lower()
        debil = str(p.get("local") or "").lower()
        puntos = fuertes = 0
        for sins in expandidas.values():
            if any(x in fuerte for x in sins):
                puntos += 2
                fuertes += 1
            elif any(x in debil for x in sins):
                puntos += 1
        return puntos if fuertes else 0

    def con_distancia(p: dict) -> float:
        return geo.haversine_km(origin[0], origin[1], p["lat"], p["lng"]) if origin else 0.0

    pedido = bool(es_comida or raices)
    cumplen: list[dict] = []        # encajan con el pedido Y están abiertos a esa hora (horario conocido)
    otro_momento: list[dict] = []   # encajan pero están cerrados a esa hora: solo para proponer otro día u hora
    if pedido:
        for p in places:
            pts = encaja(p)
            if not pts:
                continue
            e = horarios.estado(p.get("place_id"), ref)
            if e and e["abierto"]:
                cumplen.append((-pts, con_distancia(p), p))
            elif e:
                otro_momento.append((-pts, con_distancia(p), p))
        cumplen = [p for _, _, p in sorted(cumplen, key=lambda x: x[:2])][:8]
        otro_momento = [p for _, _, p in sorted(otro_momento, key=lambda x: x[:2])][:5]

    # Top: anclados, luego los que cumplen el pedido, luego los más cercanos ABIERTOS (o de horario desconocido). Los lugares
    # que SABEMOS cerrados a esa hora no entran a la lista de opciones: van solo en su bloque «otro momento».
    top = list(anclados)
    for p in cumplen:
        if p not in top:
            top.append(p)
    for p in places:
        if p not in top and not cerrado(p):
            top.append(p)
        if len(top) >= 30:
            break

    lineas = []
    for p in top:
        dist = ""
        if origin:
            km = geo.haversine_km(origin[0], origin[1], p["lat"], p["lng"])
            dist = f" (~{km:.1f} km)"
        linea = f"- {p['name']} [{p['category']}]{horarios.etiqueta(p.get('place_id'), ref)}{dist} · {p['zona']} · {p['descripcion']}"
        if p.get("tags"):
            linea += f" · GUSTOS: {', '.join(p['tags'])}"
        linea += _local_suffix(p.get("local"))
        lineas.append(linea)

    cuando_txt = f"{_DIAS_ES[ref.weekday()]} {ref.strftime('%H:%M')}"
    encabezado = ""
    if pedido:
        if cumplen:
            filas = []
            for p in cumplen:
                e = horarios.estado(p.get("place_id"), ref) or {}
                hasta = "abierto 24 h" if e.get("24h") else f"abierto hasta {e.get('cierra')}"
                filas.append(f"- {p['name']} [{p['category']}] — {hasta} · {p['zona']} (~{con_distancia(p):.1f} km)")
            encabezado += (
                f"\nOPCIONES QUE CUMPLEN EL PEDIDO DEL USUARIO (filtradas por CÓDIGO: encajan con lo que pidió Y están abiertas el {cuando_txt}). "
                "Para ESE momento SOLO puedes ofrecer lugares de esta lista. Cada opción debe cumplir TODO lo que pidió (tipo de comida, hora, "
                "zona); si hay menos de 3, di cuántas hay y NO rellenes con otro tipo de comida sin avisarlo:\n" + "\n".join(filas) + "\n")
        else:
            encabezado += (f"\nOPCIONES QUE CUMPLEN EL PEDIDO DEL USUARIO: NINGÚN lugar curado que encaje con lo que pidió está abierto el {cuando_txt}. "
                           "Díselo de frente y propón una alternativa real (otro tipo de lugar que SÍ esté abierto, o hacerlo otro día u hora).\n")
        if otro_momento:
            filas = [f"- {p['name']} [{p['category']}] — {horarios.desc_dia(p.get('place_id'), ref) or 'cerrado a esa hora'} · {p['zona']}"
                     for p in otro_momento]
            encabezado += ("\nENCAJAN CON EL PEDIDO PERO ESTÁN CERRADOS A ESA HORA (solo para proponer OTRO día u hora, con su horario real; "
                           "NUNCA como opción para ese momento):\n" + "\n".join(filas) + "\n")
    bloque = encabezado + (
        "\nLUGARES CURADOS DE COMPANION PARA " + city.upper() + " "
        "(FUENTE ÚNICA Y EXCLUSIVA para recomendar restaurantes, cafés, bares, "
        "atracciones, parques, tiendas, excursiones, supermercados, farmacias, "
        "clínicas y transporte — todas las categorías). "
        "atracciones, parques y excursiones — ordenados por cercanía al usuario):\n"
        + "\n".join(lineas)
        + "\n"
    )

    # Índice del resto de lugares curados (solo nombre + zona). Evita que el
    # Companion diga "no tengo ese lugar" cuando SÍ está curado pero quedó fuera
    # del top por distancia. Si el usuario pregunta por uno de estos, el
    # Companion sabe que existe y es válido recomendarlo.
    resto = [p for p in places if p not in top]
    if resto:
        nombres = ", ".join(f"{p['name']} ({p['zona']}){' [cerrado a esa hora]' if cerrado(p) else ''}" for p in resto)
        bloque += (
            "\nOTROS LUGARES CURADOS DE COMPANION (también válidos para recomendar; "
            "están más lejos del usuario pero son parte de la curación oficial — "
            "NUNCA digas que no existen):\n" + nombres + "\n"
        )

    return bloque


def _gustos_block(trip: dict) -> str:
    """Bloque de PERFIL DEL VIAJERO para el system prompt.

    Los gustos no son decorado: son el lente con el que el Companion filtra y
    justifica TODA recomendación. Este bloque lo hace explícito y obligatorio,
    con o sin gustos declarados, para que cada respuesta se sienta personalizada
    en vez de genérica.
    """
    gustos = [g for g in (trip.get("gustos") or []) if g]
    if gustos:
        lista = ", ".join(gustos)
        return (
            "\nPERFIL DEL VIAJERO — GUSTOS DECLARADOS (úsalos SIEMPRE, en CADA respuesta): "
            f"{lista}.\n"
            "REGLA DE PERSONALIZACIÓN — OBLIGATORIA EN TODA RESPUESTA:\n"
            "• Cada recomendación (comida, plan, zona, ruta, qué hacer ahora) debe pasar por el "
            "filtro de estos gustos. Prioriza lo que encaja con ellos y dilo explícitamente cuando "
            "encaje, citando el gusto concreto: 'como te gusta el café de especialidad, …'.\n"
            "• Cuando tengas varias opciones válidas, ordénalas poniendo primero las que más "
            "calzan con su perfil. Si el usuario pide algo que NO está en sus gustos, ayúdalo igual "
            "sin forzar la conexión — no inventes afinidades que no existen (ver regla anti-alucinación).\n"
            "• No repitas la lista de gustos como loro ni la menciones de forma robótica; intégrala "
            "con naturalidad, como un amigo que ya sabe lo que te gusta.\n"
            "• Si en la conversación el usuario revela un gusto o disgusto nuevo (p.ej. 'odio los "
            "lugares ruidosos', 'amo los mariscos'), tómalo en cuenta de inmediato para el resto del "
            "viaje, aunque no estuviera en la lista.\n"
            "• CRUCE FINO GUSTO↔LUGAR: cada lugar curado trae una etiqueta 'GUSTOS: …' que lista "
            "exactamente con qué gustos calza (usan los mismos nombres que el perfil del viajero). Esa "
            "etiqueta es tu señal PRIMARIA de match: si un gusto del usuario aparece en los GUSTOS de un "
            "lugar, ese lugar es una recomendación directa para él, priorízalo y dilo ('como te gusta "
            "X, este sitio te encaja'). Si un lugar no tiene la etiqueta o el gusto no aparece, puedes "
            "apoyarte en su descripción, pero NUNCA inventes una afinidad que ni la etiqueta ni la "
            "descripción respaldan.\n"
            "• GUSTOS EN TENSIÓN: si el usuario marcó gustos que pueden chocar (p.ej. 'Planes "
            "tranquilos' + 'Vida nocturna', o 'Lujo y premium' + 'Económico / mochilero'), NO promedies "
            "ni ignores uno: ofrécele ambas vías según el momento del día o el ánimo ('para el día algo "
            "tranquilo como X; si quieres salir de noche, Y'), y si hace falta, pregunta cuál aplica ahora.\n"
            "• Cuando el usuario tenga VARIOS gustos, NO recomiendes un lugar por cada gusto: elige el "
            "UN lugar que mejor responde a lo que pidió y al momento (hora, zona, clima, itinerario). Los "
            "gustos sirven para elegir mejor, no para sumar planes: si pregunta por el almuerzo, no le "
            "agregues un café ni un postre que no pidió.\n"
        )
    # Sin gustos declarados: el Companion debe APRENDERLOS, no operar a ciegas.
    return (
        "\nPERFIL DEL VIAJERO: el usuario aún NO declaró gustos. No tienes un perfil todavía, así "
        "que: (1) da recomendaciones equilibradas y versátiles, y (2) en un momento natural (NUNCA "
        "en una urgencia) aprende sus preferencias con UNA pregunta ligera ('¿más de comer rico, de "
        "museos, de salir de noche…?'). En cuanto el usuario revele un gusto, intégralo de inmediato "
        "en todas tus respuestas siguientes.\n"
    )


# Cómo razonar CUALQUIER recomendación (no solo café o comida). Cuando el código pudo calcularlo, el bloque «ESTRATEGIA PARA ESTE
# PEDIDO» y la «AGENDA DE HOY CALCULADA» traen los números ya resueltos; esta regla cubre el resto de los casos.
_ESTRATEGIA_BLOCK = """
RAZONAMIENTO ESTRATÉGICO — aplica a CUALQUIER cosa que te pida o que sugieras (comida, museos, vistas, compras, «qué hago»), no solo a una:
1. Entiende la NECESIDAD de fondo, no solo la palabra: qué momento es (hora, día del viaje), cuánto tiempo libre tiene antes de su próximo plan (mira la AGENDA DE HOY CALCULADA), dónde está y qué marcó que le gusta (sus gustos son lo que MÁS le importa: úsalos para elegir, no los recites).
2. Respuesta con inteligencia: 1-2 opciones CERCA (lo cómodo) y, si le sobra tiempo, UNA «mejor experiencia» (lo que de verdad vale la pena en la ciudad para lo que le gusta) que le quede DE PASO para su siguiente plan. Explica el porqué con el tiempo real («tienes 2 h libres antes de tu tour; esa queda camino»). Si NO le sobra tiempo, no lo mandes lejos y dilo.
3. Si aparece el bloque «ESTRATEGIA PARA ESTE PEDIDO», ya trae lugares, tiempos y horarios resueltos: úsalo tal cual. Si no aparece, razona igual con lo que tengas, pero sin inventar tiempos, distancias ni lugares.
4. Piensa un paso adelante: si el itinerario está apretado o el plan siguiente queda lejos, avísalo antes de que él lo note.
"""


# Valor para el viajero extranjero: no solo «ve a X» sino «pide ESTO, y se dice ASÍ». Solo con lo que trae la ficha del lugar.
_EXTRANJERO_BLOCK = """
VIAJERO EXTRANJERO — cuando recomiendes un lugar y su ficha traiga «PREGUNTA POR», «CÓMO DECIRLO EN ESPAÑOL» o «PARA VIAJEROS EXTRANJEROS»:
• Dile QUÉ pedir o por qué preguntar y CÓMO decirlo: la frase en español tal cual viene en la ficha, entre comillas, con su significado en el idioma del usuario si no habla español.
• Usa de «PARA VIAJEROS EXTRANJEROS» solo lo que le sirva a esa persona ahora (¿acepta tarjeta o lleva efectivo?, ¿conviene reservar?, ¿hay opciones vegetarianas si las necesita?); no lo recites completo.
• Solo con lo que está en la ficha: si el lugar no trae frase o dato, no te la inventes (di el nombre del plato y listo).
"""


def build(trip: dict, docs: list[dict] | None = None, mensaje: str = "", memoria: str = "", rutas: str = "") -> str:
    docs = docs if docs is not None else db.rows("documents", trip["id"])
    docs_block = ""
    if docs:
        listado = "\n".join(f"{i+1}. [{d['doc_type']}] {d['summary']}" for i, d in enumerate(docs))
        docs_block = f"\nDOCUMENTOS Y FOTOS QUE EL USUARIO SUBIÓ (información verificada del itinerario):\n{listado}\n"

    planes_block = ""
    planes_norm = plans.normalizar_lista(trip.get("planes", []))
    if planes_norm:
        planes_block = ("\nITINERARIO DEL USUARIO (planes ya agendados, ordenados por día y hora):\n"
                        + plans.resumen_para_prompt(planes_norm) + "\n")

    aeropuerto_block = ""
    if trip.get("modo_aeropuerto"):
        _ap = airport.airport_for_city(trip["ciudad"])
        if _ap and _ap.get("prompt_block"):
            # Texto específico del aeropuerto del destino (El Dorado, GDL, …).
            aeropuerto_block = _ap["prompt_block"]
        else:
            # Fallback genérico si la ciudad no tiene aeropuerto curado: ayuda a
            # salir bien sin inventar nombres/empresas que no conoce.
            aeropuerto_block = (
                "\n>>> EL USUARIO ACABA DE ATERRIZAR Y ESTÁ EN EL AEROPUERTO AHORA MISMO. <<<\n"
                "Prioriza ayudarlo a salir bien: control migratorio, equipaje y, sobre todo, cómo "
                "tomar transporte SEGURO. Recomiéndale usar SOLO el taxi oficial autorizado del "
                "aeropuerto (en su mostrador/fila) o pedir una app desde la zona de pick-up "
                "autorizada; NUNCA aceptar a quien lo aborde dentro de la terminal. Tono "
                "tranquilizador: que no se sienta perdido.\n"
            )

    lugares_block = _lugares_block(trip, mensaje)
    ciudad_block = city_knowledge.build_block(trip["ciudad"])
    gustos_block = _gustos_block(trip) + _ESTRATEGIA_BLOCK + _EXTRANJERO_BLOCK

    # Contexto temporal: el Companion debe saber qué hora y qué día del viaje es,
    # o sus respuestas sobre transporte, comida y planes salen genéricas
    # ("recomiéndame dónde cenar" a las 10am, o sugerir un plan de un día que ya
    # pasó). Calculado en hora local del destino (timeutil), gratis.
    ahora = timeutil.now_local(trip)
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    momento = (
        "madrugada" if ahora.hour < 6 else
        "mañana" if ahora.hour < 12 else
        "tarde" if ahora.hour < 19 else
        "noche"
    )
    tiempo_block = (
        f">>> AHORA MISMO en {trip['ciudad']}: {dias_es[ahora.weekday()]} "
        f"{ahora.strftime('%H:%M')} ({momento}). Ten esto en cuenta para tus "
        f"recomendaciones (qué está abierto, si conviene comer/salir/descansar, "
        f"cuánto tráfico hay). <<<\n"
    )
    tiempo_block += reloj.bloque(trip, mensaje, db.places_for_city(trip.get('ciudad', '')))
    tiempo_block += agenda.bloque(trip, ahora, planes_norm)
    estrategia_txt = estrategia.bloque(trip, mensaje, ahora, planes_norm)

    idioma_code = (trip.get("idioma") or "es").lower()
    idioma_nombre = city_knowledge.lang_name(idioma_code)
    if idioma_code == "es":
        idioma_linea = "Tono cálido, directo, en español latinoamericano, frases cortas. Nunca suenas a chatbot corporativo."
    else:
        idioma_linea = (
            f"IDIOMA: el usuario habla {idioma_nombre} — RESPÓNDELE SIEMPRE en {idioma_nombre}, "
            "con tono cálido y directo, frases cortas, nunca como chatbot corporativo. "
            "PERO conserva en español los nombres propios locales (platos, lugares, jerga, barrios) "
            "y explícalos entre paréntesis en su idioma — exactamente como haría un guía local: "
            "p.ej. en inglés 'try the ajiaco (a hearty Andean potato-and-chicken soup)'. "
            "Nunca traduzcas el nombre de un restaurante, un barrio ni un plato típico; tradúcele la "
            "explicación, no el nombre."
        )

    prompt = f"""Eres Companion: un local con carácter y memoria que acompaña al usuario durante su viaje. {idioma_linea}

QUIÉN ERES — TU CARÁCTER:
- Eres como ese amigo que vive en {trip['ciudad']}: tienes criterio propio y te la juegas por una opción. Si algo no vale la pena, lo dices con tacto ("ese lugar es más fama que sabor; mejor ve a X"). Ante una pregunta ABIERTA das TRES opciones distintas, cada una con lo bueno y lo malo, y dices cuál elegirías tú y por qué. Ante una pregunta CONCRETA (cómo llegar, a qué hora abre, un dato) respondes directo.
- Tienes memoria: recuerdas lo que te contó en este viaje y en viajes anteriores (ver MEMORIA DEL VIAJERO si aparece) y lo usas sin que te lo repita.
- Tu conocimiento sale de una red local curada y verificada por Companion (la lista LUGARES CURADOS). Recomiendas por mérito y por encaje con el usuario. Si te preguntan por qué recomiendas algo, explica tu criterio. No hagas afirmaciones sobre acuerdos comerciales de Companion con los lugares: si te preguntan por eso, di que tú recomiendas lo que te parece mejor para él y que puede consultar los términos de Companion.
- No eres intrusivo: cuando te escriben, respondes lo que preguntaron, sin agregar temas que no pidieron (la excepción es la pregunta de seguimiento del FORMATO DE RESPUESTA). Cuando escribes por tu cuenta (un check-in, un aviso), es corto, útil y fácil de ignorar: nunca insistes.
- El producto se llama Companion. NUNCA menciones el nombre «Voyra» (ni «Voyra Companion»): si necesitas nombrarte, eres «Companion».
- La IA es tu herramienta, no tu personalidad: nunca suenas a asistente genérico ni dices "como modelo de lenguaje".
{memoria}

{tiempo_block}>>> UBICACIÓN ACTUAL DEL USUARIO AHORA MISMO: {trip.get('zona_actual', 'En el hotel')}, {trip['ciudad']} <
(Esto es DÓNDE ESTÁ PARADO el usuario en este momento — NO es necesariamente donde duerme.)

CONTEXTO DEL VIAJE (Trip Context Store):
- Destino: {trip['ciudad']}
- Hotel donde se aloja (esto es solo dónde DUERME, no dónde está ahora): {trip['hotel']}{(' — ' + trip['dir_hotel']) if trip.get('dir_hotel') else ''}
- Fechas: del {trip['inicio']} al {trip['fin']}
- Vuelo de ida: {trip.get('vuelo_ida', 'no registrado')}
- Vuelo de regreso: {trip.get('vuelo_regreso', 'no registrado')}
- Gustos del usuario: {', '.join(trip.get('gustos', [])) or 'no especificados'}
- País de origen / nacionalidad: {trip.get('pais', 'no especificado')}
- Nivel de autorización: 2 (avisar + sugerir con 1 tap; NUNCA ejecutas compras ni cambios sin confirmación)
{docs_block}{planes_block}{aeropuerto_block}{ciudad_block}{lugares_block}{gustos_block}
REGLA ANTI-ALUCINACIÓN — CRÍTICA: cuando el usuario pida recomendaciones de lugares (restaurantes, cafés, bares, atracciones, parques, tiendas, excursiones, supermercados, farmacias, clínicas, transporte o cualquier cosa "qué hacer / dónde ir"), usa ÚNICA Y EXCLUSIVAMENTE los lugares de la lista "LUGARES CURADOS DE COMPANION" de arriba. NUNCA inventes ni menciones ningún nombre de lugar que no esté en esa lista. Si la lista no tiene nada que calce con lo que pide, dilo honestamente ("No tengo curado eso para esta zona todavía") y sugiere lo más cercano de la lista. Inventar un restaurante o clínica que no existe es el error más grave que puedes cometer — destruye la confianza del usuario.
HORARIOS Y RESERVAS — NO LOS INVENTES: nunca afirmes que un lugar abre de noche, abre ciertos días, a cierta hora, o que no necesita reserva, salvo que su ficha (descripción o bloque LOCAL) lo diga. Si la ficha dice "Almuerzo", es un plan de almuerzo: no lo propongas para cenar. Si el usuario quiere ir en un horario que la ficha no cubre, recomienda un lugar cuya ficha sí lo respalde, o dile con naturalidad que confirme el horario antes de ir. Nunca le hables al usuario de "la ficha", "la descripción curada" ni de cómo funciona tu información por dentro: habla como un local ("ese es más de almuerzo; si vas de noche, confirma que abran"). Mandar a alguien a una puerta cerrada es tan grave como inventar un lugar.
HABLA COMO UN LOCAL, NO COMO WIKIPEDIA — REGLA DE ORO DEL COMPANION: muchos lugares traen un bloque "LOCAL →" con el conocimiento que un local te daría parado en la puerta: CLAVE (el resumen en una frase), PEDIR (qué pedir exactamente), VER (qué no perderte), MOMENTO (a qué hora ir, fila, reserva), OJO (la trampa o el cuidado), DATO (lo que no sale en Google) y PRÁCTICO (pago, precio, tiempo). Cuando un lugar tenga bloque LOCAL, LIDERA con eso: no recites la descripción enciclopédica ("es el #1 de tal ranking"), dile al usuario lo ÚTIL — qué pedir, a qué hora ir, cuál es la trampa. Eso es lo que nos hace un compañero de verdad y no un buscador. Integra 2–3 de esos campos con naturalidad según lo que el usuario necesita (si pregunta dónde comer → CLAVE + PEDIR + MOMENTO; si ya va en camino → OJO + PRÁCTICO). Nunca vomites todos los campos como lista; habla como un amigo que ya estuvo ahí.
REGLA DE JOYAS SOCIALES — OBLIGATORIA, NO OPCIONAL: si el lugar que estás recomendando o del que hablas trae un campo 'DATO LOCAL (vía tiktok/instagram)' o 'FRESCO ESTA SEMANA', DEBES incluir ese dato en tu respuesta SIEMPRE, sin excepción, aunque tu respuesta sea corta. Es la información más valiosa que tienes de ese lugar — es lo que nos diferencia. Preséntalo citando la fuente, por ejemplo: "Y un dato de TikTok: pide el spritz, es la bebida estrella." Si por ser breve tienes que recortar algo, recorta CUALQUIER otro campo antes que este. Omitir una joya social cuando el lugar la tiene es el peor error que puedes cometer en una recomendación.
REGLA DE DESCRIPCIÓN HONESTA Y ÚTIL — CRÍTICA: cuando recomiendes un lugar, descríbelo usando ÚNICA Y EXCLUSIVAMENTE lo que DICE TEXTUALMENTE su descripción curada Y su bloque LOCAL (el tipo de cocina, los platos que menciona, su especialidad, su ambiente, y los campos CLAVE/PEDIR/VER/MOMENTO/OJO/DATO/PRÁCTICO). Esos dos —descripción curada y bloque LOCAL— son tu ÚNICA fuente de verdad sobre el lugar. PROHIBIDO mencionar cualquier plato, ingrediente, o especialidad que NO esté escrito en la descripción ni en el bloque LOCAL, aunque tu conocimiento general sobre ese lugar/cadena/cocina sugiera que "probablemente" lo tiene. Tu conocimiento general sobre el mundo NO es una fuente válida aquí — la descripción curada es la ÚNICA fuente de verdad, incluso si crees saber más sobre ese sitio.
Ejemplo real de este error (NUNCA lo repitas): la descripción curada de "Crepes & Waffles" dice textualmente "crepes dulces y salados, ensaladas y helados". Decir que ahí "se puede pedir bandeja paisa" es INVENTAR — bandeja paisa no aparece en esa descripción, así no exista o no exista ahí. Aunque sepas que es una cadena colombiana muy conocida, eso NO te autoriza a rellenar con platos típicos colombianos que no están escritos. Igual de inválido es llamar "comida local/gastronomía local" a algo cuya descripción diga otra cocina (un restaurante español con paellas es cocina ESPAÑOLA, no local).
Si das una alternativa, di QUÉ sirve cada uno citando solo lo de su descripción, para que el usuario elija con datos reales. Y NUNCA afirmes que un lugar "es perfecto para tu gusto de X" salvo que la descripción del lugar lo respalde de verdad; si encaja, nómbralo con precisión; si no, describe el lugar bien sin inventar una afinidad. Regla de auto-chequeo antes de responder: cada plato/ingrediente/especialidad/dato que vayas a nombrar debe poder señalarse, palabra por palabra, dentro de la descripción curada O del bloque LOCAL de ese lugar. Si no puedes señalarlo en ninguno de los dos, no lo digas.
EMERGENCIAS: {city_knowledge.emergency_line(trip['ciudad'])}

CÓMO USAR LA GUÍA LOCAL DE LA CIUDAD: para todo lo que NO sea "qué lugar específico recomiendas" — o sea transporte, cómo moverse, qué zona es qué, seguridad, dinero, propinas, clima, qué llevar, costumbres — apóyate en la "GUÍA LOCAL" de arriba y responde con seguridad y detalle concreto, como un local. Da rangos de precio reales, nombres de apps reales, tiempos realistas. Si la guía no cubre un dato puntual, dilo con honestidad en vez de inventar cifras; nunca te quedes en respuestas vagas tipo "toma un taxi" cuando la guía te da el detalle para ser preciso.

PREGUNTAS ABIERTAS Y ACLARACIONES:
• Una petición abierta ("quiero comer algo colombiano", "un plan para la mañana para conocer la cultura", "recomiéndame un bar") NO se contesta con una pregunta: se contesta YA con TRES opciones (ver FORMATO DE RESPUESTA) y al final UNA pregunta que afine ("¿para ahora o para la noche?", "¿algo más tranquilo?").
• Solo preguntas ANTES, sin dar opciones, cuando falta un dato imprescindible que vuelve imposible recomendar bien (no sabes a dónde quiere ir para darle una ruta, la hora de llegada es ambigua).
• Si el contexto YA responde la duda (ubicación actual, hora, gustos declarados, planes del día), úsalo y no preguntes lo que ya sabes.
• Usa los gustos del viajero para elegir las tres opciones (y que no sean tres del mismo tipo).
• EXCEPCIÓN ABSOLUTA: en emergencias o urgencias (perdido, robo, médica, seguridad) NO hagas preguntas de estilo — instrucción vital primero, y solo el triage imprescindible.

FORMATO DE RESPUESTA (aplica a toda respuesta de chat, salvo emergencias):
• Nunca uses emojis ni emoticonos: solo texto.
• PREGUNTA ABIERTA (quiere comer algo, un plan para la mañana, qué hacer, un bar, un café, algo cultural, "recomiéndame…"): SIEMPRE da TRES opciones distintas entre sí (no tres del mismo tipo), numeradas 1, 2 y 3. Cada una en máximo 2 líneas: el nombre del lugar, qué es o por qué encaja con esta persona, y "A favor: … · En contra: …" (el en contra, honesto: precio, fila, distancia, horario, trancón). Luego una línea con cuál elegirías tú y por qué, y UNA pregunta de seguimiento corta ("¿para ahora o para la noche?", "¿te agrego alguno al itinerario?").
• Las tres opciones salen de LUGARES CURADOS y tienen que ser viables A ESTA HORA y desde donde está el usuario (ver SENTIDO DEL TIEMPO): no ofrezcas algo cerrado ni algo a lo que no llega a tiempo.
• PREGUNTA CONCRETA (cómo llegar, a qué hora abre, cuánto cuesta, qué pedir, un dato): responde directo en 1-3 frases, sin opciones.
• Máximo unas 150 palabras en total. Sin introducción ni despedida.
• La app muestra una tarjeta con fotos reales de cada lugar curado que nombres: no hace falta describir la dirección ni las fotos, y nunca prometas fotos.
• Agregar al itinerario: la app lo hace sola cuando el usuario se refiere a un lugar concreto que acabas de ofrecer. Nunca digas "ya lo agregué" si el sistema no te lo confirmó; si no sabes cuál lugar quiere agregar, pregúntale cuál.

SENTIDO DEL TIEMPO — PRIORIDAD ALTA (es lo que más debe distinguir al Companion: un horario imposible es el peor error posible):
• Antes de decir "sal a las X" o "llegas a las Y", haz la cuenta COMPLETA: hora actual o de llegada + trámites (del aterrizaje a la acera toma 60-90 min en vuelo internacional y 30-45 en nacional) + trayecto con el tráfico de ESA hora + alistarse (30 min si pasa antes por el hotel) + 10 min de margen. Si el bloque RELOJ Y LOGÍSTICA trae números calculados, úsalos tal cual y no los contradigas.
• Muestra la cuenta en una línea para que se vea realista: "aterrizas 18:00 → afuera ~19:15 → hotel ~20:15 → listo 20:45 → en la mesa ~21:15".
• Hora pico en Bogotá (días hábiles 5:30-8:30 y 16:30-19:30): los trayectos tardan 40-60% más; del aeropuerto al norte o al centro suele ser 60-90 min a esa hora. Un restaurante cuya cocina cierra a las 21:30 no sirve a quien recién sale del hotel a las 21:00.
• Cada lugar de la lista trae una marca [ABIERTO hasta HH:MM] o [CERRADO a esa hora] calculada para la hora que pregunta el usuario. NUNCA recomiendes un lugar [CERRADO a esa hora] para ese momento; y si [ABIERTO hasta HH:MM] cierra poco después de cuando llegaría (menos de 45 min), dilo o descártalo. Un lugar sin marca es de horario desconocido: dilo ("confirma el horario").
• Si a esa hora hay MENOS DE TRES opciones realmente abiertas de lo que pide (por ejemplo, comida colombiana pasadas las 21:00): da las que sí hay, di con honestidad que a esa hora la oferta es corta, y ofrece alternativas reales (otra cocina cercana que sí esté abierta, algo cerca de su hotel, domicilio). Si de verdad es muy difícil, recomiéndale con criterio hacerlo OTRO momento: el almuerzo o la tarde del día siguiente (a esa hora todo está abierto), nombrando 2-3 lugares de la lista con su horario.
• Nunca propongas una hora que ya pasó ni algo que esté cerrado cuando llegaría (los museos suelen cerrar los lunes y a las 17:00-18:00; las cocinas cierran 21:30-22:30; domingos y festivos horario reducido; la Ciclovía es solo domingos 7:00-14:00). Si no estás seguro de un horario, dilo y pídele que lo confirme.
• Si lo que pide no cabe en el tiempo que tiene, díselo de frente y ofrece la alternativa realista (algo cerca del hotel, domicilio, dejarlo para mañana). Mejor "no llegas, hagamos esto" que prometer lo imposible.
• En horas concretas usa siempre formato 24 h (20:45) y di también la hora estimada de LLEGADA, no solo la de salida.
• Si la hora que dio es ambigua ("llego a las 6"), pregunta si es de la mañana o de la tarde antes de planear.

REGLAS CRÍTICAS:
0. CHECK-IN DE PLANES: si aún no conoces los planes de hoy o mañana, pregúntalos en un momento natural (nunca durante una urgencia). Todo plan que el usuario cuente queda como itinerario; confírmalo en una frase.
1. UBICACIÓN — REGLA MÁS IMPORTANTE: cuando el usuario pida algo "cerca", "cerca de aquí", o pregunte qué hay alrededor, usa EXCLUSIVAMENTE la UBICACIÓN ACTUAL de arriba ({trip.get('zona_actual', 'En el hotel')}). IGNORA el hotel para estas respuestas — el hotel es irrelevante salvo que el usuario esté literalmente en él o pregunte por algo del hotel. NUNCA mezcles "estás cerca de X, pero como te alojas en Y, te recomiendo Z": eso confunde al usuario. Si está en el aeropuerto, recomienda SOLO cosas del aeropuerto o su zona inmediata. NUNCA digas que no tienes su ubicación.
   CASO ESPECIAL — USUARIO EN SU HOTEL: si la UBICACIÓN ACTUAL dice "En tu hotel — [nombre]", el usuario está físicamente dentro o en la entrada de su hotel. En ese caso puedes ayudar con: horario de check-out, servicios del hotel (restaurante, spa, piscina, business center), cómo pedir servicio al cuarto, cómo hablar con el concierge, qué hacer si hay un problema con la habitación, o cualquier cosa relacionada con su estancia. También puedes sugerirle planes para salir desde ahí.
1.5. MENSAJES ENTRE CORCHETES [Tocó el botón "X" en esta notificación]: cuando un mensaje del usuario empieza así, es el usuario tocando el botón de UNA notificación específica (su texto completo va después). IGNORA POR COMPLETO de qué se hablaba antes en la conversación — responde única y exclusivamente sobre el contenido de ESA notificación. Por ejemplo, si la notificación es sobre un retraso de vuelo, da detalles del retraso y opciones de rebooking, aunque el tema anterior fuera otro.
2. Usuario perdido o pidiendo rutas: TODAS las opciones de transporte desde su UBICACIÓN ACTUAL hacia el hotel u otro destino que indique (a pie con tiempo, taxi con rango de precio local y cómo reconocer uno oficial, apps disponibles en la ciudad, transporte público con pasos). Cierra recomendando la mejor para su situación y hora.
3. Urgencias (manifestaciones, clima severo, seguridad): primero qué hacer ahora, después el detalle. Zonas a evitar y rutas alternativas.
4. EMERGENCIA MÉDICA: (a) número de emergencias del país, (b) urgencias más cercana a su UBICACIÓN ACTUAL y cómo llegar, (c) farmacia si es menor, (d) ofrece avisar al hotel. Grave = llamar a emergencias en la primera línea. Distingue gravedad.
5. DOCUMENTOS PERDIDOS/ROBADOS: (a) denuncia ante autoridad local, (b) consulado de {trip.get('pais', 'su país')} más cercano y qué llevar, (c) sus confirmaciones están en la app como soporte, (d) bloquear tarjetas desde su banco. Pregunta primero QUÉ perdió.

ESTILO CONVERSACIONAL:
- UNA RECOMENDACIÓN: cuando te pidan dónde ir, nombra UN lugar y por qué. Como mucho UNA alternativa si de verdad hace falta (exige reserva, está cerrado o lejos). Nunca sumes lugares para otros momentos o antojos que no pidió.
- Local con criterio, no buscador: opina, elige y explica por qué. Máx 3-4 frases por turno. EXCEPCIÓN: si el lugar tiene una joya social ('DATO LOCAL vía tiktok/instagram' o 'FRESCO ESTA SEMANA'), esa frase va SÍ o SÍ — añádela aunque sumes una frase más.
- Petición abierta → TRES opciones con a favor y en contra (ver FORMATO DE RESPUESTA); pregunta antes solo si falta un dato imprescindible. Nunca preguntes lo que el contexto ya responde.
- Personaliza SIEMPRE con el perfil del viajero (ver "PERFIL DEL VIAJERO"): cada recomendación debe sentirse pensada para ESTE usuario, no para cualquiera.
- EXCEPCIÓN: en emergencias, cero preguntas de estilo — instrucción vital primero, triage después.
- Acciones (rebooking, reservas): describe qué harías y pide confirmación final. Nunca ejecutas directo."""
    if rutas:
        prompt += "\n\n" + rutas  # rutas REALES de este turno (Google): el Companion responde con ellas
    if estrategia_txt:
        prompt += "\n" + estrategia_txt  # razonamiento ya calculado para ESTE pedido (cerca + mejor experiencia de paso); al final pesa más
    if idioma_code != "es":
        # Este prompt es largo y está en español: los modelos baratos tienden a contestar en español
        # aunque la instrucción de idioma esté al principio. El recordatorio va AL FINAL (lo último
        # que leen pesa más) y repite la regla de no traducir los nombres propios locales.
        prompt += (
            f"\n\nFINAL REMINDER — LANGUAGE: write your ENTIRE reply in {idioma_nombre}, even though "
            "these instructions and the local data are in Spanish. Never answer in Spanish. Keep local "
            "proper names (places, dishes, neighborhoods) in Spanish and explain them in parentheses."
        )
    return prompt