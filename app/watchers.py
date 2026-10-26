"""Watchers del Companion: traducen el mundo exterior a señales del motor.

- duffel_webhook: normaliza order change notifications de Duffel → señal operativa.
- update_location: señales del teléfono (geofence / ubicación significativa) →
  actualizan el Trip Context gratis y opcionalmente disparan señal de zona.
- scanner_finding: hallazgo del Destination Scanner (corre por destino en producción).
- news_alert: News watcher (Google News + X + TikTok/IG) → señal operativa de seguridad.
- check_in: cron 8pm que pregunta/confirma los planes de mañana.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from . import airport, context, db, engine, geo, llm, search

log = logging.getLogger("voyra.watchers")


def duffel_webhook(trip_id: str, payload: dict) -> dict:
    """Webhook de Duffel → señal operativa.

    IDEMPOTENTE: Duffel reintenta la entrega si no respondemos 2xx a tiempo, y
    sin dedup cada reintento generaba OTRO push de "tu vuelo cambió". Si el
    evento trae `id`, se registra en webhook_events y los duplicados se
    responden con la misma forma pero sin volver a notificar. Si el
    procesamiento falla, la marca se libera para que el reintento sí entre.
    """
    if db.get_trip(trip_id) is None:
        raise KeyError(trip_id)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    tipo = payload.get("type", "order.updated")
    desc = data.get("description") or str(data)[:300]
    event_id = str(payload.get("id") or "").strip()
    clave = f"duffel:{event_id}" if event_id else ""
    if clave and not db.claim_webhook_event(clave, trip_id):
        log.info("webhook_duplicado provider=duffel event=%s trip=%s", event_id, trip_id)
        return {"decision_id": None, "filter": "webhook_duplicado", "score": None,
                "decision": "silence", "notification": None, "duplicado": True}
    try:
        return engine.ingest(trip_id, source="Duffel webhook", category="vuelo",
                             operational=True, payload=f"Webhook Duffel ({tipo}): {desc}")
    except Exception:
        if clave:
            db.release_webhook_event(clave)
        raise


def update_location(trip_id: str, zona: str, disparar_geofence: bool = False,
                    lat: float | None = None, lng: float | None = None) -> dict:
    """Actualiza la ubicación del trip.

    Sin lat/lng: comportamiento anterior — `zona` tal cual la mande el
    cliente (dropdown manual, fallback si no hay permiso de GPS).

    Con lat/lng (GPS real del celular): se guardan como lat_actual/lng_actual
    — esto es lo que /nearby va a usar como origen para Haversine contra las
    coordenadas reales de cada lugar curado, sin pasar por ninguna zona. Y
    `zona` se reemplaza por geo.nearest_known_label(lat, lng) — una etiqueta
    legible para el Companion ("Chapinero Alto") más precisa que cualquier
    selección manual, calculada gratis por cercanía real.
    """
    trip = db.get_trip(trip_id)
    patch: dict = {}

    if lat is not None and lng is not None:
        # ── Detección "estás en tu hotel" ──────────────────────────────────
        # Si el trip tiene coordenadas del hotel (geocodificadas al crearlo) y
        # el GPS dice que el usuario está a ≤150m, la zona pasa a ser el hotel.
        # 150m: radio conservador que cubre el lobby, la piscina y los accesos
        # sin activarse cuando el usuario está en el restaurante de enfrente.
        lat_h = trip.get("lat_hotel")
        lng_h = trip.get("lng_hotel")
        nombre_hotel = trip.get("hotel", "").strip()
        if lat_h and lng_h and nombre_hotel:
            dist_m = geo.haversine_km(lat, lng, lat_h, lng_h) * 1000
            if dist_m <= 150:
                zona = f"En tu hotel — {nombre_hotel}"
            else:
                label = geo.nearest_known_label(trip["ciudad"], lat, lng)
                zona = label or zona
        else:
            label = geo.nearest_known_label(trip["ciudad"], lat, lng)
            zona = label or zona
        patch["lat_actual"] = lat
        patch["lng_actual"] = lng

    patch["zona_actual"] = zona
    db.update_trip(trip_id, patch)

    # ── Detección de "acabo de aterrizar" ──
    # Si la zona actual significa aeropuerto y la ciudad tiene curación de
    # aeropuerto, marcamos el trip en modo_aeropuerto. El frontend usa este
    # flag para cambiar TODA la interfaz al timeline de llegada. Solo lo
    # marcamos al ENTRAR (transición), para no repetir el saludo cada ping.
    en_aeropuerto = airport.is_airport_zone(zona) and airport.airport_for_city(trip["ciudad"]) is not None
    ya_estaba = bool(trip.get("modo_aeropuerto"))
    if en_aeropuerto != ya_estaba:
        db.update_trip(trip_id, {"modo_aeropuerto": en_aeropuerto})

    result = {"trip_id": trip_id, "zona_actual": zona, "geofence_event": None,
              "modo_aeropuerto": en_aeropuerto, "acaba_de_llegar": en_aeropuerto and not ya_estaba}
    if disparar_geofence:
        result["geofence_event"] = engine.ingest(
            trip_id, source="Geofencing del OS", category="zona", operational=False,
            payload=f"El usuario acaba de entrar caminando a la zona '{zona}'.",
        )
    return result


def airport_arrival(trip_id: str) -> dict:
    """Devuelve el timeline de llegada + transporte para el aeropuerto del destino.

    Es lo que pinta el 'modo aeropuerto' del frontend. Combina la curación
    (app/airport.py) con tiempos en vivo si hay búsqueda configurada.
    """
    trip = db.get_trip(trip_id)
    return airport.arrival_payload(trip)


def scanner_finding(trip_id: str, hallazgo: str) -> dict:
    return engine.ingest(trip_id, source="Destination Scanner (TikTok/IG)",
                         category="recomendacion", operational=False, payload=hallazgo)


def news_alert(trip_id: str, alerta: str) -> dict:
    return engine.ingest(trip_id, source="News watcher (Google News + X + TikTok/IG)",
                         category="seguridad", operational=True, payload=alerta)


_CHECKIN_FALLBACK = {
    "nocturno": ("¡Buenas noches! ¿Qué planes tienes para mañana? Cuéntame y te "
                 "ayudo a organizarlos (horarios, cómo llegar, qué llevar)."),
    "matutino": ("¡Buenos días! ¿Qué tienes planeado para hoy? Cuéntame y te doy "
                 "tips para arrancar bien."),
}


def _texto_checkin(trip: dict, instruccion: str, tipo: str) -> str:
    """Genera el check-in con el LLM; si el proveedor falla, usa un texto fijo
    cálido en vez de reventar el endpoint / el tick del scheduler."""
    history = context.historial(trip["id"])
    history.append({"role": "user", "content": instruccion})
    try:
        return llm.chat(context.build(trip), history)
    except Exception as e:
        log.warning("checkin_fallback tipo=%s trip=%s error=%s", tipo, trip["id"], type(e).__name__)
        from . import traducir
        return traducir.traducir([_CHECKIN_FALLBACK[tipo]], trip.get("idioma") or "es")[0]


def check_in(trip_id: str) -> dict:
    trip = db.get_trip(trip_id)
    texto = _texto_checkin(trip, (
        "[SEÑAL DEL SISTEMA — no es el usuario]: Son las 8pm. Haz tu check-in nocturno: máximo 3 frases "
        "cálidas, comenta algo útil de mañana (clima, actividad conocida, o el vuelo si es el último día) "
        "y pregunta los planes de mañana. Si ya los conoces, confírmalos y da un tip concreto en vez de preguntar."
    ), "nocturno")
    db.insert("messages", {"trip_id": trip_id, "role": "companion", "content": texto})
    _push_checkin(trip_id, "Prepara mañana 🌙", texto)
    return {"message": texto}


def check_in_matutino(trip_id: str) -> dict:
    """Check-in de la mañana (~8am hora local): saluda, comenta el plan/clima del
    día y da un tip concreto para arrancar. Es conversación, no alerta operativa."""
    trip = db.get_trip(trip_id)
    texto = _texto_checkin(trip, (
        "[SEÑAL DEL SISTEMA — no es el usuario]: Es la mañana (~8am). Haz tu check-in matutino: "
        "máximo 3 frases cálidas. Si conoces los planes de HOY, confírmalos y da un tip concreto para "
        "arrancar bien (a qué hora salir, qué llevar, clima si lo sabes). Si no conoces los planes de hoy, "
        "pregúntalos en una frase amable. No repitas lo que ya dijiste anoche."
    ), "matutino")
    db.insert("messages", {"trip_id": trip_id, "role": "companion", "content": texto})
    _push_checkin(trip_id, "Revisa tu día ☀️", texto)
    return {"message": texto}


# Si el usuario habló con el Companion hace menos de esto, el check-in queda en
# el chat pero no le suena el celular: ya está en contacto, no hace falta empujar.
CHECKIN_SIN_PUSH_SI_ACTIVO_MIN = 180


def _push_checkin(trip_id: str, title: str, texto: str) -> None:
    """Manda el check-in al celular como push (si hay VAPID). El cuerpo va corto
    a propósito: la notificación es solo el 'gancho' para abrir la app, donde el
    mensaje completo ya quedó guardado en el chat. Nunca rompe el flujo."""
    if db.user_active_within(trip_id, CHECKIN_SIN_PUSH_SI_ACTIVO_MIN):
        return
    try:
        from . import push
        # Cuerpo breve para la notificación (1 línea). El texto completo vive en
        # el chat (tabla messages), que la app carga al abrirse.
        cuerpo = texto if len(texto) <= 90 else texto[:87].rstrip() + "…"
        # data.kind="checkin" + open_chat le dice al service worker / la app que
        # al tocar debe abrir directamente la conversación.
        push.send_to_trip_async(trip_id, title, cuerpo, data={"kind": "checkin", "open": "chat"})
    except Exception:
        log.exception("push_checkin_error trip=%s", trip_id)


def recordatorio_vuelo_regreso(trip_id: str) -> dict:
    """Aviso operativo el día del regreso: recuerda el vuelo y qué hacer.
    Operacional → ignora presupuesto y quiet hours, siempre llega como push."""
    trip = db.get_trip(trip_id)
    vuelo = trip.get("vuelo_regreso") or "tu vuelo de regreso"
    payload = (
        f"Hoy es el día del regreso del usuario. Vuelo: {vuelo}. Recuérdale con calma: "
        f"llegar al aeropuerto con tiempo (en El Dorado, 3h antes para internacional, 2h nacional), "
        f"tener pasaporte/documentos a mano, y ofrécele ayuda para organizar el traslado al aeropuerto "
        f"desde donde se aloja ({trip.get('hotel','su hotel')})."
    )
    return engine.ingest(trip_id, source="Itinerary watcher (día de regreso)",
                         category="vuelo", operational=True, payload=payload)


def aviso_hora_de_salir(trip_id: str, plan: str, minutos_antes: int = 60,
                        extra_msg: str = "") -> dict:
    """'Es hora de salir' antes de una actividad reservada. Operacional → push."""
    msg = extra_msg or (
        f"Se acerca una actividad del usuario: '{plan}'. Faltan ~{minutos_antes} minutos. "
        f"Recuérdaselo en tono cálido, dile que es buen momento para ir saliendo y, si sabes la zona, "
        f"sugiere cómo llegar desde su ubicación actual."
    )
    return engine.ingest(trip_id, source="Itinerary watcher (hora de salir)",
                         category="itinerario", operational=True, payload=msg)


def scan_destination(trip_id: str) -> dict:
    """News watcher + Destination Scanner reales, vía Tavily.

    Dos búsquedas: una de noticias/alertas (manifestaciones, cierres, clima) y
    otra de recomendaciones (eventos, restaurantes, blogs). Cada resultado entra
    al motor de relevancia normal — operational=False, así el SCORING decide
    qué vale la pena mostrar. Esto es clave: una búsqueda automática trae ruido
    (noticias viejas, otras ciudades); marcarlo todo como operativo forzaría
    push de todo. Dejamos que el LLM filtre, igual que con cualquier otra señal.

    En producción esto corre como cron por CIUDAD (no por trip) cada 24h,
    escribiendo a un `destination_feed` compartido entre todos los usuarios
    en ese destino — aquí se dispara por trip para poder probarlo end-to-end.
    """
    trip = db.get_trip(trip_id)
    ciudad = trip["ciudad"]
    from . import city_knowledge, timeutil
    from datetime import datetime

    pais  = city_knowledge.country_for_city(ciudad)
    lugar = f"{ciudad} {pais}".strip()

    # Fechas del viaje en español para las queries.
    try:
        inicio = datetime.strptime(trip["inicio"], "%Y-%m-%d")
        fin    = datetime.strptime(trip["fin"],    "%Y-%m-%d")
        meses  = ["enero","febrero","marzo","abril","mayo","junio",
                  "julio","agosto","septiembre","octubre","noviembre","diciembre"]
        rango  = (f"{inicio.day} al {fin.day} de {meses[fin.month-1]} {fin.year}"
                  if inicio.month == fin.month
                  else f"{inicio.day} de {meses[inicio.month-1]} al "
                       f"{fin.day} de {meses[fin.month-1]} {fin.year}")
    except Exception:
        rango = "2026"

    # ── Búsqueda 1: Seguridad / alertas operativas (siempre, 1 búsqueda) ──
    q_noticias = (f"{lugar} manifestación cierre vial huelga alerta seguridad turista", 3, "news")

    # ── Búsquedas 2-N: Una por gusto del usuario (máx 3 para no quemar cuota) ──
    # Cada gusto se traduce a términos de búsqueda concretos para que Tavily
    # devuelva resultados directamente relevantes al perfil — el LLM de scoring
    # luego los cruza con el contexto completo del viaje. Si no hay gustos
    # declarados, una búsqueda general de "qué hacer" sirve de fallback.
    # Mantén estas claves EXACTAMENTE iguales a las etiquetas del frontend
    # (constants.ts → GUSTOS). Si no coincide una clave, cae al fallback genérico
    # de abajo, así que igual funciona, pero pierde precisión de búsqueda.
    GUSTO_QUERY: dict[str, str] = {
        "Gastronomía local":            f"{lugar} restaurantes comida local típica imperdible {rango}",
        "Comida callejera":             f"{lugar} comida callejera tacos antojitos mercado {rango}",
        "Cocina de autor":              f"{lugar} restaurantes alta cocina chef autor {rango}",
        "Mariscos":                     f"{lugar} mariscos marisquería pescado fresco {rango}",
        "Tacos y antojitos":            f"{lugar} mejores tacos al pastor birria antojitos {rango}",
        "Dulces y repostería":          f"{lugar} repostería postres pastelería panadería {rango}",
        "Opciones veganas/vegetarianas":f"{lugar} restaurantes veganos vegetarianos {rango}",
        "Café de especialidad":         f"{lugar} cafés especialidad café tercera ola tostador {rango}",
        "Mezcal y tequila":             f"{lugar} mezcalería bar tequila cata destilados {rango}",
        "Cervecerías y vino":           f"{lugar} cervecería artesanal bar de vinos enoteca {rango}",
        "Brunch y desayunos":           f"{lugar} mejores desayunos brunch terraza {rango}",
        "Vida nocturna":                f"{lugar} bares vida nocturna eventos noche {rango}",
        "Bares de coctelería":          f"{lugar} bares coctelería de autor mixología mejores bares {rango}",
        "Antros y baile":               f"{lugar} antros discotecas baile fiesta {rango}",
        "Música en vivo":               f"{lugar} música en vivo conciertos mariachi shows {rango}",
        "Eventos deportivos":           f"{lugar} eventos deportivos fútbol partidos estadio {rango}",
        "Historia y cultura":           f"{lugar} museos monumentos historia exposición {rango}",
        "Museos y galerías":            f"{lugar} museos galerías exposiciones arte {rango}",
        "Arte y diseño":                f"{lugar} arte galerías murales diseño {rango}",
        "Arquitectura":                 f"{lugar} arquitectura edificios históricos arquitectónico {rango}",
        "Cine y teatro":                f"{lugar} teatro cine cartelera funciones espectáculos {rango}",
        "Mercados y artesanías":        f"{lugar} mercados artesanías tianguis local {rango}",
        "Naturaleza y aire libre":      f"{lugar} parques naturaleza senderismo aire libre {rango}",
        "Senderismo":                   f"{lugar} senderismo rutas montaña hiking bosque {rango}",
        "Aventura":                     f"{lugar} aventura actividades al aire libre tours {rango}",
        "Playas":                       f"{lugar} playas actividades acuáticas {rango}",
        "Bienestar y relax":            f"{lugar} spa bienestar relax termales {rango}",
        "Aguas termales":               f"{lugar} aguas termales balneario spa natural {rango}",
        "Fotografía":                   f"{lugar} miradores spots fotográficos {rango}",
        "Planes tranquilos":           f"{lugar} parques jardines planes tranquilos {rango}",
        "Compras":                      f"{lugar} compras tiendas centros comerciales {rango}",
        "En familia / con niños":       f"{lugar} actividades familia niños qué hacer {rango}",
        "Pet-friendly":                 f"{lugar} lugares pet friendly se admiten mascotas perros {rango}",
        "Joyas escondidas":             f"{lugar} lugares escondidos secretos que pocos conocen locales {rango}",
        "Lujo y premium":               f"{lugar} experiencias de lujo premium fine dining exclusivo {rango}",
        "Económico / mochilero":        f"{lugar} barato económico mochilero gratis qué hacer {rango}",
        "Grafiti y arte urbano":        f"{lugar} grafiti arte urbano murales tour {rango}",
        "Ciclovía y bici":              f"{lugar} ciclovía tour en bicicleta rutas {rango}",
        "Miradores y vistas":           f"{lugar} miradores vistas panorámicas atardecer {rango}",
        "Escapadas desde Bogotá":       f"{lugar} escapadas de un día excursiones cerca {rango}",
    }
    gustos = trip.get("gustos") or []
    gustos_a_buscar = gustos[:3] if gustos else []  # máx 3 búsquedas de cuota

    if gustos_a_buscar:
        q_recos = [(GUSTO_QUERY.get(g, f"{lugar} {g.lower()} {rango}"), 2, "general")
                   for g in gustos_a_buscar]
    else:
        # Sin gustos: fallback genérico de qué hacer en las fechas del viaje.
        q_recos = [(f"{lugar} qué hacer eventos festivales {rango}", 3, "general")]

    # Las 2-4 búsquedas son independientes: en paralelo (antes en serie, ~1-3 s
    # cada una). El scoring de abajo sigue en serie a propósito: el presupuesto
    # diario de push se evalúa señal por señal y en paralelo podría excederse.
    consultas = [q_noticias] + q_recos
    if search.MODE == "mock":
        resultados_busqueda = [[] for _ in consultas]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(consultas))) as ex:
            resultados_busqueda = list(ex.map(
                lambda q: search.search(q[0], max_results=q[1], topic=q[2]), consultas))
    noticias = resultados_busqueda[0]
    recomendaciones: list[dict] = [it for lote in resultados_busqueda[1:] for it in lote]

    resultados = []
    for item in noticias:
        if not item["content"]:
            continue
        payload = f"{item['title']}: {item['content'][:400]}"
        r = engine.ingest(trip_id, source=f"News watcher — {item['url']}",
                          category="seguridad", operational=False, payload=payload)
        resultados.append({"tipo": "noticia", "fuente": item["url"], **r})

    for item in recomendaciones:
        if not item["content"]:
            continue
        payload = f"{item['title']}: {item['content'][:400]}"
        r = engine.ingest(trip_id, source=f"Destination Scanner — {item['url']}",
                          category="recomendacion", operational=False, payload=payload)
        resultados.append({"tipo": "recomendacion", "fuente": item["url"], **r})

    return {
        "search_mode": search.MODE,
        "encontrados": len(noticias) + len(recomendaciones),
        "procesados": len(resultados),
        "resultados": resultados,
    }


def nearby_recommendations(trip_id: str, limit: int = 3) -> dict:
    """Recomendaciones desde la base curada (destination_places), rankeadas por
    distancia REAL (Haversine). Cero búsquedas, cero costo — y cada una sale
    con su deep link de Google Maps listo.

    Origen de la distancia, en orden de prioridad:
    1. lat_actual/lng_actual (GPS real del celular, vía update_location) —
       Haversine directo contra las coordenadas reales de cada lugar curado.
       Esto YA es precisión real, no aproximación de zona.
    2. geo.zone_coords(zona_actual) — fallback si todavía no hay GPS (ej.
       cliente sin permiso de ubicación, o testing manual con el dropdown).

    Dedup (24h): los lugares ya recomendados a este viaje se saltan, así cada
    llamada muestra opciones nuevas en vez de repetir/silenciar las mismas.
    """
    trip = db.get_trip(trip_id)
    city = trip["ciudad"]
    zona = trip.get("zona_actual", "")

    places = db.places_for_city(city)
    if not places:
        return {"city": city, "zona_actual": zona, "candidatos": 0, "resultados": [],
                "nota": f"Sin lugares curados para '{city}' todavía."}

    origin = geo.resolve_origin(trip)

    enriquecidos = []
    for p in places:
        d = geo.haversine_km(origin[0], origin[1], p["lat"], p["lng"]) if origin else None
        enriquecidos.append({**p, "distancia_km": round(d, 2) if d is not None else None})

    if origin:
        enriquecidos.sort(key=lambda p: p["distancia_km"])

    ya_vistos = db.recently_recommended(trip_id)
    candidatos = [p for p in enriquecidos if p["name"] not in ya_vistos]
    seleccionados = candidatos[:limit]

    resultados = []
    for p in seleccionados:
        dist_txt = f", a ~{p['distancia_km']} km de tu ubicación actual ({zona})" if p["distancia_km"] is not None else ""
        payload = f"{p['name']} ({p['category']}, zona {p['zona']}){dist_txt}. {p['descripcion']}"
        modo = geo.suggest_mode(p["distancia_km"])
        extra = {
            "place_name": p["name"],
            "distancia_km": p["distancia_km"],
            "maps_link": geo.maps_link(p["lat"], p["lng"], modo, p.get("maps_query")),
        }
        r = engine.ingest(trip_id, source=f"Curación de destino — {p['name']}",
                         category="recomendacion", operational=False, payload=payload, extra=extra)
        db.mark_recommended(trip_id, p["name"])
        resultados.append({**r, "lugar": p["name"], "distancia_km": p["distancia_km"]})

    out = {"city": city, "zona_actual": zona, "candidatos": len(places), "resultados": resultados}
    if not seleccionados:
        out["nota"] = "Ya te mostré todos los lugares cercanos que tengo curados para esta zona en las últimas 24h."
    return out



# ── Recomendaciones proactivas según los gustos del viajero ──
# Tres momentos del día. En cada uno se elige UN lugar curado que calce con los gustos declarados (tags ∩ gustos),
# de la categoría que tiene sentido a esa hora, que no se haya recomendado ya en este viaje ni esté en su itinerario.
# Sin gustos o sin match no se manda nada: preferimos callar a mandar algo genérico. Cero costo de API.
_SLOTS_RECO = {
    "mediodia": {"cats": {"restaurante"}, "emoji": "🍽️"},
    "tarde": {"cats": {"atraccion", "parque", "mercado", "experiencia", "mirador", "cafe"}, "emoji": "📍"},
    "noche": {"cats": {"bar", "restaurante"}, "emoji": "🌙"},
}
_PLANTILLA_RECO = {
    "mediodia": {"es": "Para almorzar hoy: {name} ({zona}). {clave} ¿Te cuento cómo llegar?",
                 "en": "For lunch today: {name} ({zona}). {clave} Want directions?",
                 "pt": "Para almoçar hoje: {name} ({zona}). {clave} Quer saber como chegar?",
                 "fr": "Pour déjeuner aujourd'hui : {name} ({zona}). {clave} Je t'explique comment y aller ?",
                 "de": "Zum Mittagessen heute: {name} ({zona}). {clave} Soll ich dir den Weg zeigen?",
                 "it": "Per pranzo oggi: {name} ({zona}). {clave} Ti dico come arrivare?"},
    "tarde": {"es": "Plan para esta tarde: {name} ({zona}). {clave} ¿Lo agrego a tu itinerario?",
              "en": "Plan for this afternoon: {name} ({zona}). {clave} Should I add it to your itinerary?",
              "pt": "Plano para esta tarde: {name} ({zona}). {clave} Adiciono ao seu roteiro?",
              "fr": "Plan pour cet après-midi : {name} ({zona}). {clave} Je l'ajoute à ton itinéraire ?",
              "de": "Plan für heute Nachmittag: {name} ({zona}). {clave} Soll ich es in deinen Reiseplan aufnehmen?",
              "it": "Piano per questo pomeriggio: {name} ({zona}). {clave} Lo aggiungo al tuo itinerario?"},
    "noche": {"es": "Para esta noche: {name} ({zona}). {clave} ¿Te cuento cómo llegar?",
              "en": "For tonight: {name} ({zona}). {clave} Want directions?",
              "pt": "Para esta noite: {name} ({zona}). {clave} Quer saber como chegar?",
              "fr": "Pour ce soir : {name} ({zona}). {clave} Je t'explique comment y aller ?",
              "de": "Für heute Abend: {name} ({zona}). {clave} Soll ich dir den Weg zeigen?",
              "it": "Per stasera: {name} ({zona}). {clave} Ti dico come arrivare?"},
}


def elegir_reco_por_gustos(trip: dict, slot: str) -> dict | None:
    """El lugar curado que mejor calza con los gustos del viaje para este momento del día, o None."""
    gustos = {g for g in (trip.get("gustos") or []) if g}
    if not gustos or slot not in _SLOTS_RECO:
        return None
    ya = db.recently_recommended(trip["id"], hours=24 * 60)
    en_plan = " ".join(f"{p.get('titulo', '')} {p.get('lugar') or ''}" for p in db.get_plans(trip["id"])).lower()
    origin = geo.resolve_origin(trip)
    candidatos = []
    for p in db.places_for_city(trip.get("ciudad", "")):
        if p.get("category") not in _SLOTS_RECO[slot]["cats"] or p["name"] in ya:
            continue
        if p["name"].lower() in en_plan:
            continue
        match = len(gustos & set(p.get("tags") or []))
        if not match:
            continue
        d = geo.haversine_km(origin[0], origin[1], p["lat"], p["lng"]) if origin else 0.0
        candidatos.append((-match, d if d is not None else 0.0, -float(p.get("rating") or 0), p))
    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[:3])
    return candidatos[0][3]


def recomendar_por_gustos(trip_id: str, slot: str) -> dict:
    """Mensaje proactivo corto con UN lugar acorde a los gustos. Queda en el chat y sale como push."""
    from . import traducir
    trip = db.get_trip(trip_id)
    p = elegir_reco_por_gustos(trip, slot)
    if not p:
        return {"enviado": False}
    idioma = (trip.get("idioma") or "es").lower()
    local = p.get("local") if isinstance(p.get("local"), dict) else {}
    clave = (local.get("clave") or "").strip()
    if clave and idioma != "es":
        try:
            clave = traducir.traducir([clave], idioma)[0]
        except Exception:
            pass
    plantilla = _PLANTILLA_RECO[slot].get(idioma, _PLANTILLA_RECO[slot]["es"])
    texto = " ".join(plantilla.format(name=p["name"], zona=p.get("zona", ""), clave=clave).split())
    db.insert("messages", {"trip_id": trip_id, "role": "companion", "content": texto})
    db.mark_recommended(trip_id, p["name"])
    _push_checkin(trip_id, f"{_SLOTS_RECO[slot]['emoji']} {p['name']}", texto)
    return {"enviado": True, "lugar": p["name"], "slot": slot}
