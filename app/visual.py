"""Tarjetas visuales de lugares curados: foto, categoría, zona, calificación y el dato local en una línea.

Cuando el Companion recomienda o menciona un lugar de NUESTRA curación, la app lo muestra como tarjeta con foto
(las personas entienden mejor lo que ven). Solo lugares curados: ese es el valor de Companion.

Las fotos vienen de Google Places (Place Details → foto → URL pública): son fotos REALES del lugar, nunca
generadas ni de stock. Se cachean 6 h por lugar, así que el costo no crece con los usuarios; si Google falla o
el lugar no tiene foto, la tarjeta sale igual (sin foto) — no se rellena con nada inventado.
"""
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import geo, net, places_live, traducir

log = logging.getLogger("companion.visual")

_cache_foto = net.TTLCache(ttl_s=float(os.environ.get("FOTO_TTL_S", str(6 * 3600))), maxsize=1024)
_SIN = object()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="foto")
_pool_media = ThreadPoolExecutor(max_workers=8, thread_name_prefix="foto-media")
_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
# Categorías con las que una tarjeta con foto tiene sentido (no estaciones, supermercados ni clínicas).
CATEGORIAS_VISUALES = {"restaurante", "bar", "cafe", "atraccion", "parque", "mercado", "experiencia", "excursion",
                       "compras", "museo", "hotel"}


def _media(nombre: str, headers: dict, ancho: int) -> str | None:
    m = net.request("GET", f"https://places.googleapis.com/v1/{nombre}/media", retries=0, timeout=4, headers=headers,
                    params={"maxWidthPx": ancho, "skipHttpRedirect": "true"})
    return m.json().get("photoUri") if m.status_code == 200 else None


def fotos(place_id: str | None, n: int = 1, ancho: int = 640) -> list[dict]:
    """Hasta `n` fotos reales [{'uri','autor'}] del lugar, o []. Nunca lanza."""
    if not place_id or not places_live.API_KEY or not _PLACE_ID.match(place_id):
        return []
    n = max(1, min(n, 6))
    hit = _cache_foto.get(place_id, _SIN)
    if hit is not _SIN and len(hit or []) >= n:
        return [dict(f) for f in hit[:n]]
    if hit is not _SIN and not hit:
        return []
    try:
        h = {"X-Goog-Api-Key": places_live.API_KEY}
        r = net.request("GET", f"https://places.googleapis.com/v1/places/{place_id}", retries=0, timeout=4,
                        headers={**h, "X-Goog-FieldMask": "photos.name,photos.authorAttributions"})
        crudas = (r.json().get("photos") or [])[:n] if r.status_code == 200 else []
        if not crudas:
            _cache_foto.set(place_id, [], ttl_s=1800)
            return []
        futs = [_pool_media.submit(_media, f["name"], h, ancho) for f in crudas]
        out = []
        for f, fut in zip(crudas, futs):
            try:
                uri = fut.result(timeout=5)
            except Exception:
                uri = None
            if uri:
                autor = ((f.get("authorAttributions") or [{}])[0]).get("displayName") or ""
                out.append({"uri": uri, "autor": autor})
        if out:
            _cache_foto.set(place_id, out)
        return [dict(f) for f in out]
    except Exception as e:
        log.warning("foto_fallo error=%s", type(e).__name__)
        return []


def foto(place_id: str | None, ancho: int = 640) -> dict | None:
    """{'uri', 'autor'} de la primera foto del lugar, o None. Nunca lanza."""
    f = fotos(place_id, 1, ancho)
    return f[0] if f else None


_cache_pid = net.TTLCache(ttl_s=24 * 3600, maxsize=1024)


def place_id_de(p: dict) -> str | None:
    """place_id del lugar curado; si no lo tiene, lo busca en Google por nombre + coordenadas y SOLO lo acepta si
    el resultado está a <250 m y su nombre coincide (para no mostrar la foto de otro lugar). None si hay duda."""
    if p.get("place_id"):
        return p["place_id"]
    if not places_live.API_KEY or p.get("lat") is None or p.get("lng") is None or not p.get("name"):
        return None
    clave = f"{p['name']}|{p['lat']}|{p['lng']}"
    hit = _cache_pid.get(clave, _SIN)
    if hit is not _SIN:
        return hit
    try:
        lat, lng = float(p["lat"]), float(p["lng"])
        r = net.request(
            "POST", "https://places.googleapis.com/v1/places:searchText", retries=0, timeout=4,
            headers={"X-Goog-Api-Key": places_live.API_KEY,
                     "X-Goog-FieldMask": "places.id,places.displayName,places.location"},
            json={"textQuery": p["name"], "maxResultCount": 3,
                  "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": 300.0}}})
        base = set(_nombre_base(p["name"]).split())
        elegido = None
        for g in (r.json().get("places") or []) if r.status_code == 200 else []:
            loc = g.get("location") or {}
            gn = set(_nombre_base((g.get("displayName") or {}).get("text", "")).split())
            if (loc and geo.haversine_km(lat, lng, loc["latitude"], loc["longitude"]) <= 0.25
                    and base and len(base & gn) >= max(1, len(base) // 2)):
                elegido = g["id"]
                break
        _cache_pid.set(clave, elegido, ttl_s=24 * 3600 if elegido else 3600)
        return elegido
    except Exception as e:
        log.warning("place_id_fallo error=%s", type(e).__name__)
        return None


def _nombre_base(nombre: str) -> str:
    """«Crepes & Waffles (Parque 93)» → «crepes waffles»; «Museo del Oro — algo» → «museo del oro»."""
    n = re.sub(r"\(.*?\)", " ", nombre or "")
    n = n.split("—")[0].split(" - ")[0]
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", _sin_tildes(n.lower())).split())


def _sin_tildes(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def mencionados(texto: str, lugares: list[dict], maximo: int = 3) -> list[dict]:
    """Lugares curados que el texto nombra, en el orden en que aparecen (máx `maximo`)."""
    n = " " + " ".join(re.sub(r"[^a-z0-9 ]+", " ", _sin_tildes((texto or "").lower())).split()) + " "
    hallados: list[tuple[int, dict]] = []
    vistos: set[str] = set()
    for p in lugares:
        if p.get("category") not in CATEGORIAS_VISUALES:
            continue
        base = _nombre_base(p.get("name", ""))
        if len(base) < 5 or base in vistos:
            continue
        i = n.find(f" {base} ")
        if i >= 0:
            vistos.add(base)
            hallados.append((i, p))
    hallados.sort(key=lambda x: x[0])
    return [p for _, p in hallados[:maximo]]


def _linea(local, clave: str) -> str:
    if isinstance(local, dict):
        return (local.get(clave) or "").strip()
    return ""


def _galeria(p: dict, n: int) -> list[dict]:
    return fotos(place_id_de(p), n)


def tarjetas(lugares: list[dict], idioma: str = "es", con_foto: bool = True, distancias: dict | None = None,
             n_fotos: int = 3) -> list[dict]:
    """Tarjetas listas para pintar. Fotos reales en paralelo; textos del dato local traducidos al idioma del usuario."""
    if not lugares:
        return []
    futs = [_pool.submit(_galeria, p, n_fotos) if con_foto else None for p in lugares]
    out = []
    for p, f in zip(lugares, futs):
        try:
            ph = f.result(timeout=8) if f else []
        except Exception:
            ph = []
        out.append({
            "name": p["name"], "categoria": p.get("category", ""), "zona": p.get("zona", ""),
            "rating": p.get("rating"), "dir": p.get("dir", ""),
            "lat": p.get("lat"), "lng": p.get("lng"), "place_id": p.get("place_id"),
            "clave": _linea(p.get("local"), "clave"), "pedir": _linea(p.get("local"), "pedir"),
            "foto": ph[0] if ph else None, "fotos": ph, "distancia_km": (distancias or {}).get(p["name"]),
        })
    if idioma and idioma != "es":
        textos = [t for c in out for t in (c["clave"], c["pedir"]) if t]
        if textos:
            mapa = dict(zip(textos, traducir.traducir(textos, idioma)))
            for c in out:
                c["clave"], c["pedir"] = mapa.get(c["clave"], c["clave"]), mapa.get(c["pedir"], c["pedir"])
    return out



# ── Portada: fotos reales de lugares emblemáticos de las ciudades activas (pantalla de bienvenida e inicio) ──
_cache_portada = net.TTLCache(ttl_s=6 * 3600, maxsize=4)
_PORTADA_CATS = ("atraccion", "restaurante", "parque", "mercado", "experiencia", "cafe", "museo")


def portada(ciudades: list[str], por_ciudad: int = 9) -> dict:
    """{'fotos': [{uri, autor, lugar, ciudad}], 'ciudades': {ciudad: {uri, autor, lugar}}}. Collage de lugares reales
    y variados (un poco de cada categoría, no solo monumentos). Solo fotos reales de Google; sin fotos, listas vacías
    (la app pinta su fondo de siempre). Cacheado 6 h."""
    from . import db
    clave = "|".join(ciudades) + f"|{por_ciudad}"
    hit = _cache_portada.get(clave, _SIN)
    if hit is not _SIN:
        return hit
    fotos_out, por = [], {}
    try:
        for ciudad in ciudades:
            vistos: set[str] = set()
            por_cat: dict[str, list[dict]] = {}
            for p in sorted(db.places_for_city(ciudad), key=lambda p: -(float(p.get("rating") or 0))):
                if p.get("category") in _PORTADA_CATS and p["name"] not in vistos:
                    vistos.add(p["name"])
                    por_cat.setdefault(p["category"], []).append(p)
            # Una a una por categoría (atracciones primero) hasta juntar candidatos de sobra.
            candidatos: list[dict] = []
            for i in range(3):
                for cat in _PORTADA_CATS:
                    if i < len(por_cat.get(cat, [])):
                        candidatos.append(por_cat[cat][i])
            elegidos = candidatos[:por_ciudad + 4]
            futs = [_pool.submit(_galeria, p, 1) for p in elegidos]
            n = 0
            for p, f in zip(elegidos, futs):
                try:
                    ph = f.result(timeout=10)
                except Exception:
                    ph = []
                if not ph or n >= por_ciudad:
                    continue
                item = {"uri": ph[0]["uri"], "autor": ph[0]["autor"], "lugar": p["name"], "ciudad": ciudad}
                fotos_out.append(item)
                por.setdefault(ciudad, item)
                n += 1
    except Exception:
        log.warning("portada_fallo", exc_info=True)
    out = {"fotos": fotos_out, "ciudades": por}
    _cache_portada.set(clave, out, ttl_s=6 * 3600 if fotos_out else 300)
    return out
