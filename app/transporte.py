"""Cómo llegar: rutas con tráfico real + criterio para Bogotá.

QUÉ HACE
─────────────────────────────────────────────────────────────────────────────
Dado un origen y un destino, compara las formas de llegar y dice cuál conviene AHORA:

  · taxi / app  → tiempo CON tráfico frente al tiempo sin tráfico (el trancón), distancia y una
                  estimación de precio.
  · TransMilenio / SITP → pasos reales (líneas, estaciones, transbordos, caminata).
  · a pie       → solo si es cerca.

Fuente de datos: Google Routes API (routes.googleapis.com), la misma GOOGLE_MAPS_API_KEY de Places
con "Routes API" habilitada. Sin la API (sin key, o key sin Routes habilitada) responde en MODO DEMO:
estimaciones por distancia, claramente marcadas con `fuente: "demo"` (así la pantalla se puede probar
y nunca se inventan líneas ni estaciones).

El texto para el usuario NO se arma aquí: la respuesta trae códigos (`motivo`, `nivel`) y el frontend los
traduce a cada idioma.

LÍMITES (honestos): no hay datos públicos fiables de llegada en vivo de buses SITP ni de TransMilenio,
así que el transporte público es por itinerario, no «llega en 3 min». El precio del taxi es una
estimación (taxímetro: base + km + tiempo, configurable por variables de entorno).
"""
import datetime
import logging
import os
import re
import threading
import urllib.parse
from zoneinfo import ZoneInfo

from . import geo, net

log = logging.getLogger("companion.transporte")

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
TZ = ZoneInfo("America/Bogota")
AEROPUERTO = (4.7016, -74.1469, "Aeropuerto El Dorado")

# Misma consulta → misma respuesta durante 5 min (varios viajeros piden las mismas rutas).
_cache = net.TTLCache(ttl_s=float(os.environ.get("TRANSPORTE_CACHE_TTL_S", "300")), maxsize=256)

# Estimación de taxi (COP): base + km + minutos (con tráfico) · mínimo · rango ±20 %.
TAXI_BASE = float(os.environ.get("TAXI_BASE_COP", "4500"))
TAXI_KM = float(os.environ.get("TAXI_KM_COP", "2000"))
TAXI_MIN = float(os.environ.get("TAXI_MIN_COP", "150"))
TAXI_MINIMA = float(os.environ.get("TAXI_MINIMA_COP", "9000"))

CAMINAR_MAX_KM = 3.0     # más lejos de esto no ofrecemos caminar
CAMINAR_KMH = 4.8


class RoutesNoDisponible(Exception):
    """La API de rutas no respondió (sin key, bloqueada, cuota…): se usa el modo demo."""


def disponible() -> bool:
    return bool(API_KEY)


# ─────────────────────────── lógica pura (con tests) ───────────────────────────
def nivel_trancon(con_trafico_s: float, sin_trafico_s: float) -> dict:
    """Qué tan mal está el tráfico: cociente entre el tiempo con tráfico y el tiempo sin tráfico."""
    ratio = (con_trafico_s / sin_trafico_s) if sin_trafico_s else 1.0
    extra = max(0, round((con_trafico_s - sin_trafico_s) / 60))
    nivel = "fluido" if ratio < 1.15 else "moderado" if ratio < 1.4 else "pesado" if ratio < 1.8 else "trancon"
    # En trayectos cortos la proporción exagera (15 → 22 min es «x1,5» pero son solo 7 min): exigimos minutos reales.
    if nivel in ("pesado", "trancon") and extra < 8:
        nivel = "moderado"
    if nivel == "moderado" and extra < 3:
        nivel = "fluido"
    return {"nivel": nivel, "ratio": round(ratio, 2), "extra_min": extra}


def precio_taxi(km: float, min_con_trafico: float) -> dict:
    """Estimación del taxi en COP: rango ±20 % redondeado a miles. Es un ORIENTATIVO, no el taxímetro."""
    centro = max(TAXI_MINIMA, TAXI_BASE + TAXI_KM * km + TAXI_MIN * min_con_trafico)
    redondea = lambda v: int(round(v / 1000.0) * 1000)  # noqa: E731
    return {"min": redondea(centro * 0.8), "max": redondea(centro * 1.2), "moneda": "COP"}


def hora_pico(ahora: datetime.datetime) -> bool:
    """Hora pico de Bogotá en días hábiles (TransMilenio va lleno y el tráfico pesa más)."""
    if ahora.weekday() >= 5:
        return False
    m = ahora.hour * 60 + ahora.minute
    return 5 * 60 + 30 <= m <= 8 * 60 + 30 or 16 * 60 + 30 <= m <= 19 * 60 + 30


def recomendar(op: dict, nivel: str) -> dict:
    """Elige la mejor forma de llegar y devuelve un CÓDIGO de motivo (el frontend lo traduce)."""
    taxi, tr, wk = op.get("taxi"), op.get("transporte"), op.get("caminar")
    if wk and wk["min"] <= 15:
        return {"modo": "caminar", "motivo": "cerca"}
    if taxi and tr:
        if nivel in ("pesado", "trancon") and tr["min"] <= taxi["min"] * 1.25:
            return {"modo": "transporte", "motivo": "trancon_gana_transporte"}
        if tr["min"] <= taxi["min"] * 1.1:
            return {"modo": "transporte", "motivo": "similar_y_mas_barato"}
        return {"modo": "taxi", "motivo": "taxi_mas_rapido"}
    if taxi:
        return {"modo": "taxi", "motivo": "solo_taxi"}
    if tr:
        return {"modo": "transporte", "motivo": "solo_transporte"}
    return {"modo": None, "motivo": "sin_opciones"}


def _segundos(valor: str | None) -> float:
    """'1234s' (formato de duración de Google) → 1234.0"""
    try:
        return float((valor or "0s").rstrip("s"))
    except ValueError:
        return 0.0


def etiqueta_agencia(nombres: list[str], respaldo: str = "") -> str:
    """Google llama «Autobús» a todo; la agencia sí distingue: Transmilenio-Dual → TransMilenio,
    Zonal-Urbano → SITP. Si no reconoce la agencia, deja el texto que dio Google."""
    j = " ".join(nombres or []).lower()
    if "transmilenio" in j or "troncal" in j or "alimentador" in j:
        return "TransMilenio"
    if "zonal" in j or "sitp" in j:
        return "SITP"
    return respaldo


def parsear_transito(route: dict) -> dict:
    """Respuesta de Routes (modo TRANSIT) → {min, caminata_min, transbordos, pasos[]}.
    Pasos: {tipo: 'caminar', min, metros} | {tipo: 'transporte', linea, vehiculo, desde, hasta, paradas, color}"""
    pasos, caminata_s, buses = [], 0.0, 0
    pend_s = pend_m = 0.0  # caminata acumulada: Google la parte en muchos pasos de una esquina; la unimos

    def volcar():
        nonlocal pend_s, pend_m
        if pend_s >= 45:
            pasos.append({"tipo": "caminar", "min": max(1, round(pend_s / 60)), "metros": int(pend_m)})
        pend_s = pend_m = 0.0

    for leg in route.get("legs", []):
        for st in leg.get("steps", []):
            dur = _segundos(st.get("staticDuration"))
            td = st.get("transitDetails")
            if td:
                volcar()
                ln = td.get("transitLine", {}) or {}
                paradas = td.get("stopDetails", {}) or {}
                buses += 1
                pasos.append({
                    "tipo": "transporte",
                    "linea": ln.get("nameShort") or ln.get("name") or "",
                    "vehiculo": etiqueta_agencia([a.get("name", "") for a in ln.get("agencies", []) or []],
                                                 ((ln.get("vehicle", {}) or {}).get("name", {}) or {}).get("text", "") or ""),
                    "tipo_vehiculo": (ln.get("vehicle", {}) or {}).get("type", ""),
                    "desde": (paradas.get("departureStop", {}) or {}).get("name", ""),
                    "hasta": (paradas.get("arrivalStop", {}) or {}).get("name", ""),
                    "paradas": td.get("stopCount"),
                    "color": ln.get("color", ""),
                    "min": round(dur / 60),
                })
            else:
                caminata_s += dur
                pend_s += dur
                pend_m += st.get("distanceMeters", 0) or 0
    volcar()
    total_s = _segundos(route.get("duration"))
    return {"min": round(total_s / 60), "caminata_min": round(caminata_s / 60),
            "transbordos": max(0, buses - 1), "pasos": pasos}


def enlaces(o: tuple[float, float], d: tuple[float, float], nombre: str) -> dict:
    """Atajos para abrir cada opción en la app que el viajero ya usa."""
    dest = f"{d[0]},{d[1]}"
    orig = f"{o[0]},{o[1]}"
    maps = "https://www.google.com/maps/dir/?api=1&origin={}&destination={}&travelmode={}"
    return {
        "uber": "https://m.uber.com/ul/?action=setPickup&pickup=my_location"
                f"&dropoff[latitude]={d[0]}&dropoff[longitude]={d[1]}&dropoff[nickname]={urllib.parse.quote(nombre or '')}",
        "maps_transporte": maps.format(orig, dest, "transit"),
        "maps_caminar": maps.format(orig, dest, "walking"),
        "maps_carro": maps.format(orig, dest, "driving"),
    }


def _norm(txt: str) -> str:
    import re
    import unicodedata
    t = unicodedata.normalize("NFD", (txt or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(ch for ch in t if unicodedata.category(ch) != "Mn")).strip()


def buscar_lugar(nombre: str, lugares: list[dict]) -> dict | None:
    """Lugar curado por nombre: primero coincidencia exacta (sin tildes ni mayúsculas), luego «contiene»."""
    n = _norm(nombre)
    if not n:
        return None
    for p in lugares:
        if _norm(p.get("name", "")) == n:
            return p
    for p in lugares:
        pn = _norm(p.get("name", ""))
        if n in pn or pn in n:
            return p
    return None


# ─────────────────────────── Google Routes ───────────────────────────
_MASK = ",".join([
    "routes.duration", "routes.staticDuration", "routes.distanceMeters",
    "routes.legs.steps.staticDuration", "routes.legs.steps.distanceMeters", "routes.legs.steps.travelMode",
    "routes.legs.steps.transitDetails",
    "routes.legs.steps.transitDetails.transitLine.agencies",
])


def _pedir_google(modo: str, o: tuple[float, float], d: tuple[float, float], salida: datetime.datetime | None) -> dict | None:
    """Una ruta de Google en el modo dado ('DRIVE' | 'TRANSIT'). Devuelve la ruta o None si no hay ruta.
    Lanza RoutesNoDisponible si la API no está accesible (para caer al modo demo)."""
    body = {
        "origin": {"location": {"latLng": {"latitude": o[0], "longitude": o[1]}}},
        "destination": {"location": {"latLng": {"latitude": d[0], "longitude": d[1]}}},
        "travelMode": modo, "languageCode": "es", "units": "METRIC",
    }
    if modo == "DRIVE":
        body["routingPreference"] = "TRAFFIC_AWARE"
    if salida:
        body["departureTime"] = salida.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = net.request("POST", ROUTES_URL, retries=1, timeout=15,
                        headers={"X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": _MASK}, json=body)
    except Exception as e:  # red caída: no rompemos la pantalla
        raise RoutesNoDisponible(type(e).__name__) from e
    if r.status_code in (400, 404):
        return None  # sin ruta posible en ese modo (p. ej. sin transporte público hasta allá)
    if r.status_code != 200:
        raise RoutesNoDisponible(f"http_{r.status_code}")
    rutas = r.json().get("routes") or []
    return rutas[0] if rutas else None


def _opciones_google(o, d, salida) -> dict:
    op = {}
    drive = _pedir_google("DRIVE", o, d, salida)
    km_linea = geo.haversine_km(o[0], o[1], d[0], d[1])
    trancon = {"nivel": "fluido", "ratio": 1.0, "extra_min": 0}
    km = km_linea * 1.3
    if drive:
        con, sin = _segundos(drive.get("duration")), _segundos(drive.get("staticDuration"))
        km = drive.get("distanceMeters", km * 1000) / 1000.0
        trancon = nivel_trancon(con, sin)
        op["taxi"] = {"min": round(con / 60), "min_sin_trafico": round(sin / 60), "km": round(km, 1),
                      "precio": precio_taxi(km, con / 60)}
    try:
        tr = _pedir_google("TRANSIT", o, d, salida)
    except RoutesNoDisponible:
        tr = None  # el carro funcionó pero el transporte falló: mostramos lo que hay
    if tr:
        op["transporte"] = parsear_transito(tr)
    if km_linea <= CAMINAR_MAX_KM:
        op["caminar"] = {"min": round(km / CAMINAR_KMH * 60), "km": round(km, 1)}
    return {"opciones": op, "trancon": trancon}


# ─────────────────────────── el chat: «¿cómo llego a…?» ───────────────────────────
_PIDE_RUTA = re.compile(
    r"(c[oó]mo (llego|voy|ir|me muevo|salgo|se llega)|como llegar|llegar a|\bir (a|al)\b|\bvoy (a|al)\b|"
    r"cu[aá]nto (tarda|demora|se demora|cuesta)|\bruta\b|tranc[oó]n|transmilenio|\bsitp\b|\btaxi\b|\buber\b|\bbus\b|"
    r"take me|llevame|quiero ir|how (do i|can i|to|long)|get to|getting to|directions|\broute\b|traffic|"
    r"comment (aller|je vais|y aller)|aller (\u00e0|au)\b|combien de temps|wie komme|wie lange|anreise|"
    r"come (arrivo|si arriva|vado)|arrivare|quanto (ci vuole|tempo)|como (chego|ir|vou)|chegar)", re.I)
_AEROPUERTO = re.compile(r"aeropuerto|airport|a[eé]roport|flughafen|aeroporto|el dorado|eldorado", re.I)
_HOTEL = re.compile(r"\b(mi hotel|my hotel|mon h[oô]tel|mein hotel|il mio hotel|meu hotel|al hotel|to the hotel|\bhotel\b)", re.I)
_GENERICAS = {"museo", "parque", "centro", "cerro", "plaza", "restaurante", "iglesia", "teatro", "mercado", "cafe",
              "calle", "avenida", "catedral", "torre", "zona", "bogota", "colombia"}


# Palabras de lugares en los otros idiomas → su forma en español (los nombres curados están en español).
# Así «museum of gold», «musée de l'or» o «Goldmuseum» llegan a «Museo del Oro».
_A_ES = {
    "museum": "museo", "musee": "museo", "museu": "museo", "museen": "museo",
    "gold": "oro", "ouro": "oro", "goldmuseum": "museo oro",
    "cathedral": "catedral", "cathedrale": "catedral", "kathedrale": "catedral", "cattedrale": "catedral",
    "square": "plaza", "place": "plaza", "platz": "plaza", "piazza": "plaza", "praca": "plaza",
    "park": "parque", "parc": "parque", "parco": "parque", "parkplatz": "parque",
    "theater": "teatro", "theatre": "teatro", "theatro": "teatro", "teatre": "teatro",
    "library": "biblioteca", "bibliotheque": "biblioteca", "bibliothek": "biblioteca", "biblioteque": "biblioteca",
    "church": "iglesia", "eglise": "iglesia", "kirche": "iglesia", "chiesa": "iglesia", "igreja": "iglesia",
    "market": "mercado", "marche": "mercado", "markt": "mercado", "mercato": "mercado",
    "hill": "cerro", "mountain": "cerro", "colline": "cerro", "berg": "cerro", "monte": "cerro",
    "garden": "jardin", "gardens": "jardin", "jardim": "jardin", "garten": "jardin", "giardino": "jardin",
    "botanical": "botanico", "botanique": "botanico", "botanischer": "botanico", "botanico": "botanico",
    "national": "nacional", "nationale": "nacional", "nazionale": "nacional",
    "modern": "moderno", "moderne": "moderno", "moderna": "moderno",
    "art": "arte", "kunst": "arte",
    "flea": "pulgas", "puces": "pulgas", "floh": "pulgas",
    "salt": "sal", "sel": "sal", "salz": "sal",
    "old": "antiguo", "downtown": "centro", "center": "centro", "centre": "centro",
    "mall": "centro comercial",
    "tower": "torre", "towers": "torre", "turm": "torre", "tour": "torre", "tur": "torre", "torre": "torre",
    "lookout": "mirador", "viewpoint": "mirador", "aussichtspunkt": "mirador",
}
# Palabras vacías de cualquier idioma: no cuentan al comparar nombres.
_VACIAS = {"de", "del", "la", "el", "los", "las", "of", "the", "des", "du", "le", "les", "l", "di", "da", "do", "das",
           "der", "die", "den", "dem", "a", "al", "en", "in", "to", "and", "y", "e", "et", "und", "o", "um", "uma", "un", "une",
           "ein", "eine", "il", "lo", "gli", "i", "mi", "my", "mon", "ma", "mein", "meu"}


def _palabras_es(txt: str) -> set[str]:
    """Palabras de contenido de un texto, con las de lugar traducidas al español y sin vacías."""
    n = re.sub(r"\bl or\b", "oro", _norm(txt))  # «musée de l'or»
    out: set[str] = set()
    for w in n.split():
        if w in _VACIAS:
            continue
        out.update(_A_ES.get(w, w).split())
    return out - _VACIAS


# ── zonas: «el centro», «downtown», «Zona T» son un ÁREA, no un lugar curado. Se rutea al punto de referencia de la zona
#    (en vez de tratarlas como «no está en la curación» y desviar al usuario a otro sitio). Patrones sobre texto normalizado. ──
ZONAS: dict[str, dict] = {
    "centro": {"nombre": "Centro Histórico (La Candelaria)", "lat": 4.5981, "lng": -74.0760,
               "pat": re.compile(r"\b(downtown|centro(?! comercial)|(?:the |to the |in the )?(?:city )?cent(?:er|re)(?! comercial)\b|"
                                 r"centro historico|centro de la ciudad|la candelaria|candelaria|old town|historic(?:al)? (?:center|centre)|"
                                 r"centre[- ]ville|centro storico|innenstadt|stadtzentrum|centro da cidade)")},
    "zona_t": {"nombre": "Zona T", "lat": 4.6670, "lng": -74.0530,
               "pat": re.compile(r"\b(zona t|zona rosa|zona viva)\b")},
    "zona_g": {"nombre": "Zona G", "lat": 4.6516, "lng": -74.0560,
               "pat": re.compile(r"\b(zona g|zona gourmet)\b")},
    "chapinero": {"nombre": "Chapinero", "lat": 4.6486, "lng": -74.0620,
                  "pat": re.compile(r"\bchapinero\b")},
    "parque_93": {"nombre": "Parque de la 93", "lat": 4.6762, "lng": -74.0488,
                  "pat": re.compile(r"\b(parque (?:de )?(?:la )?93|calle 93|93 park)\b")},
}


def zona(texto: str) -> dict | None:
    """Si el texto nombra una zona conocida, devuelve el destino {'tipo': 'lugar', ..., 'zona': clave}; si no, None."""
    n = _norm(texto or "")
    if not n:
        return None
    for clave, z in ZONAS.items():
        if z["pat"].search(n):
            return {"tipo": "lugar", "lat": z["lat"], "lng": z["lng"], "nombre": z["nombre"], "zona": clave}
    return None


def _frase_destino(mensaje: str) -> str | None:
    """El texto que sigue a «ir a / to / hacia…» (sin colas como «desde mi hotel»), o None si no hay."""
    m = _DESTINO_LIBRE.search(mensaje or "")
    if not m:
        return None
    return _COLA.sub("", m.group("d")).strip(" 	\"'") or None



def _distintivas(nombre: str) -> set[str]:
    """Palabras que identifican a un lugar: largas, no genéricas ni vacías («colpatria», «monserrate»)."""
    n = _norm(re.sub(r"\(.*?\)", "", nombre or ""))
    return {w for w in n.split() if len(w) >= 6 and w not in _GENERICAS and w not in _VACIAS and w not in _A_ES}


def pide_ruta(mensaje: str) -> bool:
    """¿El mensaje pide cómo llegar / moverse a algún sitio?"""
    return bool(mensaje and _PIDE_RUTA.search(mensaje))


def detectar_destino(mensaje: str, lugares: list[dict]) -> dict | None:
    """Si el mensaje pide cómo llegar a un sitio que conocemos, devuelve el destino:
    {'tipo': 'aeropuerto'} | {'tipo': 'hotel'} | {'tipo': 'lugar', 'lat', 'lng', 'nombre'}. Si no, None.
    Exige una palabra de transporte/ruta Y un destino reconocible (pocos falsos positivos)."""
    if not mensaje or not _PIDE_RUTA.search(mensaje):
        return None
    n = _norm(mensaje)
    palabras = set(n.split())
    msg_es = _palabras_es(mensaje)
    mejor, puntaje = None, 0
    usos: dict[str, int] = {}  # en cuántos lugares aparece cada palabra distintiva
    for p in lugares:
        for w in _distintivas(p.get("name", "")):
            usos[w] = usos.get(w, 0) + 1
    for p in lugares:
        pn = _norm(re.sub(r"\(.*?\)", "", p.get("name", "")))  # «Usaquén (centro histórico)» → «usaquen»
        if not pn:
            continue
        toks = {w for w in pn.split() if len(w) >= 5 and w not in _GENERICAS}
        score = 0
        if len(pn) >= 4 and pn in n:
            score = 100 + len(pn)                       # nombre completo en el mensaje
        elif toks and toks <= palabras:
            score = 10 * len(toks) + len(pn) / 100      # todas sus palabras distintivas aparecen
        if not score:
            cont = _palabras_es(re.sub(r"\(.*?\)", "", p.get("name", "")))
            comun = len(cont & msg_es)
            if len(cont) >= 2 and comun == len(cont):
                score = 50 + 10 * len(cont)             # «museum of gold» ⊇ {museo, oro}
            elif comun >= 2 and (len(cont) - comun <= 1 or (len(cont) >= 4 and comun / len(cont) >= 0.4)):
                score = 20 + 10 * comun                 # «salt cathedral» ≈ «Catedral de Sal de Zipaquirá»
        if not score:
            # Una palabra propia que solo tiene ESE lugar («colpatria») basta, aunque falten «mirador» o «torre».
            unicas = [w for w in _distintivas(p.get("name", "")) if usos.get(w) == 1 and (w in palabras or w in msg_es)]
            if unicas:
                score = 30 + max(len(w) for w in unicas)
        if score > puntaje:
            mejor, puntaje = p, score
    # Si el mensaje dice «a X», el destino es X: «de mi hotel al centro» no es una ruta al hotel.
    frase = _frase_destino(mensaje)
    objetivo = frase or mensaje
    z = zona(objetivo)
    # Una zona gana sobre un lugar que solo la nombra («Open Chapinero Café» no es «Chapinero»); un nombre completo, no.
    if mejor and not (z and puntaje < 100):
        return {"tipo": "lugar", "lat": float(mejor["lat"]), "lng": float(mejor["lng"]), "nombre": mejor["name"]}
    if z:
        return z
    if _AEROPUERTO.search(objetivo):
        return {"tipo": "aeropuerto"}
    if _HOTEL.search(objetivo):
        return {"tipo": "hotel"}
    return None


# ── destino «libre»: el usuario nombra un sitio que NO está en nuestra curación. No se calcula ruta (el valor de
#    Companion es recomendar lugares curados): solo se detecta para que el Companion lo diga y ofrezca alternativas. ──
_DESTINO_LIBRE = re.compile(
    r"(?:\b(?:ir|llegar|llego|voy|vamos|iré|quiero ir|quiero llegar)\s+(?:a la|a los|a las|al|a|hasta|hacia|para)\s+"
    r"|\b(?:go to|get to|going to|get me to|take me to|travel to|to the|directions to|route to)\s+"
    r"|\baller (?:à la|à l'|au|aux|à)\s+|\b(?:nach|zum|zur|zu)\s+|\b(?:andare|arrivare)\s+(?:a|al|alla|allo)\s+"
    r"|\b(?:ir|chegar|vou)\s+(?:para o|para a|para|ao|à|a)\s+)"
    r"(?P<d>[^?.!,;¿¡\n]{3,70})", re.I)
_COLA = re.compile(
    r"\s+(?:desde|saliendo|partiendo|en (?:taxi|bus|uber|carro|transmilenio|transporte|metro|moto)|por favor|"
    r"con |sin |hoy|ahora|mañana|esta (?:noche|tarde|mañana)|from|by |in (?:a |an )?(?:taxi|uber|bus|car)|please|"
    r"today|now|tonight|tomorrow|depuis|en (?:taxi|bus|voiture)|heute|jetzt|da |con il|oggi|agora|hoje|de (?:táxi|ônibus)).*$",
    re.I)
_NO_LUGAR = {"casa", "alla", "allá", "alli", "allí", "ahi", "ahí", "aqui", "aquí", "there", "here", "home", "work",
             "trabajo", "el", "la", "un", "una", "the", "my hotel", "mi hotel", "hotel", "airport", "aeropuerto"}


def destino_libre(mensaje: str) -> str | None:
    """Texto del sitio al que quiere ir cuando no es uno curado, o None. Exige una palabra de ruta/transporte."""
    if not mensaje or not _PIDE_RUTA.search(mensaje) and not _DESTINO_LIBRE.search(mensaje):
        return None
    m = _DESTINO_LIBRE.search(mensaje)
    if not m:
        return None
    d = _COLA.sub("", m.group("d")).strip(" 	\"'")
    n = _norm(d)
    if len(re.sub(r"[^A-Za-zÁ-ú]", "", d)) < 3 or n in _NO_LUGAR or n.split()[0] in ("mi", "my", "mon", "ma", "mein", "meu", "minha", "il"):
        return None  # «mi casa», «my place»: no sabemos dónde es
    return d


_MOTIVOS = {
    "cerca": "está cerca, caminar es lo más rápido",
    "trancon_gana_transporte": "con el trancón de ahora el transporte público le gana al taxi",
    "similar_y_mas_barato": "tarda casi lo mismo que el taxi y sale mucho más barato",
    "taxi_mas_rapido": "el taxi es bastante más rápido en este trayecto",
    "solo_taxi": "es la única opción disponible",
    "solo_transporte": "es la única opción disponible",
}
_MODOS = {"taxi": "taxi/app", "transporte": "transporte público", "caminar": "a pie"}


def _miles(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def bloque_chat(r: dict) -> str:
    """Resumen de una ruta REAL (Google) para el prompt del Companion. Solo con datos que sí tenemos."""
    op = r["opciones"]
    L = [f"RUTAS EN VIVO (Google, tráfico de AHORA) de «{r['origen']['nombre'] or 'su ubicación'}» a «{r['destino']['nombre']}»:"]
    tx = op.get("taxi")
    if tx:
        L.append(f"- Taxi/app: {tx['min']} min con tráfico (normalmente {tx['min_sin_trafico']}), unos {tx['km']} km, "
                 f"precio ESTIMADO {_miles(tx['precio']['min'])}–{_miles(tx['precio']['max'])} COP.")
    tr = op.get("transporte")
    if tr:
        tramos = [f"{p.get('vehiculo') or 'Bus'} {p.get('linea', '')} ({p.get('desde', '')} → {p.get('hasta', '')})".replace("  ", " ")
                  for p in tr["pasos"] if p["tipo"] == "transporte"]
        detalle = " + ".join(tramos) if tramos else "sin detalle de líneas"
        L.append(f"- Transporte público: {tr['min']} min · {detalle} · {tr['transbordos']} transbordo(s) · "
                 f"caminata total {tr['caminata_min']} min.")
    if op.get("caminar"):
        L.append(f"- A pie: {op['caminar']['min']} min ({op['caminar']['km']} km).")
    tf = r["trancon"]
    L.append(f"- Tráfico ahora: {tf['nivel']} (+{tf['extra_min']} min sobre lo normal)." + (" Es hora pico: TransMilenio va muy lleno." if r.get("hora_pico") else ""))
    rec = r["recomendacion"]
    if rec["modo"]:
        L.append(f"- Conviene: {_MODOS[rec['modo']]} ({_MOTIVOS.get(rec['motivo'], '')}).")
    L.append("REGLA: si el usuario pregunta cómo llegar, responde con ESTOS datos (tiempos, precio estimado, líneas) y "
             "recomienda UNA opción con criterio de local. No inventes líneas, estaciones ni precios que no estén aquí; "
             "el precio del taxi es una estimación, dilo así.")
    return "\n".join(L)


# ─────────────────────────── termómetro de tráfico ───────────────────────────
CORREDORES = [
    {"id": "norte_centro", "o": (4.6860, -74.0560), "d": (4.5981, -74.0758)},
    {"id": "aeropuerto_centro", "o": (4.7016, -74.1469), "d": (4.5981, -74.0758)},
    {"id": "usaquen_zonat", "o": (4.6950, -74.0310), "d": (4.6680, -74.0530)},
    {"id": "chapinero_aeropuerto", "o": (4.6486, -74.0620), "d": (4.7016, -74.1469)},
]
# Una sola medición compartida por TODOS los viajeros: el costo no crece con los usuarios.
_cache_trafico = net.TTLCache(ttl_s=float(os.environ.get("TRAFICO_TTL_S", "1200")), maxsize=2)
_lock_trafico = threading.Lock()


def trafico() -> dict:
    """Estado del tráfico en 4 corredores de Bogotá (carro, con tráfico real). Se refresca a lo sumo cada
    TRAFICO_TTL_S (20 min) y solo cuando alguien lo pide. Sin Routes API: {'disponible': False} (no se inventa nada)."""
    if not disponible():
        return {"disponible": False, "corredores": []}
    hit = _cache_trafico.get("t")
    if hit is not None:
        return hit
    with _lock_trafico:
        hit = _cache_trafico.get("t")
        if hit is not None:
            return hit
        corredores = []
        try:
            for c in CORREDORES:
                r = _pedir_google("DRIVE", c["o"], c["d"], None)
                if not r:
                    continue
                con, sin = _segundos(r.get("duration")), _segundos(r.get("staticDuration"))
                n = nivel_trancon(con, sin)
                corredores.append({"id": c["id"], "min": round(con / 60), **n})
        except RoutesNoDisponible as e:
            log.warning("trafico_no_disponible motivo=%s", e)
            res = {"disponible": False, "corredores": []}
            _cache_trafico.set("t", res, ttl_s=120)  # no martillamos a Google si está caído
            return res
        if not corredores:
            return {"disponible": False, "corredores": []}
        con_prom = sum(c["min"] for c in corredores) / len(corredores) * 60
        sin_prom = sum(c["min"] - c["extra_min"] for c in corredores) / len(corredores) * 60
        general = nivel_trancon(con_prom, sin_prom)
        res = {"disponible": True, "actualizado": datetime.datetime.now(TZ).isoformat(timespec="minutes"),
               "general": {"nivel": general["nivel"], "ratio": general["ratio"], "extra_min": general["extra_min"]},
               "corredores": corredores}
        _cache_trafico.set("t", res)
        return res


# ─────────────────────────── alertas de movilidad (paros, marchas, bloqueos) ───────────────────────────
_CLAVE_MOVILIDAD = re.compile(
    r"\b(paro|paros|marcha|marchas|manifestaci|protesta|bloqueo|bloqueos|plant[oó]n|disturbio|cierre|cierres|"
    r"cerrad[ao]|desv[ií]o|transmilenio|sitp|trancon|trancón|movilidad|pico y placa)", re.I)
_ES_BOGOTA = re.compile(r"bogot[aá]|transmilenio|sitp|distrito capital", re.I)
_cache_alertas = net.TTLCache(ttl_s=float(os.environ.get("ALERTAS_TTL_S", "10800")), maxsize=2)
_lock_alertas = threading.Lock()


def filtrar_alertas(resultados: list[dict], max_n: int = 3) -> list[dict]:
    """De los resultados de búsqueda deja solo lo que habla de movilidad EN Bogotá. Sin duplicados por URL/título."""
    vistos, out = set(), []
    for r in resultados:
        titulo, cuerpo = (r.get("title") or "").strip(), (r.get("content") or "").strip()
        texto = f"{titulo} {cuerpo}"
        if not titulo or not _CLAVE_MOVILIDAD.search(texto) or not _ES_BOGOTA.search(texto):
            continue
        k = (r.get("url") or titulo).lower()
        if k in vistos:
            continue
        vistos.add(k)
        out.append({"titulo": titulo[:140], "resumen": cuerpo[:220].rstrip(), "url": r.get("url", ""),
                    "fecha": (r.get("fecha") or "")[:10]})
        if len(out) >= max_n:
            break
    return out


def alertas() -> dict:
    """Paros, marchas y cierres que afectan moverse por Bogotá en los últimos 2 días (Tavily, noticias).
    Una sola búsqueda compartida por todos los viajeros, cacheada ALERTAS_TTL_S (3 h). Sin Tavily: lista vacía."""
    from . import search
    if search.MODE == "mock":
        return {"disponible": False, "alertas": []}
    hit = _cache_alertas.get("a")
    if hit is not None:
        return hit
    with _lock_alertas:
        hit = _cache_alertas.get("a")
        if hit is not None:
            return hit
        crudos = search.search("Bogotá paro marcha manifestación bloqueo cierre vías TransMilenio hoy",
                               max_results=8, topic="news", days=2)
        res = {"disponible": True, "actualizado": datetime.datetime.now(TZ).isoformat(timespec="minutes"),
               "alertas": filtrar_alertas(crudos)}
        _cache_alertas.set("a", res)
        return res


_PREGUNTA_MOVILIDAD = re.compile(
    r"\b(paro|marcha|manifestaci|protesta|bloqueo|plant[oó]n|strike|protest|demonstration|gr[eè]ve|streik|sciopero|"
    r"transmilenio|trancon|trancón|tr[aá]fico|traffic|how do i get|c[oó]mo llego|como chego|comment aller|wie komme)", re.I)


def bloque_alertas(a: dict) -> str:
    """Alertas para el prompt del Companion (solo si hay). Son titulares de prensa: se citan como tal, sin exagerar."""
    if not a.get("alertas"):
        return ""
    L = ["ALERTAS DE MOVILIDAD EN BOGOTÁ (titulares de prensa de los últimos 2 días; pueden no afectar la ruta del usuario):"]
    for x in a["alertas"]:
        L.append(f"- {x['titulo']}" + (f" ({x['fecha']})" if x["fecha"] else ""))
    L.append("REGLA: si el usuario va a moverse o pregunta por paros/tráfico, menciónalo brevemente y con cautela "
             "(«según prensa de hoy…»), sugiere confirmar antes de salir. No lo menciones si no es relevante a su pregunta.")
    return "\n".join(L)


# ─────────────────────────── modo demo (sin Routes API) ───────────────────────────
def _opciones_demo(o, d, ahora: datetime.datetime) -> dict:
    """Estimaciones por distancia, para probar la pantalla sin la API. NO inventa líneas ni estaciones."""
    km = geo.haversine_km(o[0], o[1], d[0], d[1]) * 1.3
    pico = hora_pico(ahora)
    kmh_libre, kmh_real = 32.0, (13.0 if pico else 20.0)
    sin, con = km / kmh_libre * 3600, km / kmh_real * 3600
    op = {"taxi": {"min": round(con / 60), "min_sin_trafico": round(sin / 60), "km": round(km, 1),
                   "precio": precio_taxi(km, con / 60)}}
    caminata = min(15.0, 6.0 + km * 0.8)
    op["transporte"] = {"min": round(km / 17.0 * 60 + caminata + 6), "caminata_min": round(caminata),
                        "transbordos": 1 if km > 6 else 0, "pasos": []}
    if km <= CAMINAR_MAX_KM:
        op["caminar"] = {"min": round(km / CAMINAR_KMH * 60), "km": round(km, 1)}
    return {"opciones": op, "trancon": nivel_trancon(con, sin)}


# ─────────────────────────── entrada principal ───────────────────────────
def rutas(origen: tuple[float, float, str], destino: tuple[float, float, str], salida_min: int = 0) -> dict:
    """Compara las formas de llegar de `origen` a `destino` (lat, lng, nombre). `salida_min`: en cuántos
    minutos sale el viajero (0 = ahora)."""
    salida_min = max(0, min(int(salida_min), 24 * 60))
    ahora = datetime.datetime.now(TZ) + datetime.timedelta(minutes=salida_min)
    o, d = (origen[0], origen[1]), (destino[0], destino[1])
    clave = (round(o[0], 3), round(o[1], 3), round(d[0], 3), round(d[1], 3), salida_min // 15)
    hit = _cache.get(clave)
    if hit is not None:
        data = dict(hit)
    else:
        fuente = "demo"
        data = None
        if disponible():
            try:
                data = _opciones_google(o, d, ahora if salida_min else None)
                fuente = "google"
            except RoutesNoDisponible as e:
                log.warning("routes_no_disponible motivo=%s (modo demo)", e)
        if data is None:
            data = _opciones_demo(o, d, ahora)
        data["fuente"] = fuente
        _cache.set(clave, data)
    rec = recomendar(data["opciones"], data["trancon"]["nivel"])
    return {
        **data,
        "origen": {"lat": o[0], "lng": o[1], "nombre": origen[2]},
        "destino": {"lat": d[0], "lng": d[1], "nombre": destino[2]},
        "salida_min": salida_min,
        "hora_pico": hora_pico(ahora),
        "recomendacion": rec,
        "enlaces": enlaces(o, d, destino[2]),
    }
