"""Descubre lugares NUEVOS para la curación de Bogotá combinando Google Places y Tavily.

Paso 3 del plan de curaduría (después de verificar_lugares.py y del dato local). Sin modelos de
lenguaje: Google aporta la verdad estructurada y Tavily la corroboración editorial.

    1. DESCUBRIR (Google Places, Text Search): por cada zona × tipo de lugar ("cafés de
       especialidad en Chapinero"…) trae lugares reales con rating, reseñas, estado y horario.
       Filtra: operativos, rating y volumen de reseñas mínimos, dentro de Bogotá y de la zona,
       y que NO estén ya en la curación (por place_id o por nombre parecido) ni excluidos.
    2. CORROBORAR (Tavily): para los mejores candidatos busca «<nombre> <zona> Bogotá» y cuenta
       en cuántos DOMINIOS distintos se menciona (guías, prensa, blogs) y si algún texto que lo
       nombra habla de cierre. Guarda las fuentes y los extractos como evidencia.
    3. CLASIFICAR: «recomendado» (≥ 2 dominios, sin señal de cierre), «dudoso» (1 dominio o sin
       evidencia) y «descartado por cierre».
    4. REPORTE para revisión humana: reportes/candidatos_bogota_<fecha>.{json,md}. NADA entra a la
       curación sin pasar por aprobar_candidatos.py (regla de confianza de Voyra).

    # Las keys van en .env junto a este script: GOOGLE_MAPS_API_KEY y TAVILY_API_KEY
    python descubrir_lugares.py --dry-run                 # muestra el plan y el costo, no llama a nada
    python descubrir_lugares.py                           # corrida completa (límites por defecto)
    python descubrir_lugares.py --zonas "Usaquén,Chicó" --tipos "cafes,veganos"
    python descubrir_lugares.py --max-tavily 25           # gasta menos cuota de Tavily

Costo orientativo: ~100 consultas de Google (≈ 3 USD, dentro del cupo gratuito mensual) y hasta
40 búsquedas de Tavily (de las 1.000 gratis al mes, que comparte el escáner de los viajeros).
"""
import argparse
import datetime
import json
import math
import os
import re
import sys
import time
import unicodedata
from urllib.parse import urlparse

import httpx

from app import geo
from verificar_lugares import _nombres_parecidos, cargar_env

cargar_env()
GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
TAVILY_URL = "https://api.tavily.com/search"

MASK = ",".join("places." + f for f in [
    "id", "displayName", "formattedAddress", "location", "businessStatus", "rating", "userRatingCount",
    "priceLevel", "primaryType", "types", "googleMapsUri", "websiteUri",
    "regularOpeningHours.weekdayDescriptions"])

# Centro y radio de cada zona (m). El filtro final exige estar a ≤ RADIO_MAX_M del centro.
ZONAS = {
    "Chapinero": (4.6486, -74.0620), "Chapinero Alto": (4.6420, -74.0610), "Zona G": (4.6580, -74.0570),
    "Zona T": (4.6680, -74.0530), "Quinta Camacho": (4.6540, -74.0630), "Usaquén": (4.6950, -74.0310),
    "Chicó": (4.6780, -74.0470), "La Candelaria": (4.5981, -74.0758), "La Macarena": (4.6120, -74.0680),
    "Teusaquillo": (4.6350, -74.0780), "Rosales": (4.6600, -74.0490), "Parque 93": (4.6760, -74.0480),
}
RADIO_BIAS_M = 1800.0
RADIO_MAX_M = 2500

# tipo → (categoría de la curación, consultas de Google). La consulta se completa con « en <zona> Bogotá».
TIPOS = {
    "restaurantes": ("restaurante", ["mejores restaurantes", "restaurantes de cocina colombiana"]),
    "veganos": ("restaurante", ["restaurantes vegetarianos y veganos"]),
    "cafes": ("cafe", ["cafés de especialidad"]),
    "panaderias": ("cafe", ["panaderías y pastelerías"]),
    "bares": ("bar", ["bares de coctelería", "bares con música en vivo"]),
    "cultura": ("atraccion", ["museos y galerías de arte"]),
    "parques": ("parque", ["parques y jardines"]),
    "mercados": ("mercado", ["mercados y plazas de mercado"]),
    # Para cenar tarde (más comida típica abierta de noche): se usan con --cierra-despues 22:30
    "colombiano": ("restaurante", ["restaurantes de comida típica colombiana", "restaurantes de cocina criolla y parrilla"]),
    "nocturno": ("restaurante", ["restaurantes abiertos hasta tarde para cenar"]),
}

# Umbrales (ajustables por línea de comandos)
RATING_MIN = 4.4
RESENAS_MIN = 150
DOMINIOS_MIN = 2          # menciones en dominios distintos para ser «recomendado»
# Evidencia fuerte de Google: un lugar con esto, sin señal de cierre, también es «recomendado» aunque casi no tenga
# menciones (la mayoría de los restaurantes buenos aparecen en una sola guía, no en dos).
RATING_FUERTE = 4.5
RESENAS_FUERTE = 300
SENAL_CIERRE = re.compile(r"cerr[óo]|cierre definitivo|cerrado definitivamente|permanently closed|"
                          r"closed permanently|ya no existe|clausur", re.I)

# Google primaryType → categoría de la curación
_CAT_POR_TIPO = [
    (("coffee_shop", "cafe", "bakery", "pastry_shop", "dessert_shop", "ice_cream_shop", "tea_house"), "cafe"),
    (("bar", "pub", "night_club", "wine_bar", "cocktail_bar", "live_music_venue"), "bar"),
    (("museum", "art_gallery", "tourist_attraction", "church", "cultural_center", "historical_landmark",
      "performing_arts_theater", "library", "plaza"), "atraccion"),
    (("park", "garden", "national_park", "botanical_garden"), "parque"),
    (("market", "farmers_market", "flea_market"), "mercado"),
]


# ─────────────────────────── lógica pura (con tests) ───────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def categoria_de(primary: str, tipos: list[str], fallback: str) -> str:
    """Categoría de la curación según los tipos de Google; si no hay pista clara, la de la consulta."""
    for t in ([primary] if primary else []):
        if t.endswith("restaurant"):
            return "restaurante"
        for grupo, cat in _CAT_POR_TIPO:
            if t in grupo:
                return cat
    # Sin tipo principal reconocible: miramos el resto de tipos (restaurante antes que bar).
    resto = list(tipos or [])
    if any(t and t.endswith("restaurant") for t in resto):
        return "restaurante"
    for grupo, cat in _CAT_POR_TIPO:
        if any(t in grupo for t in resto):
            return cat
    return fallback


# Tipos que no son «lugares para recomendar a un viajero»
TIPOS_RUIDO = {"supermarket", "grocery_store", "convenience_store", "school", "primary_school", "secondary_school",
               "university", "gym", "fitness_center", "lodging", "hotel", "car_dealer", "car_repair", "store",
               "clothing_store", "electronics_store", "pharmacy", "hospital", "doctor", "dentist", "bank",
               "real_estate_agency", "insurance_agency", "lawyer", "meal_takeaway_only"}


def cierra_despues(horarios: list[str], limite: str = "22:30", dias_min: int = 3) -> bool:
    """¿Cierra a las `limite` o más tarde (la madrugada cuenta como tarde) al menos `dias_min` días de la semana?
    `horarios` = weekdayDescriptions de Google («viernes: 12:00–23:00»). Abierto 24 h cuenta."""
    lh, lm = (int(x) for x in limite.split(":"))
    tarde = 0
    for ln in horarios or []:
        d = ln.split(":", 1)[1] if ":" in ln else ln
        if re.search(r"24 horas|abierto 24", d, re.I):
            tarde += 1
            continue
        cierres = re.findall(r"[–-]\s*(\d{1,2})(?::(\d{2}))?", d)
        if not cierres:
            continue
        h, m = int(cierres[-1][0]), int(cierres[-1][1] or 0)
        h = h + 24 if h < 6 else h           # 1:00 = una de la madrugada: después de medianoche
        if (h, m) >= (lh, lm):
            tarde += 1
    return tarde >= dias_min


def es_ruido(g: dict, zonas: list[str]) -> bool:
    """Descarta lo que no es un lugar recomendable: nombre de zona, tiendas, colegios, hoteles, gimnasios…"""
    tipos = {g.get("primary", "")} | set(g.get("tipos") or [])
    if g.get("primary") in TIPOS_RUIDO:
        return True
    n = _norm(g.get("nombre", ""))
    if any(n in (_norm(z), _norm(z) + " bogota") for z in zonas):
        return True
    # Un tipo de ruido sin ninguna pista de ser gastronómico/cultural/ocio también se descarta
    util = ("restaurant", "cafe", "coffee_shop", "bakery", "bar", "museum", "park", "tourist_attraction",
            "night_club", "art_gallery", "market")
    return bool(tipos & TIPOS_RUIDO) and not any(t and any(u in t for u in util) for t in tipos)


def puntaje(rating: float | None, resenas: int | None) -> float:
    """Ordena candidatos: rating pesado por (el log del) volumen de reseñas."""
    if not rating or not resenas:
        return 0.0
    return rating * math.log10(resenas + 1)


def es_duplicado(nombre: str, place_id: str, curados: dict) -> bool:
    """¿Ya está en la curación (o excluido)? curados = {'ids': set, 'nombres': [str]}."""
    if place_id and place_id in curados["ids"]:
        return True
    return any(_nombres_parecidos(nombre, n) and _norm(nombre)[:5] == _norm(n)[:5] for n in curados["nombres"])


# Redes, mapas y agregadores de reservas: confirman que el local existe, pero NO son una recomendación
# independiente. La web propia del local tampoco cuenta.
NO_INDEPENDIENTES = {"instagram.com", "facebook.com", "tiktok.com", "youtube.com", "twitter.com", "x.com",
                     "linktr.ee", "waze.com", "maps.app.goo.gl", "google.com", "goo.gl", "resy.com", "cluvi.co",
                     "my.matterport.com", "eventario.co", "wa.me", "whatsapp.com", "pinterest.com"}


# Listados, domicilios y reservas: sirven para confirmar que existe, no son una recomendación editorial.
LISTADOS = {"rappi.com.co", "rappi.com", "degusta.com.co", "restaurantguru.com", "es.restaurantguru.com",
            "menupp.co", "menu.apparta.co", "restaurante.covermanager.com", "covermanager.com", "widget.riservi.com",
            "mapquest.com", "trip.com", "us.trip.com", "mindtrip.ai", "saborygusto.com", "queresto.com",
            "tikipal.com.co", "evendo.com", "ineventos.com", "jetlygo.com", "cielo.travel", "finediningtable.com",
            "ubereats.com", "didi-food.com", "domicilios.com", "yelp.com", "foursquare.com", "tiktok.com"}


def familia(dom: str) -> str:
    """tripadvisor.es y en.tripadvisor.com.hk son la MISMA fuente: se cuenta por la etiqueta principal."""
    partes = dom.split(".")
    ccsld = {"com", "co", "org", "net", "edu", "gov"}
    if len(partes) >= 3 and partes[-2] in ccsld:
        return partes[-3]
    return partes[-2] if len(partes) >= 2 else dom


_PALABRAS_COMUNES = {"bar", "the", "los", "las", "del", "con", "por", "casa", "cafe", "restaurante"}


def es_propio_o_red(dom: str, nombre: str) -> bool:
    if any(dom == d or dom.endswith("." + d) for d in NO_INDEPENDIENTES | LISTADOS):
        return True
    cuerpo = _norm(dom.rsplit(".", 1)[0]).replace(" ", "")
    return any(len(w) >= 3 and w not in _PALABRAS_COMUNES and w in cuerpo for w in _norm(nombre).split())


def dominio(url: str) -> str:
    h = (urlparse(url).netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def corroborar(nombre: str, resultados: list[dict]) -> dict:
    """De los resultados de Tavily, solo cuentan los textos que NOMBRAN al lugar.
    Devuelve dominios distintos, fuentes, extractos y si algún texto habla de cierre."""
    clave = _norm(nombre)
    dominios, fuentes, extractos, cierre, otras = set(), [], [], False, set()
    for r in resultados:
        texto = f"{r.get('title', '')} {r.get('content', '')}"
        tn = _norm(texto)
        if clave not in tn or "bogot" not in tn:
            continue  # sin «Bogotá» en el texto suele ser otra cosa con el mismo nombre (una canción, un periódico…)
        d = dominio(r.get("url", ""))
        if SENAL_CIERRE.search(texto):
            cierre = True
        if es_propio_o_red(d, nombre):
            otras.add(d)  # existe, pero no cuenta como recomendación independiente
            continue
        if familia(d) in {familia(x) for x in dominios}:
            continue  # misma fuente con otro dominio (tripadvisor.es / tripadvisor.com)
        dominios.add(d)
        fuentes.append({"dominio": d, "url": r.get("url", ""), "titulo": (r.get("title") or "")[:120]})
        extractos.append((r.get("content") or "")[:320])
    return {"dominios": sorted(dominios), "menciones": len(dominios), "cierre": cierre,
            "fuentes": fuentes[:4], "extractos": extractos[:3], "otras": sorted(otras)}


def clasificar(corro: dict, dominios_min: int = DOMINIOS_MIN, g: dict | None = None) -> str:
    """recomendado: 2+ fuentes independientes, o (con `g`) rating y reseñas fuertes en Google. Cualquier señal de cierre
    lo descarta."""
    if corro["cierre"]:
        return "descartado_cierre"
    if corro["menciones"] >= dominios_min:
        return "recomendado"
    if g and (g.get("rating") or 0) >= RATING_FUERTE and (g.get("resenas") or 0) >= RESENAS_FUERTE:
        return "recomendado"
    return "dudoso"


def motivo(corro: dict, estado: str) -> str:
    """Por qué quedó como recomendado: «fuentes» (2+ independientes) o «google» (solo evidencia de Google)."""
    if estado != "recomendado":
        return ""
    return "fuentes" if corro["menciones"] >= DOMINIOS_MIN else "google"


def a_entrada(g: dict, cat: str, zona: str, corro: dict, estado: str) -> dict:
    """Candidato en formato de la curación (descripcion queda para redactarse al revisar)."""
    return {
        "city_display": "Bogotá", "name": g["nombre"], "category": cat, "zona": zona,
        "dir": g.get("direccion", ""), "maps_query": f"{g['nombre']} Bogotá",
        "lat": round(g["lat"], 6), "lng": round(g["lng"], 6), "place_id": g["place_id"],
        "rating": g.get("rating"), "price_level": g.get("price_level"),
        "descripcion": "", "confianza": "media", "tags": [],
        "_evidencia": {"estado": estado, "resenas": g.get("resenas"), "menciones": corro["menciones"],
                       "dominios": corro["dominios"], "fuentes": corro["fuentes"],
                       "extractos": corro["extractos"], "horarios": g.get("horarios", []),
                       "web": g.get("web", ""), "maps": g.get("maps", "")},
    }


def resumen_md(entradas: list[dict]) -> str:
    por = {"recomendado": [], "dudoso": [], "descartado_cierre": []}
    for e in entradas:
        por[e["_evidencia"]["estado"]].append(e)
    L = [f"# Candidatos nuevos para la curación de Bogotá — {datetime.date.today().isoformat()}",
         f"_{len(entradas)} candidatos: {len(por['recomendado'])} recomendados, {len(por['dudoso'])} dudosos, "
         f"{len(por['descartado_cierre'])} descartados por señal de cierre._", "",
         "Para aprobar: redacta la `descripcion` en el JSON y corre `python aprobar_candidatos.py --archivo <json> --aprobar \"Nombre\"`.", ""]
    for titulo, clave in (("✅ Recomendados", "recomendado"), ("🟡 Dudosos (1 sola fuente o ninguna)", "dudoso"),
                          ("🔴 Descartados: algún texto habla de cierre", "descartado_cierre")):
        if not por[clave]:
            continue
        L += [f"## {titulo}", ""]
        for e in sorted(por[clave], key=lambda x: -puntaje(x.get("rating"), x["_evidencia"]["resenas"])):
            ev = e["_evidencia"]
            L.append(f"### {e['name']} — {e['zona']} · {e['category']}")
            L.append(f"- ★{e.get('rating')} ({ev['resenas']} reseñas) · {e['dir']}")
            if ev.get("motivo") == "google":
                L.append("- Recomendado por evidencia de Google (rating y reseñas fuertes), sin 2 fuentes independientes: revisa antes de aprobar.")
            if ev["dominios"]:
                L.append(f"- Recomendado/mencionado en {ev['menciones']} sitio(s) independiente(s): {', '.join(ev['dominios'])}")
            for f in ev["fuentes"][:2]:
                L.append(f"  - [{f['titulo'] or f['dominio']}]({f['url']})")
            for x in ev["extractos"][:1]:
                L.append(f"  - «{x.strip()}»")
            if ev.get("maps"):
                L.append(f"- [Google Maps]({ev['maps']})")
            L.append("")
    return "\n".join(L) + "\n"


# ─────────────────────────── acceso a Google y Tavily ───────────────────────────
CACHE_RUTA = "reportes/cache_descubrir.json"
_cache: dict = {}


def _cargar_cache() -> None:
    try:
        with open(CACHE_RUTA, encoding="utf-8") as f:
            _cache.update(json.load(f))
    except (OSError, ValueError):
        pass


def _guardar_cache() -> None:
    os.makedirs("reportes", exist_ok=True)
    with open(CACHE_RUTA, "w", encoding="utf-8") as f:
        json.dump(_cache, f, ensure_ascii=False)


def google_buscar(cl: httpx.Client, consulta: str, centro: tuple[float, float]) -> list[dict]:
    clave = f"g|{consulta}"
    if clave in _cache:
        return _cache[clave]
    res = _google_buscar(cl, consulta, centro)
    _cache[clave] = res
    return res


def _google_buscar(cl: httpx.Client, consulta: str, centro: tuple[float, float]) -> list[dict]:
    for intento in range(4):
        r = cl.post(SEARCH_URL, headers={"X-Goog-Api-Key": GOOGLE_KEY, "X-Goog-FieldMask": MASK},
                    json={"textQuery": consulta, "languageCode": "es", "regionCode": "CO", "maxResultCount": 20,
                          "locationBias": {"circle": {"center": {"latitude": centro[0], "longitude": centro[1]},
                                                      "radius": RADIO_BIAS_M}}})
        if r.status_code == 429:
            time.sleep((2 ** intento) * 2)
            continue
        r.raise_for_status()
        return r.json().get("places", [])
    return []


def normalizar(p: dict) -> dict:
    loc = p.get("location") or {}
    return {"place_id": p.get("id", ""), "nombre": (p.get("displayName") or {}).get("text", ""),
            "direccion": p.get("formattedAddress", ""), "lat": loc.get("latitude"), "lng": loc.get("longitude"),
            "estado": p.get("businessStatus", ""), "rating": p.get("rating"), "resenas": p.get("userRatingCount"),
            "price_level": p.get("priceLevel"), "primary": p.get("primaryType", ""), "tipos": p.get("types", []),
            "web": p.get("websiteUri", ""), "maps": p.get("googleMapsUri", ""),
            "horarios": (p.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []}


def tavily_buscar(cl: httpx.Client, consulta: str) -> list[dict]:
    clave = f"t|{consulta}"
    if clave in _cache:
        return _cache[clave]
    res = _tavily_buscar(cl, consulta)
    _cache[clave] = res
    return res


def _tavily_buscar(cl: httpx.Client, consulta: str) -> list[dict]:
    r = cl.post(TAVILY_URL, json={"api_key": TAVILY_KEY, "query": consulta, "topic": "general",
                                  "search_depth": "basic", "max_results": 6, "include_answer": False})
    r.raise_for_status()
    return r.json().get("results", [])


def cargar_curados() -> dict:
    """place_id y nombres de lo que YA está en la curación (incluye ajustes y excluidos de la capa)."""
    from app import seed_data
    ids, nombres = set(), []
    for p in seed_data.all_seeds():
        if p.get("city_display") == "Bogotá":
            nombres.append(p["name"])
            if p.get("place_id"):
                ids.add(p["place_id"])
    capa = seed_data._capa_verificacion()
    nombres += list(capa["excluir"])  # los cerrados no deben «redescubrirse» como nuevos
    return {"ids": ids, "nombres": nombres}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Descubre lugares nuevos (Google Places + Tavily). Solo genera un reporte.")
    ap.add_argument("--zonas", default="", help="Lista separada por comas (por defecto, todas)")
    ap.add_argument("--tipos", default="", help=f"Lista separada por comas de: {', '.join(TIPOS)}")
    ap.add_argument("--rating-min", type=float, default=RATING_MIN)
    ap.add_argument("--resenas-min", type=int, default=RESENAS_MIN)
    ap.add_argument("--max-google", type=int, default=120, help="Tope de consultas a Google")
    ap.add_argument("--max-tavily", type=int, default=40, help="Tope de búsquedas a Tavily")
    ap.add_argument("--dry-run", action="store_true", help="Solo muestra el plan y el costo")
    ap.add_argument("--cierra-despues", default="", metavar="HH:MM", help="Solo lugares que cierran a esta hora o más tarde (≥3 días)")
    ap.add_argument("--prefijo", default="candidatos_bogota", help="Nombre base del reporte en reportes/ (no pisa el de siempre)")
    args = ap.parse_args()

    zonas = [z.strip() for z in args.zonas.split(",") if z.strip()] or list(ZONAS)
    tipos = [t.strip() for t in args.tipos.split(",") if t.strip()] or list(TIPOS)
    malos = [z for z in zonas if z not in ZONAS] + [t for t in tipos if t not in TIPOS]
    if malos:
        sys.exit(f"No reconozco: {malos}. Zonas: {list(ZONAS)}. Tipos: {list(TIPOS)}")

    consultas = [(z, t, q) for z in zonas for t in tipos for q in TIPOS[t][1]][: args.max_google]
    print(f"Plan: {len(consultas)} consultas a Google (≈ {len(consultas) * 0.035:.1f} USD) y hasta "
          f"{args.max_tavily} búsquedas a Tavily.")
    if args.dry_run:
        for z, t, q in consultas[:8]:
            print(f"   · {q} en {z} Bogotá")
        print("   …")
        return
    if not GOOGLE_KEY or not TAVILY_KEY:
        sys.exit("Faltan GOOGLE_MAPS_API_KEY y/o TAVILY_API_KEY (ponlas en .env).")

    curados = cargar_curados()
    _cargar_cache()
    candidatos: dict[str, dict] = {}   # place_id → {g, cat, zona}
    with httpx.Client(timeout=25) as cl:
        for i, (z, t, q) in enumerate(consultas, 1):
            try:
                lugares = google_buscar(cl, f"{q} en {z} Bogotá", ZONAS[z])
            except Exception as e:
                print(f"[{i}/{len(consultas)}] {q} en {z}: error {e}")
                continue
            nuevos = 0
            for raw in lugares:
                g = normalizar(raw)
                if not g["place_id"] or g["place_id"] in candidatos or g["lat"] is None:
                    continue
                if g["estado"] != "OPERATIONAL" or (g["rating"] or 0) < args.rating_min \
                        or (g["resenas"] or 0) < args.resenas_min:
                    continue
                if geo.haversine_km(ZONAS[z][0], ZONAS[z][1], g["lat"], g["lng"]) * 1000 > RADIO_MAX_M:
                    continue
                if args.cierra_despues and not cierra_despues(g.get("horarios") or [], args.cierra_despues):
                    continue
                if es_ruido(g, list(ZONAS)) or es_duplicado(g["nombre"], g["place_id"], curados):
                    continue
                candidatos[g["place_id"]] = {"g": g, "zona": z,
                                             "cat": categoria_de(g["primary"], g["tipos"], TIPOS[t][0])}
                nuevos += 1
            print(f"[{i}/{len(consultas)}] {q} en {z}: {len(lugares)} resultados, {nuevos} candidatos nuevos")
            time.sleep(0.25)
        _guardar_cache()

        orden = sorted(candidatos.values(), key=lambda c: -puntaje(c["g"]["rating"], c["g"]["resenas"]))
        print(f"\n{len(orden)} candidatos únicos tras los filtros; corroborando los {min(len(orden), args.max_tavily)} mejores con Tavily…")
        entradas = []
        for k, c in enumerate(orden):
            g = c["g"]
            if k < args.max_tavily:
                try:
                    res = tavily_buscar(cl, f"\"{g['nombre']}\" {c['zona']} Bogotá")
                except Exception as e:
                    print(f"   Tavily falló en {g['nombre']}: {e}")
                    res = []
                corro = corroborar(g["nombre"], res)
                time.sleep(0.3)
            else:
                corro = {"dominios": [], "menciones": 0, "cierre": False, "fuentes": [], "extractos": []}
            if (k + 1) % 10 == 0:
                _guardar_cache()
            estado = clasificar(corro, g=g)
            print(f"   {estado[:11]:<11} {g['nombre'][:38]:<38} ★{g['rating']} ({g['resenas']}) {corro['menciones']} sitios")
            entradas.append(a_entrada(g, c["cat"], c["zona"], corro, estado))
            entradas[-1]["_evidencia"]["motivo"] = motivo(corro, estado)

    _guardar_cache()
    os.makedirs("reportes", exist_ok=True)
    base = f"reportes/{args.prefijo}_{datetime.date.today().isoformat()}"
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(entradas, f, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(resumen_md(entradas))
    rec = sum(1 for e in entradas if e["_evidencia"]["estado"] == "recomendado")
    print(f"\nListo: {len(entradas)} candidatos ({rec} recomendados).\nReporte: {base}.md")


if __name__ == "__main__":
    main()
