"""Geografía del Companion: distancia real entre puntos y deep links a Maps.

Cero APIs, cero costo, cero llave — solo matemática (Haversine) y construcción
de URLs. Esto es el "Nivel 1 + 1.5" que acordamos: las coordenadas vienen de
la curación por destino (destination_places) y de ZONE_COORDS (referencias
de zona para zona_actual, la misma granularidad que ya usa el Trip Context).

Google Maps Directions/Places API en vivo (tráfico real, geocoding de
direcciones arbitrarias) queda como mejora futura — requiere cuenta de
Google Cloud con facturación. Esto cubre el caso de uso principal sin esa
fricción.
"""
import math
import urllib.parse

from .db import norm_city, places_for_city


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en línea recta entre dos puntos del globo, en km."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def suggest_mode(distance_km: float | None) -> str:
    """Caminando si está cerca, si no auto. Sin dato de distancia, auto por defecto."""
    if distance_km is not None and distance_km < 1.5:
        return "walking"
    return "driving"


def maps_link(lat: float, lng: float, mode: str = "walking",
             maps_query: str | None = None) -> str:
    """Deep link de Google Maps hacia el lugar curado.

    Usa el formato que funciona tanto en navegador como abriendo la app nativa
    en iOS y Android. El parámetro api=1 es solo para la JS API embebida y
    causa "unsupported link" en móvil — no se usa aquí.
    """
    if maps_query:
        q = urllib.parse.quote(maps_query)
        return f"https://maps.google.com/?q={q}"
    return f"https://maps.google.com/?q={lat},{lng}"


def maps_link_from_to(
    orig_lat: float | None, orig_lng: float | None,
    dest_lat: float, dest_lng: float,
    mode: str = "driving",
    maps_query: str | None = None,
) -> str:
    """Deep link con ORIGEN y DESTINO explícitos (ruta completa).

    - Con origen: abre Google Maps en modo navegación con la ruta ya calculada.
    - Sin origen: abre Maps mostrando el destino para que el usuario inicie la ruta.

    El formato /dir/origen/destino funciona en navegador y abre la app nativa
    en Android e iOS (si está instalada), sin el problema del api=1.
    """
    # Destino: preferimos lat,lng exacto (más preciso) sobre el nombre
    if dest_lat and dest_lng:
        dest = f"{dest_lat},{dest_lng}"
    elif maps_query:
        dest = urllib.parse.quote(maps_query)
    else:
        dest = urllib.parse.quote(maps_query or "")

    if orig_lat is not None and orig_lng is not None:
        orig = f"{orig_lat},{orig_lng}"
        return f"https://www.google.com/maps/dir/{orig}/{dest}"

    # Sin origen: abrir el lugar directamente (Maps detecta ubicación actual)
    return f"https://maps.google.com/?q={dest}&zoom=16"


# Referencias de zona por ciudad — misma granularidad que zona_actual del
# Trip Context (nombres de barrio/área, no direcciones exactas).
ZONE_COORDS: dict[str, dict[str, tuple[float, float]]] = {
    "bogota": {
        "la candelaria": (4.5981, -74.0758),
        "centro": (4.5981, -74.0758),
        "centro historico": (4.5981, -74.0758),
        "zona g": (4.6580, -74.0570),
        "zona t": (4.6680, -74.0530),
        "zona rosa": (4.6680, -74.0530),
        "usaquen": (4.6950, -74.0310),
        "aeropuerto": (4.7016, -74.1469),
        "en el hotel": (4.6580, -74.0570),  # default razonable: Zona G/Chapinero
    },
    "cartagena": {
        "getsemani": (10.4218, -75.5499),
        "centro": (10.4236, -75.5482),
        "barrio historico / centro": (10.4236, -75.5482),
        "bocagrande": (10.3995, -75.5547),
        "aeropuerto": (10.4424, -75.5130),
        "mercado local": (10.4280, -75.5410),
        "playa / malecon": (10.3995, -75.5547),
        "en el hotel": (10.4236, -75.5482),
    },
}




def travel_minutes(dist_km: float | None, city: str = "") -> int:
    """Estima minutos de viaje dado distancia en km.

    Bogotá tiene tráfico pesado, así que los factores son conservadores:
    - A pie  (<1.5 km):  ~15 min/km  (promedio caminando con semáforos)
    - Taxi/Uber/carro:   ~4 km/h efectivos en Bogotá hora pico → 15 min/km
      (suena lento pero la velocidad promedio real en hora pico en Bogotá
       ronda 20-25 km/h; con la espera del taxi se va a ~4 km efectivos/h)
    - Mínimo: 10 min (tiempo de bajar, esperar taxi, etc.)

    Para otras ciudades usamos factores más optimistas (menos tráfico).
    """
    if dist_km is None:
        return 45  # sin info, margen conservador
    ciudad_baja = (city or "").lower()
    if dist_km < 1.5:
        minutos = dist_km * 15  # caminando
    elif "bogot" in ciudad_baja:
        minutos = dist_km * 15  # tráfico pesado bogotano
    else:
        minutos = dist_km * 8   # ciudades más fluidas
    return max(10, round(minutos))




def zone_coords(city: str, zona: str) -> tuple[float, float] | None:
    """Coordenadas de referencia para una zona. None si la ciudad o la zona no están mapeadas."""
    ciudad = ZONE_COORDS.get(norm_city(city))
    if not ciudad:
        return None
    z = norm_city(zona)
    if z in ciudad:
        return ciudad[z]
    # match parcial: "zona t / zona rosa" -> "zona t"
    for k, v in ciudad.items():
        if k in z or z in k:
            return v
    return None


def nearest_known_label(city: str, lat: float, lng: float) -> str | None:
    """Reverse-geocode gratis: dado un punto GPS, devuelve el nombre de zona/
    lugar conocido más cercano — para que el Companion hable en términos
    humanos ("Estás en Chapinero Alto") sin que el usuario elija nada.

    Busca en dos fuentes y se queda con la más cercana:
    - ZONE_COORDS[city]: zonas generales, incluye casos especiales sin
      lugares curados cerca (ej. "aeropuerto").
    - destination_places (la curación real, con coordenadas propias): da
      etiquetas más finas — "Chapinero Alto", "Parque de la 93 (Chicó)",
      "Usaquén" — exactamente el mismo vocabulario que ya usan las
      descripciones de /nearby.

    None si la ciudad no tiene ninguna referencia todavía (sin curación y
    sin ZONE_COORDS) — el caller debe mantener la zona anterior en ese caso.
    """
    candidatos: list[tuple[float, str]] = []

    for nombre, (zlat, zlng) in ZONE_COORDS.get(norm_city(city), {}).items():
        candidatos.append((haversine_km(lat, lng, zlat, zlng), nombre.title()))

    for p in places_for_city(city):
        candidatos.append((haversine_km(lat, lng, p["lat"], p["lng"]), p["zona"]))

    if not candidatos:
        return None
    candidatos.sort(key=lambda x: x[0])
    return candidatos[0][1]
