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


def disponible() -> bool:
    return MODE == "real"


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
