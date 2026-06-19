"""Búsqueda EN VIVO de cadenas cercanas con Google Places API (New).

Para cadenas (supermercados, farmacias, cajeros, fast food) NO tiene sentido
curar cada sede a mano — hay cientos y cambian. En vez de eso, cuando el usuario
pregunta "¿dónde hay un Carulla / cajero / farmacia cerca?", consultamos Google
Places alrededor de su GPS y devolvemos las más cercanas, al momento.

Diferencia con la curación (seed_data):
- Curación = lugares ÚNICOS con criterio (restaurantes de autor, museos). Offline.
- Búsqueda en vivo = cadenas y servicios. Online, solo cuando se usa.

Costo: solo se paga por búsqueda realizada (~$0.005-0.03 c/u con field mask
mínimo). Sin GOOGLE_MAPS_API_KEY corre en modo mock (lista vacía).

La key es la MISMA que usa enrich_places.py (Places API New). En Railway se
configura como variable de entorno GOOGLE_MAPS_API_KEY.
"""
import os

import httpx

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
MODE = "real" if API_KEY else "mock"
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Field mask mínimo = SKU barato. Solo lo que el viajero necesita ver.
_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.currentOpeningHours.openNow",
    "places.googleMapsUri",
])

# Sinónimos → query que Google entiende bien. El usuario puede decir
# "cajero" y Google busca "ATM"; "súper" → "supermarket", etc.
SINONIMOS = {
    "cajero": "cajero automático ATM",
    "cajeros": "cajero automático ATM",
    "atm": "cajero automático ATM",
    "farmacia": "farmacia droguería",
    "droguería": "farmacia droguería",
    "drogueria": "farmacia droguería",
    "supermercado": "supermercado",
    "súper": "supermercado",
    "super": "supermercado",
    "mercado": "supermercado",
    "banco": "banco",
    "gasolina": "estación de gasolina",
    "café": "café",
    "comida rápida": "comida rápida restaurante",
}



# Detección de intención de búsqueda de cadena.
# Cada entrada: (patrones_genericos, query_google, nombres_de_cadena_especificos)
#  - patrones_genericos: palabras/frases que, CON intención de proximidad, disparan.
#  - especificos: nombres de cadena que pueden disparar SIN proximidad.
# Todos los tokens van normalizados (sin acentos, minúsculas) y se matchean por
# palabra completa (\b...\b), así que NO uses espacios al final ni fragmentos.
_INTENCIONES = [
    # Supermercados (cadenas específicas + genérico)
    (["carulla"], "Carulla supermercado", ["carulla"]),
    (["exito", "almacenes exito"], "Éxito supermercado", ["exito"]),
    (["d1", "tienda d1"], "D1 tienda descuento", ["d1"]),
    (["ara", "supermercado ara"], "Ara supermercado", ["ara"]),
    (["jumbo", "tienda jumbo"], "Jumbo supermercado", ["jumbo"]),
    (["olimpica"], "Olímpica supermercado", ["olimpica"]),
    (["supermercado", "mercado", "super", "comprar comida", "comprar agua",
      "comprar cosas", "donde comprar"], "supermercado", []),
    # Farmacias
    (["farmacia", "drogueria", "medicamento", "pastilla", "cruz verde",
      "farmacenter", "drogas la rebaja"], "farmacia droguería",
     ["cruz verde", "farmacenter"]),
    # Cajeros / bancos
    (["cajero", "cajeros", "atm", "retirar plata", "sacar plata", "efectivo",
      "bancolombia", "davivienda"], "cajero automático ATM",
     ["bancolombia", "davivienda"]),
    # Comida rápida / cadenas colombianas
    (["crepes", "crepes & waffles", "crepes y waffles"], "Crepes & Waffles restaurante",
     ["crepes"]),
    (["juan valdez"], "Juan Valdez Café", ["juan valdez"]),
    (["el corral", "hamburguesa", "hamburguesas"], "El Corral hamburguesas",
     ["el corral"]),
    (["frisby"], "Frisby pollo", ["frisby"]),
    # Gasolina / movilidad
    (["gasolina", "combustible", "gasolinera", "estacion de servicio"],
     "estación de gasolina", []),
    # Café genérico
    (["cafeteria"], "café", []),
]


def disponible() -> bool:
    return MODE == "real"


def detectar_busqueda_cadena(mensaje: str) -> str | None:
    """Devuelve la query de búsqueda si el mensaje pide encontrar una cadena/servicio.
    Devuelve None si es charla general.

    IMPORTANTE: el matching es por PALABRA COMPLETA (regex con \\b), no por
    substring. Antes 'ara ' hacía match dentro de 'para', 'comprara', 'llegara',
    etc., y disparaba una búsqueda de supermercados Ara de la nada. Ahora 'ara'
    solo matchea la palabra 'ara' aislada. Además, las categorías genéricas
    (supermercado, farmacia, cajero) SOLO se activan si hay intención real de
    proximidad/compra en el mensaje; mencionar el nombre de una cadena específica
    se acepta sin proximidad solo si no es claramente una pregunta informativa.
    """
    import re
    m = _normaliza(mensaje)

    proximidad = ["cerca", "aqui", "por aca", "por aqui", "hay un", "hay una",
                  "hay algun", "donde hay", "donde queda", "donde puedo",
                  "encuentro", "buscame", "encuentrame", "necesito",
                  "quiero ir a un", "quiero comprar", "donde compro", "como llego a un"]
    tiene_proximidad = any(p in m for p in proximidad)

    # Es una pregunta informativa sobre el lugar, no un "encuéntrame uno cerca".
    # Ej: "¿qué venden en Carulla?", "¿a qué hora abre Éxito?". No buscar.
    es_pregunta_info = any(p in m for p in (
        "que venden", "que tiene", "que hay en", "a que hora", "horario de",
        "cuanto cuesta", "es bueno", "como es", "que es "))

    for patrones, query, especificos in _INTENCIONES:
        # match por palabra completa de cualquiera de los patrones
        if any(re.search(rf"\b{re.escape(p)}\b", m) for p in patrones):
            if tiene_proximidad and not es_pregunta_info:
                return query
            # Sin proximidad: solo si nombra una CADENA específica (no categoría
            # genérica) y no es una pregunta informativa.
            if especificos and not es_pregunta_info and any(
                    re.search(rf"\b{re.escape(p)}\b", m) for p in especificos):
                return query
    return None


def _normaliza(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", (s or "").lower().strip()) \
        .encode("ascii", "ignore").decode()


def buscar_cerca(query: str, lat: float, lng: float,
                 radio_m: int = 2500, max_results: int = 4) -> list[dict]:
    """Busca lugares de un tipo/cadena alrededor de unas coordenadas.

    query: lo que el usuario pide ("Carulla", "cajero", "farmacia", "D1"...).
    Devuelve lista de dicts normalizados o [] si no hay key o no hay resultados.
    """
    if not API_KEY or lat is None or lng is None:
        return []

    q = SINONIMOS.get(query.strip().lower(), query.strip())
    try:
        resp = httpx.post(
            SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": _FIELDS,
            },
            json={
                "textQuery": q,
                "languageCode": "es",
                "regionCode": "CO",
                "maxResultCount": max_results,
                "locationBias": {
                    "circle": {
                        "center": {"latitude": lat, "longitude": lng},
                        "radius": float(radio_m),
                    }
                },
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        places = resp.json().get("places", [])
    except Exception:
        return []

    out = []
    for p in places:
        loc = p.get("location", {})
        plat, plng = loc.get("latitude"), loc.get("longitude")
        out.append({
            "name": (p.get("displayName") or {}).get("text", ""),
            "address": p.get("formattedAddress", ""),
            "lat": plat, "lng": plng,
            "rating": p.get("rating"),
            "abierto_ahora": (p.get("currentOpeningHours") or {}).get("openNow"),
            "place_id": p.get("id", ""),
            "maps_link": _maps_link(plat, plng, p.get("id", ""), lat, lng),
        })
    return out


def _maps_link(dest_lat, dest_lng, place_id, orig_lat, orig_lng) -> str:
    import urllib.parse
    if dest_lat is None:
        return ""
    params = {
        "api": "1",
        "origin": f"{orig_lat},{orig_lng}",
        "destination": f"{dest_lat},{dest_lng}",
        "travelmode": "walking",
    }
    if place_id:
        params["destination_place_id"] = place_id
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(params)


def geocodificar_hotel(nombre_hotel: str, ciudad: str, pais: str = "") -> dict | None:
    """Geocodifica el nombre del hotel contra Google Places API una sola vez
    al crear/actualizar el trip. Devuelve {lat, lng, place_id, dir} o None.

    Se llama en background (no bloquea el onboarding). Con estos datos, el
    Companion puede detectar automáticamente cuando el usuario está en su hotel
    comparando su GPS con las coordenadas devueltas aquí.

    Si no hay API key (modo dev/test), devuelve None sin lanzar excepción.
    """
    if not API_KEY or not nombre_hotel.strip():
        return None

    sufijo_pais = pais or ""
    query = f"{nombre_hotel}, {ciudad}"
    if sufijo_pais and sufijo_pais.lower() not in query.lower():
        query += f", {sufijo_pais}"

    try:
        resp = httpx.post(
            SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location",
            },
            json={
                "textQuery": query,
                "languageCode": "es",
                "maxResultCount": 1,
                # Sin locationBias: el hotel puede estar en cualquier zona de la ciudad
                # y una restricción geográfica podría descartar el resultado correcto.
                "includedType": "lodging",
            },
            timeout=12,
        )
        if resp.status_code != 200:
            return None
        places = resp.json().get("places", [])
        if not places:
            return None
        p = places[0]
        loc = p.get("location", {})
        return {
            "lat": round(loc.get("latitude", 0.0), 7),
            "lng": round(loc.get("longitude", 0.0), 7),
            "place_id": p.get("id", ""),
            "dir": p.get("formattedAddress", ""),
            "google_name": (p.get("displayName") or {}).get("text", ""),
        }
    except Exception:
        return None
