"""Watchers del Companion: traducen el mundo exterior a señales del motor.

- duffel_webhook: normaliza order change notifications de Duffel → señal operativa.
- update_location: señales del teléfono (geofence / ubicación significativa) →
  actualizan el Trip Context gratis y opcionalmente disparan señal de zona.
- scanner_finding: hallazgo del Destination Scanner (corre por destino en producción).
- news_alert: News watcher (Google News + X + TikTok/IG) → señal operativa de seguridad.
- check_in: cron 8pm que pregunta/confirma los planes de mañana.
"""
from . import context, db, engine, geo, llm, search


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
        label = geo.nearest_known_label(trip["ciudad"], lat, lng)
        zona = label or zona
        patch["lat_actual"] = lat
        patch["lng_actual"] = lng

    patch["zona_actual"] = zona
    db.update_trip(trip_id, patch)

    result = {"trip_id": trip_id, "zona_actual": zona, "geofence_event": None}
    if disparar_geofence:
        result["geofence_event"] = engine.ingest(
            trip_id, source="Geofencing del OS", category="zona", operational=False,
            payload=f"El usuario acaba de entrar caminando a la zona '{zona}'.",
        )
    return result


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
    return {"message": texto}


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

    noticias = search.search(
        f"{ciudad} Colombia noticias hoy manifestación cierre vial clima",
        max_results=3, topic="news",
    )
    recomendaciones = search.search(
        f"{ciudad} eventos restaurantes recomendados blog 2026",
        max_results=3, topic="general",
    )

    resultados = []
    for item in noticias:
        if not item["content"]:
            continue
        payload = f"{item['title']}: {item['content'][:400]}"
        r = engine.ingest(trip_id, source=f"News watcher (Google News) — {item['url']}",
                          category="seguridad", operational=False, payload=payload)
        resultados.append({"tipo": "noticia", "fuente": item["url"], **r})

    for item in recomendaciones:
        if not item["content"]:
            continue
        payload = f"{item['title']}: {item['content'][:400]}"
        r = engine.ingest(trip_id, source=f"Destination Scanner (blogs) — {item['url']}",
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

    origin = (trip["lat_actual"], trip["lng_actual"]) \
        if trip.get("lat_actual") is not None and trip.get("lng_actual") is not None \
        else geo.zone_coords(city, zona)

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
