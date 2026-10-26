"""Verificación periódica de la curación contra Google Places (API New).

Paso 1 del plan de curaduría: antes de AMPLIAR la lista hay que asegurarse de que lo que
ya recomendamos sigue existiendo y sigue siendo bueno. Para cada lugar de app/seed_data.py
consulta su ficha en Google y detecta:

  · CERRADO_PERMANENTE / CERRADO_TEMPORAL — el Companion no debe recomendarlo.
  · NO_ENCONTRADO       — Google ya no lo conoce (cerró o cambió de nombre).
  · NOMBRE_DISTINTO     — el place_id apunta a OTRO lugar (riesgo de pin equivocado).
  · COORDENADAS         — las coordenadas curadas están lejos de las de Google (> 300 m).
  · RATING_BAJO         — el rating cayó respecto a la última vez, o está por debajo de 4.0.
  · SIN_HORARIO         — Google no publica horario (no podemos decir "abre hoy").

NO modifica nada: genera un reporte para que una persona decida (regla de confianza de
Companion: nada cambia en la curación sin revisión humana).

    pip install httpx                           # ya está en requirements.txt
    # La key va en un archivo .env (junto a este script) con la línea:  GOOGLE_MAPS_API_KEY=tu_key
    # (Places API (New) habilitada). También funciona como variable de entorno.
    python verificar_lugares.py                 # Bogotá, todos los lugares
    python verificar_lugares.py --limit 5       # prueba rápida
    python verificar_lugares.py --solo-problemas

Salida: reportes/verificacion_<ciudad>_<fecha>.json (todo, incluidos los horarios) y .md
(resumen legible, lo grave primero). Costo: una consulta de detalle por lugar (~180 para
Bogotá), centavos o dentro del cupo gratuito de Google.
"""
import argparse
import datetime
import difflib
import json
import os
import re
import sys
import time
import unicodedata

import httpx

from app import geo



def cargar_env(path: str = ".env") -> None:
    """Lee KEY=valor de un .env local (git lo ignora) sin pisar variables ya definidas.
    Así la key de Google no tiene que pegarse en la consola ni en un chat."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


cargar_env()
API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
DETAILS_URL = "https://places.googleapis.com/v1/places/{pid}"
SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Mantener el field mask mínimo: cada campo extra puede subir de SKU.
DETAILS_MASK = ",".join([
    "id", "displayName", "formattedAddress", "location", "businessStatus", "rating",
    "userRatingCount", "googleMapsUri", "regularOpeningHours.weekdayDescriptions",
])
SEARCH_MASK = ",".join("places." + f for f in [
    "id", "displayName", "formattedAddress", "location", "businessStatus", "rating",
    "userRatingCount", "googleMapsUri", "regularOpeningHours.weekdayDescriptions",
])

CIUDADES = {
    "Bogotá": {"lista": "BOGOTA", "json": "places_enriched.json", "lat": 4.6533, "lng": -74.0836,
               "radius": 25000.0, "region": "CO", "pais": "Colombia"},
    "Guadalajara": {"lista": "GUADALAJARA", "json": "places_enriched_guadalajara.json", "lat": 20.6736,
                    "lng": -103.3440, "radius": 30000.0, "region": "MX", "pais": "México"},
}

# Umbrales (ajustables)
DIST_MAX_M = 300          # más lejos que esto → COORDENADAS
RATING_CAIDA = 0.4        # caída respecto al rating previo → RATING_BAJO
RATING_MIN = 4.0          # por debajo → RATING_BAJO (para un lugar "curado")
SIMILITUD_MIN = 0.45      # nombre curado vs nombre de Google

GRAVEDAD = {"alto": 0, "medio": 1, "bajo": 2}


# ─────────────────────────── lógica pura (con tests) ───────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def _nombres_parecidos(a: str, b: str) -> bool:
    """Parecidos si comparten una palabra significativa o la similitud de texto es suficiente."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True  # sin datos no acusamos
    ta = {w for w in na.split() if len(w) > 3}
    tb = {w for w in nb.split() if len(w) > 3}
    if ta & tb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= SIMILITUD_MIN


def evaluar(seed: dict, g: dict | None, previo: dict | None = None) -> list[dict]:
    """Compara el lugar curado (`seed`) con la ficha de Google (`g`, ya normalizada) y devuelve
    una lista de hallazgos [{nivel, codigo, detalle}]. Lista vacía = sin problemas."""
    previo = previo or {}
    if g is None:
        return [{"nivel": "alto", "codigo": "NO_ENCONTRADO",
                 "detalle": "Google no devolvió ficha: puede haber cerrado o cambiado de nombre."}]
    out: list[dict] = []
    estado = g.get("estado") or ""
    if estado == "CLOSED_PERMANENTLY":
        out.append({"nivel": "alto", "codigo": "CERRADO_PERMANENTE",
                    "detalle": "Google lo marca como cerrado permanentemente: sacarlo de la curación."})
    elif estado == "CLOSED_TEMPORARILY":
        out.append({"nivel": "medio", "codigo": "CERRADO_TEMPORAL",
                    "detalle": "Google lo marca como cerrado temporalmente: no recomendarlo por ahora."})

    if not _nombres_parecidos(seed.get("name", ""), g.get("nombre", "")):
        out.append({"nivel": "medio", "codigo": "NOMBRE_DISTINTO",
                    "detalle": f"El nombre en Google es «{g.get('nombre')}»: posible lugar equivocado."})

    if g.get("lat") is not None and seed.get("lat") is not None:
        d_m = geo.haversine_km(seed["lat"], seed["lng"], g["lat"], g["lng"]) * 1000
        if d_m > DIST_MAX_M:
            out.append({"nivel": "medio", "codigo": "COORDENADAS",
                        "detalle": f"Las coordenadas curadas están a {round(d_m)} m de las de Google."})

    r, r0 = g.get("rating"), previo.get("rating")
    if r is not None and r0 is not None and r0 - r >= RATING_CAIDA:
        out.append({"nivel": "medio", "codigo": "RATING_BAJO",
                    "detalle": f"El rating cayó de {r0} a {r}."})
    elif r is not None and r < RATING_MIN:
        out.append({"nivel": "bajo", "codigo": "RATING_BAJO",
                    "detalle": f"Rating {r} (por debajo de {RATING_MIN}) para un lugar curado."})

    if not g.get("horarios"):
        out.append({"nivel": "bajo", "codigo": "SIN_HORARIO",
                    "detalle": "Google no publica horario: no se puede afirmar «abre hoy»."})
    return sorted(out, key=lambda h: GRAVEDAD[h["nivel"]])


def normalizar(place: dict) -> dict:
    loc = place.get("location") or {}
    return {
        "place_id": place.get("id", ""),
        "nombre": (place.get("displayName") or {}).get("text", ""),
        "direccion": place.get("formattedAddress", ""),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "estado": place.get("businessStatus", ""),
        "rating": place.get("rating"),
        "rating_count": place.get("userRatingCount"),
        "horarios": (place.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [],
        "google_maps_uri": place.get("googleMapsUri", ""),
    }


def resumen_md(ciudad: str, resultados: list[dict], solo_problemas: bool = False) -> str:
    """Reporte legible: primero lo grave. `resultados` = [{name, zona, hallazgos, ...}]."""
    con = [r for r in resultados if r["hallazgos"]]
    sin = [r for r in resultados if not r["hallazgos"]]
    peor = lambda r: min(GRAVEDAD[h["nivel"]] for h in r["hallazgos"])  # noqa: E731
    con.sort(key=lambda r: (peor(r), r["name"]))
    cuenta = {n: sum(1 for r in con for h in r["hallazgos"] if h["nivel"] == n) for n in GRAVEDAD}
    L = [f"# Verificación de la curación — {ciudad}",
         f"_{datetime.date.today().isoformat()} · {len(resultados)} lugares · "
         f"{len(con)} con hallazgos ({cuenta['alto']} altos, {cuenta['medio']} medios, {cuenta['bajo']} bajos)_", ""]
    marca = {"alto": "🔴", "medio": "🟠", "bajo": "🟡"}
    for r in con:
        L.append(f"## {r['name']} — {r.get('zona', '')}")
        for h in r["hallazgos"]:
            L.append(f"- {marca[h['nivel']]} **{h['codigo']}**: {h['detalle']}")
        if r.get("google_maps_uri"):
            L.append(f"- [Ver en Google Maps]({r['google_maps_uri']})")
        L.append("")
    if not solo_problemas and sin:
        L.append(f"## Sin hallazgos ({len(sin)})")
        L.append(", ".join(r["name"] for r in sin))
    return "\n".join(L) + "\n"


# ─────────────────────────── acceso a Google ───────────────────────────
def _con_reintentos(fn):
    for intento in range(4):
        resp = fn()
        if resp.status_code != 429:
            return resp
        espera = (2 ** intento) * 2
        print(f"   rate limit (429), esperando {espera}s…")
        time.sleep(espera)
    return resp


def detalles(client: httpx.Client, place_id: str) -> dict | None:
    resp = _con_reintentos(lambda: client.get(
        DETAILS_URL.format(pid=place_id),
        headers={"X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": DETAILS_MASK, "Accept-Language": "es"}))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return normalizar(resp.json())


def buscar(client: httpx.Client, nombre: str, direccion: str, cfg: dict, ciudad: str) -> dict | None:
    consulta = f"{nombre}, {direccion}" if direccion and ciudad.lower() in direccion.lower() \
        else f"{nombre}, {direccion or ciudad}, {cfg['pais']}"
    resp = _con_reintentos(lambda: client.post(
        SEARCH_URL,
        headers={"X-Goog-Api-Key": API_KEY, "X-Goog-FieldMask": SEARCH_MASK},
        json={"textQuery": consulta, "languageCode": "es", "regionCode": cfg["region"], "maxResultCount": 1,
              "locationBias": {"circle": {"center": {"latitude": cfg["lat"], "longitude": cfg["lng"]},
                                          "radius": cfg["radius"]}}}))
    resp.raise_for_status()
    places = resp.json().get("places", [])
    return normalizar(places[0]) if places else None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # consolas de Windows
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Verifica la curación contra Google Places (solo lectura).")
    ap.add_argument("--city", default="Bogotá", choices=list(CIUDADES))
    ap.add_argument("--limit", type=int, default=0, help="Procesar solo los primeros N (prueba rápida)")
    ap.add_argument("--sleep", type=float, default=0.4, help="Segundos entre consultas")
    ap.add_argument("--solo-problemas", action="store_true", help="El .md omite los lugares sin hallazgos")
    args = ap.parse_args()

    if not API_KEY:
        print("ERROR: falta GOOGLE_MAPS_API_KEY en el entorno (Places API New habilitada).")
        sys.exit(1)

    from app import seed_data
    cfg = CIUDADES[args.city]
    lugares = getattr(seed_data, cfg["lista"])
    if args.limit:
        lugares = lugares[: args.limit]

    previos: dict = {}
    if os.path.exists(cfg["json"]):
        with open(cfg["json"], encoding="utf-8") as f:
            previos = json.load(f)

    resultados = []
    with httpx.Client(timeout=20) as client:
        for i, p in enumerate(lugares, 1):
            nombre = p["name"]
            previo = previos.get(nombre) or {}
            pid = p.get("place_id") or previo.get("place_id")
            try:
                g = detalles(client, pid) if pid else buscar(client, nombre, p.get("dir", ""), cfg, args.city)
            except Exception as e:  # un fallo de red no debe tirar toda la corrida
                print(f"[{i}/{len(lugares)}] {nombre}: error {e}")
                time.sleep(args.sleep)
                continue
            hallazgos = evaluar(p, g, previo)
            if not pid and g:
                hallazgos.append({"nivel": "bajo", "codigo": "SIN_PLACE_ID",
                                  "detalle": "No tenía place_id; se buscó por texto. Conviene correr enrich_places.py."})
            marca = "OK " if not hallazgos else ("!! " if hallazgos[0]["nivel"] == "alto" else "~  ")
            print(f"[{i}/{len(lugares)}] {marca}{nombre}" + ("" if not hallazgos else f"  → {hallazgos[0]['codigo']}"))
            resultados.append({
                "name": nombre, "zona": p.get("zona", ""), "place_id": (g or {}).get("place_id") or pid,
                "estado": (g or {}).get("estado"), "rating": (g or {}).get("rating"),
                "rating_count": (g or {}).get("rating_count"), "horarios": (g or {}).get("horarios", []),
                "google_maps_uri": (g or {}).get("google_maps_uri", ""), "hallazgos": hallazgos,
                "google": {k: (g or {}).get(k) for k in ("nombre", "lat", "lng", "direccion")},
            })
            time.sleep(args.sleep)

    os.makedirs("reportes", exist_ok=True)
    base = f"reportes/verificacion_{args.city.lower().replace('á', 'a')}_{datetime.date.today().isoformat()}"
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(resumen_md(args.city, resultados, args.solo_problemas))
    graves = sum(1 for r in resultados if r["hallazgos"] and r["hallazgos"][0]["nivel"] == "alto")
    print(f"\nListo: {len(resultados)} lugares, {graves} con hallazgos graves.\nReporte: {base}.md")


if __name__ == "__main__":
    main()
