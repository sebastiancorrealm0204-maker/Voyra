"""Watchers del Companion: traducen el mundo exterior a señales del motor.

- duffel_webhook: normaliza order change notifications de Duffel → señal operativa.
- update_location: señales del teléfono (geofence / ubicación significativa) →
  actualizan el Trip Context gratis y opcionalmente disparan señal de zona.
- scanner_finding: hallazgo del Destination Scanner (corre por destino en producción).
- news_alert: News watcher (Google News + X + TikTok/IG) → señal operativa de seguridad.
- check_in: cron 8pm que pregunta/confirma los planes de mañana.
"""
from . import airport, context, db, engine, geo, llm, search


def duffel_webhook(trip_id: str, payload: dict) -> dict:
    tipo = payload.get("type", "order.updated")
    data = payload.get("data", {})
    desc = data.get("description") or str(data)[:300]
    return engine.ingest(trip_id, source="Duffel webhook", category="vuelo",
                         operational=True, payload=f"Webhook Duffel ({tipo}): {desc}")


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


def check_in(trip_id: str) -> dict:
    trip = db.get_trip(trip_id)
    history = [{"role": m["role"], "content": m["content"]} for m in db.rows("messages", trip_id)]
    history.append({"role": "user", "content": (
        "[SEÑAL DEL SISTEMA — no es el usuario]: Son las 8pm. Haz tu check-in nocturno: máximo 3 frases "
        "cálidas, comenta algo útil de mañana (clima, actividad conocida, o el vuelo si es el último día) "
        "y pregunta los planes de mañana. Si ya los conoces, confírmalos y da un tip concreto en vez de preguntar."
    )})
    texto = llm.chat(context.build(trip), history)
    db.insert("messages", {"trip_id": trip_id, "role": "companion", "content": texto})
    _push_checkin(trip_id, "Prepara mañana 🌙", texto)
    return {"message": texto}


def check_in_matutino(trip_id: str) -> dict:
    """Check-in de la mañana (~8am hora local): saluda, comenta el plan/clima del
    día y da un tip concreto para arrancar. Es conversación, no alerta operativa."""
    trip = db.get_trip(trip_id)
    history = [{"role": m["role"], "content": m["content"]} for m in db.rows("messages", trip_id)]
    history.append({"role": "user", "content": (
        "[SEÑAL DEL SISTEMA — no es el usuario]: Es la mañana (~8am). Haz tu check-in matutino: "
        "máximo 3 frases cálidas. Si conoces los planes de HOY, confírmalos y da un tip concreto para "
        "arrancar bien (a qué hora salir, qué llevar, clima si lo sabes). Si no conoces los planes de hoy, "
        "pregúntalos en una frase amable. No repitas lo que ya dijiste anoche."
    )})
    texto = llm.chat(context.build(trip), history)
    db.insert("messages", {"trip_id": trip_id, "role": "companion", "content": texto})
    _push_checkin(trip_id, "Revisa tu día ☀️", texto)
    return {"message": texto}


def _push_checkin(trip_id: str, title: str, texto: str) -> None:
    """Manda el check-in al celular como push (si hay VAPID). El cuerpo va corto
    a propósito: la notificación es solo el 'gancho' para abrir la app, donde el
    mensaje completo ya quedó guardado en el chat. Nunca rompe el flujo."""
    try:
        from . import push
        # Cuerpo breve para la notificación (1 línea). El texto completo vive en
        # el chat (tabla messages), que la app carga al abrirse.
        cuerpo = texto if len(texto) <= 90 else texto[:87].rstrip() + "…"
        # data.kind="checkin" + open_chat le dice al service worker / la app que
        # al tocar debe abrir directamente la conversación.
        push.send_to_trip(trip_id, title, cuerpo, data={"kind": "checkin", "open": "chat"})
    except Exception:
        pass


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
    noticias = search.search(
        f"{lugar} manifestación cierre vial huelga alerta seguridad turista",
        max_results=3, topic="news",
    )

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
        "Opciones veganas/vegetarianas":f"{lugar} restaurantes veganos vegetarianos {rango}",
        "Café de especialidad":         f"{lugar} cafés especialidad café tercera ola {rango}",
        "Mezcal y tequila":             f"{lugar} mezcalería bar tequila cata destilados {rango}",
        "Vida nocturna":                f"{lugar} bares vida nocturna eventos noche {rango}",
        "Música en vivo":               f"{lugar} música en vivo conciertos mariachi shows {rango}",
        "Historia y cultura":           f"{lugar} museos monumentos historia exposición {rango}",
        "Arte y diseño":                f"{lugar} arte galerías murales diseño {rango}",
        "Mercados y artesanías":        f"{lugar} mercados artesanías tianguis local {rango}",
        "Naturaleza y aire libre":      f"{lugar} parques naturaleza senderismo aire libre {rango}",
        "Fotografía":                   f"{lugar} miradores spots fotográficos {rango}",
        "Planes tranquilos":           f"{lugar} parques jardines planes tranquilos {rango}",
        "Bienestar y relax":            f"{lugar} spa bienestar relax termales {rango}",
        "Compras":                      f"{lugar} compras tiendas centros comerciales {rango}",
        "En familia / con niños":       f"{lugar} actividades familia niños qué hacer {rango}",
        "Aventura":                     f"{lugar} aventura actividades al aire libre tours {rango}",
        "Playas":                       f"{lugar} playas actividades acuáticas {rango}",
    }
    gustos = trip.get("gustos") or []
    gustos_a_buscar = gustos[:3] if gustos else []  # máx 3 búsquedas de cuota

    recomendaciones: list[dict] = []
    if gustos_a_buscar:
        for gusto in gustos_a_buscar:
            query = GUSTO_QUERY.get(gusto, f"{lugar} {gusto.lower()} {rango}")
            recomendaciones += search.search(query, max_results=2, topic="general")
    else:
        # Sin gustos: fallback genérico de qué hacer en las fechas del viaje.
        recomendaciones = search.search(
            f"{lugar} qué hacer eventos festivales {rango}",
            max_results=3, topic="general",
        )

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
