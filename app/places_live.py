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
import logging
import os

from . import net

log = logging.getLogger("companion.places")

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
MODE = "real" if API_KEY else "mock"
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Timeouts acotados: la búsqueda en vivo va dentro del request del chat, así que
# nunca debe colgar al usuario más de unos segundos.
_TIMEOUT = 8.0
# Cachés TTL: la misma búsqueda ("farmacia" a ~100 m del mismo punto) o el mismo
# hotel no cambian en minutos/horas. Evita pagar y esperar a Google dos veces.
_cache_cerca = net.TTLCache(ttl_s=float(os.environ.get("PLACES_CACHE_TTL_S", "600")), maxsize=1024)
_cache_hotel = net.TTLCache(ttl_s=24 * 3600, maxsize=1024)
_SIN_CACHE = object()

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
    # Clave de caché: coordenadas redondeadas a ~100 m (3 decimales).
    ck = (q.lower(), round(float(lat), 3), round(float(lng), 3), int(radio_m), int(max_results))
    cacheado = _cache_cerca.get(ck)
    if cacheado is not None:
        return [dict(x) for x in cacheado]
    try:
        resp = net.request(
            "POST", SEARCH_URL, retries=1,
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
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("places_search_error status=%s", resp.status_code)
            return []
        places = resp.json().get("places", [])
    except Exception as e:
        log.warning("places_search_exception error=%s", type(e).__name__)
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
    _cache_cerca.set(ck, [dict(x) for x in out])
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


_cache_busqueda_hotel = net.TTLCache(ttl_s=6 * 3600, maxsize=256)
_CENTROS = {"bogota": (4.65, -74.07), "guadalajara": (20.6597, -103.3496)}


def buscar_hoteles(nombre: str, ciudad: str, pais: str = "", n: int = 4) -> list[dict]:
    """Hasta `n` hoteles que coinciden con lo que escribió el usuario, para que ELIJA cuál es. Hay nombres que existen en
    varias zonas o ciudades (hay dos Dann Carlton en Bogotá): tomar el primer resultado, como hace `geocodificar_hotel`, es
    adivinar. Devuelve [{place_id, nombre, direccion, lat, lng, rating, resenas, foto_name}]; [] si no hay key o resultados."""
    if not API_KEY or not (nombre or "").strip():
        return []
    ck = f"{nombre.strip().lower()}|{ciudad.strip().lower()}|{n}"
    hit = _cache_busqueda_hotel.get(ck, _SIN_CACHE)
    if hit is not _SIN_CACHE:
        return [dict(x) for x in hit]
    cuerpo = {"textQuery": f"{nombre}, {ciudad}" + (f", {pais}" if pais and pais.lower() not in ciudad.lower() else ""),
              "languageCode": "es", "maxResultCount": max(1, min(n, 6)), "includedType": "lodging"}
    centro = _CENTROS.get(norm_ciudad(ciudad))
    if centro:   # sesgo (no restricción) hacia la ciudad: los hoteles de ahí salen primero
        cuerpo["locationBias"] = {"circle": {"center": {"latitude": centro[0], "longitude": centro[1]}, "radius": 30000.0}}
    try:
        r = net.request("POST", SEARCH_URL, retries=1, timeout=_TIMEOUT,
                        headers={"Content-Type": "application/json", "X-Goog-Api-Key": API_KEY,
                                 "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,"
                                                     "places.rating,places.userRatingCount,places.photos.name"},
                        json=cuerpo)
        if r.status_code != 200:
            log.warning("buscar_hoteles_error status=%s", r.status_code)
            return []
        out = []
        for p in r.json().get("places", []):
            loc = p.get("location") or {}
            if loc.get("latitude") is None:
                continue
            out.append({"place_id": p.get("id", ""), "nombre": (p.get("displayName") or {}).get("text", ""),
                        "direccion": p.get("formattedAddress", ""), "lat": round(loc["latitude"], 7), "lng": round(loc["longitude"], 7),
                        "rating": p.get("rating"), "resenas": p.get("userRatingCount"),
                        "foto_name": ((p.get("photos") or [{}])[0]).get("name")})
        _cache_busqueda_hotel.set(ck, [dict(x) for x in out])
        return out
    except Exception as e:
        log.warning("buscar_hoteles_exception error=%s", type(e).__name__)
        return []


def norm_ciudad(c: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", (c or "").strip().lower()).encode("ascii", "ignore").decode()


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

    ck = query.strip().lower()
    # None también se cachea (hotel no encontrado): no re-preguntar en cada PATCH.
    cacheado = _cache_hotel.get(ck, _SIN_CACHE)
    if cacheado is not _SIN_CACHE:
        return dict(cacheado) if cacheado else None

    try:
        resp = net.request(
            "POST", SEARCH_URL, retries=1,
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
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("places_geocode_error status=%s", resp.status_code)
            return None
        places = resp.json().get("places", [])
        if not places:
            _cache_hotel.set(ck, None)
            return None
        p = places[0]
        loc = p.get("location", {})
        out = {
            "lat": round(loc.get("latitude", 0.0), 7),
            "lng": round(loc.get("longitude", 0.0), 7),
            "place_id": p.get("id", ""),
            "dir": p.get("formattedAddress", ""),
            "google_name": (p.get("displayName") or {}).get("text", ""),
        }
        _cache_hotel.set(ck, dict(out))
        return out
    except Exception as e:
        log.warning("places_geocode_exception error=%s", type(e).__name__)
        return None
