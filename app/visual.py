"""Tarjetas visuales de lugares curados: foto, categoría, zona, calificación y el dato local en una línea.

Cuando el Companion recomienda o menciona un lugar de NUESTRA curación, la app lo muestra como tarjeta con foto
(las personas entienden mejor lo que ven). Solo lugares curados: ese es el valor de Voyra.

Las fotos vienen de Google Places (Place Details → foto → URL pública). Se cachean 6 h por lugar, así que el costo
no crece con los usuarios; si Google falla o el lugar no tiene foto, la tarjeta sale igual (sin foto).
"""
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from . import net, places_live, traducir

log = logging.getLogger("voyra.visual")

_cache_foto = net.TTLCache(ttl_s=float(os.environ.get("FOTO_TTL_S", str(6 * 3600))), maxsize=1024)
_SIN = object()
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="foto")
_PLACE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
# Categorías con las que una tarjeta con foto tiene sentido (no estaciones, supermercados ni clínicas).
CATEGORIAS_VISUALES = {"restaurante", "bar", "cafe", "atraccion", "parque", "mercado", "experiencia", "excursion",
                       "compras", "museo", "hotel"}


def foto(place_id: str | None, ancho: int = 640) -> dict | None:
    """{'uri', 'autor'} de la primera foto del lugar, o None. Nunca lanza."""
    if not place_id or not places_live.API_KEY or not _PLACE_ID.match(place_id):
        return None
    hit = _cache_foto.get(place_id, _SIN)
    if hit is not _SIN:
        return dict(hit) if hit else None
    try:
        h = {"X-Goog-Api-Key": places_live.API_KEY}
        r = net.request("GET", f"https://places.googleapis.com/v1/places/{place_id}", retries=0, timeout=4,
                        headers={**h, "X-Goog-FieldMask": "photos.name,photos.authorAttributions"})
        fotos = (r.json().get("photos") or []) if r.status_code == 200 else []
        if not fotos:
            _cache_foto.set(place_id, None, ttl_s=1800)
            return None
        nombre = fotos[0]["name"]
        autor = ((fotos[0].get("authorAttributions") or [{}])[0]).get("displayName") or ""
        m = net.request("GET", f"https://places.googleapis.com/v1/{nombre}/media", retries=0, timeout=4, headers=h,
                        params={"maxWidthPx": ancho, "skipHttpRedirect": "true"})
        uri = m.json().get("photoUri") if m.status_code == 200 else None
        if not uri:
            return None
        out = {"uri": uri, "autor": autor}
        _cache_foto.set(place_id, out)
        return dict(out)
    except Exception as e:
        log.warning("foto_fallo error=%s", type(e).__name__)
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


def tarjetas(lugares: list[dict], idioma: str = "es", con_foto: bool = True, distancias: dict | None = None) -> list[dict]:
    """Tarjetas listas para pintar. Fotos en paralelo; textos del dato local traducidos al idioma del usuario."""
    if not lugares:
        return []
    futs = [_pool.submit(foto, p.get("place_id")) if con_foto else None for p in lugares]
    out = []
    for p, f in zip(lugares, futs):
        try:
            ph = f.result(timeout=6) if f else None
        except Exception:
            ph = None
        out.append({
            "name": p["name"], "categoria": p.get("category", ""), "zona": p.get("zona", ""),
            "rating": p.get("rating"), "dir": p.get("dir", ""),
            "lat": p.get("lat"), "lng": p.get("lng"), "place_id": p.get("place_id"),
            "clave": _linea(p.get("local"), "clave"), "pedir": _linea(p.get("local"), "pedir"),
            "foto": ph, "distancia_km": (distancias or {}).get(p["name"]),
        })
    if idioma and idioma != "es":
        textos = [t for c in out for t in (c["clave"], c["pedir"]) if t]
        if textos:
            mapa = dict(zip(textos, traducir.traducir(textos, idioma)))
            for c in out:
                c["clave"], c["pedir"] = mapa.get(c["clave"], c["clave"]), mapa.get(c["pedir"], c["pedir"])
    return out
