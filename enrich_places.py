"""Enriquecimiento de la curación con Google Places API (New).

Para cada lugar de app/seed_data.py, busca su ficha oficial en Google y obtiene
datos DUROS verificados: place_id, coordenadas exactas, dirección formateada,
rating y nivel de precios. Esos datos sustituyen a las coordenadas aproximadas
hechas a mano (que causaban pines errados, como el bug El Cielo → BBC).

POR QUÉ ESTE SCRIPT Y NO LLAMAR A GOOGLE EN PRODUCCIÓN:
- Corre UNA sola vez (o cada vez que agregas lugares), en tu máquina local.
- ~300 lugares = ~300 requests = centavos / dentro del free tier.
- Producción sigue 100% offline desde la DB. Costo en runtime: $0.

VENTAJA CLAVE — place_id:
Guardar el place_id permite un link de Maps que va al POI EXACTO sin ninguna
ambigüedad de geocoding:  https://www.google.com/maps/place/?q=place_id:XXXX
Eso elimina para siempre el problema de "El Cielo → Bogotá Beer Company".

USO:
    pip install requests
    export GOOGLE_MAPS_API_KEY=tu_api_key      # Places API (New) habilitada
    python enrich_places.py                     # modo revisión (no escribe)
    python enrich_places.py --write             # parchea app/seed_data.py

El modo por defecto solo MUESTRA lo que encontró y lo guarda en
places_enriched.json (respaldo). Con --write, además inyecta los campos
verificados (place_id, lat, lng, dir, rating, price_level) en seed_data.py.

API key: habilita "Places API (New)" en Google Cloud Console y restringe la
key a esa API. https://console.cloud.google.com/apis/library/places.googleapis.com
"""
import argparse
import json
import os
import re
import sys
import time

import requests

API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Campos que pedimos. Mantener el field mask MÍNIMO para no pagar SKUs caros:
# id + location + formattedAddress + displayName están en el tier barato.
# rating y priceLevel suben de SKU; se incluyen pero puedes quitarlos.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.googleMapsUri",
    "places.primaryType",
])


def buscar(nombre: str, ciudad: str = "Bogotá", direccion: str = "") -> dict | None:
    """Text Search del lugar. Devuelve el primer resultado o None.
    Reintenta automáticamente si Google devuelve 429 (rate limit)."""
    if direccion and direccion.lower() not in ("bogotá, colombia", "bogota, colombia"):
        query = f"{nombre}, {direccion}"
    else:
        query = f"{nombre}, {ciudad}, Colombia"

    for intento in range(4):  # máximo 4 intentos (1 normal + 3 retry)
        resp = requests.post(
            TEXT_SEARCH_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            json={
                "textQuery": query,
                "languageCode": "es",
                "regionCode": "CO",
                "locationBias": {
                    "circle": {
                        "center": {"latitude": 4.6533, "longitude": -74.0836},
                        "radius": 25000.0,
                    }
                },
                "maxResultCount": 1,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            places = resp.json().get("places", [])
            return places[0] if places else None
        if resp.status_code == 429:
            espera = (2 ** intento) * 2  # 2s, 4s, 8s, 16s
            print(f"\n   ⏳ Rate limit (429). Esperando {espera}s antes de reintentar...")
            time.sleep(espera)
            continue
        # otro error
        print(f"   ! HTTP {resp.status_code}: {resp.text[:120]}")
        return None
    print(f"   ! Agotados los reintentos para '{nombre}'")
    return None


def normalizar(place: dict) -> dict:
    loc = place.get("location", {})
    return {
        "place_id": place.get("id", ""),
        "lat": round(loc.get("latitude", 0.0), 6),
        "lng": round(loc.get("longitude", 0.0), 6),
        "dir": place.get("formattedAddress", ""),
        "rating": place.get("rating"),
        "rating_count": place.get("userRatingCount"),
        "price_level": place.get("priceLevel"),
        "google_maps_uri": place.get("googleMapsUri", ""),
        "google_name": (place.get("displayName") or {}).get("text", ""),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="Inyecta los datos verificados en app/seed_data.py")
    ap.add_argument("--city", default="Bogotá")
    ap.add_argument("--desde", default="",
                    help="Reanudar desde este nombre (útil si se cortó por 429). "
                         "Escribe el nombre EXACTO del lugar donde se quedó.")
    ap.add_argument("--sleep", type=float, default=1.2,
                    help="Segundos entre requests (default: 1.2 — conservador para free tier)")
    args = ap.parse_args()

    if not API_KEY:
        print("ERROR: falta GOOGLE_MAPS_API_KEY en el entorno.")
        print("  export GOOGLE_MAPS_API_KEY=tu_key  (Places API New habilitada)")
        sys.exit(1)

    from app import seed_data
    lugares = seed_data.BOGOTA

    # Cargar resultados previos si existen (para no reprocesar lo ya hecho)
    prev = {}
    if os.path.exists("places_enriched.json"):
        try:
            with open("places_enriched.json", encoding="utf-8") as f:
                prev = json.load(f)
            print(f"📂 Cargados {len(prev)} resultados previos de places_enriched.json")
        except Exception:
            pass

    resultados = dict(prev)  # empezar con lo ya hecho
    faltantes = []
    dudosos = []

    # Si --desde, saltar hasta ese nombre
    saltando = bool(args.desde)

    print(f"Enriqueciendo {len(lugares)} lugares con Google Places API (New)")
    print(f"Sleep entre requests: {args.sleep}s\n")
    print(f"{'':3}{'Lugar':<42}{'rating':>7}{'  place_id':<20}")
    print("─" * 78)

    for p in lugares:
        nombre = p["name"]

        # Modo reanudación: saltar hasta el punto de corte
        if saltando:
            if nombre == args.desde:
                saltando = False
            else:
                print(f"↷  {nombre:<42}  (ya procesado)")
                continue

        # Si ya está en resultados previos, saltar
        if nombre in resultados:
            info = resultados[nombre]
            print(f"✓  {nombre:<42}  (previo, place_id={info['place_id'][:16]})")
            continue

        try:
            place = buscar(nombre, args.city, p.get("dir", ""))
        except Exception as e:
            print(f"!  {nombre:<42}  error: {e}")
            faltantes.append(nombre)
            time.sleep(args.sleep)
            continue

        if not place:
            print(f"✗  {nombre:<42}{'?':>7}   <-- NO encontrado")
            faltantes.append(nombre)
            time.sleep(args.sleep)
            continue

        info = normalizar(place)
        resultados[nombre] = info

        g = info["google_name"].lower()
        n = nombre.lower()
        sospechoso = not (any(w in g for w in n.split() if len(w) > 3) or
                          any(w in n for w in g.split() if len(w) > 3))
        flag = "  ⚠ revisar nombre" if sospechoso else ""
        if sospechoso:
            dudosos.append((nombre, info["google_name"]))

        r = info["rating"]
        print(f"OK {nombre:<42}{(str(r) if r else '-'):>7}  {info['place_id'][:18]}{flag}")

        # Guardar progreso parcial en cada request (por si se vuelve a cortar)
        with open("places_enriched.json", "w", encoding="utf-8") as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)

        time.sleep(args.sleep)  # más lento = sin 429

    with open("places_enriched.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, ensure_ascii=False, indent=2)
    nuevos = len(resultados) - len(prev)
    print(f"\nEncontrados esta sesión: {nuevos} · Total acumulado: {len(resultados)}/{len(lugares)}")
    print("Respaldo escrito en places_enriched.json")

    if faltantes:
        print(f"\n✗ NO encontrados ({len(faltantes)}) — revisar nombre/dirección a mano:")
        for n in faltantes:
            print(f"    - {n}")

    if dudosos:
        print(f"\n⚠ Posibles matches equivocados ({len(dudosos)}) — verificar:")
        for nuestro, google in dudosos:
            print(f"    - '{nuestro}'  →  Google dice  '{google}'")

    if args.write:
        escribir_en_seed(resultados)
    else:
        print("\n(modo revisión — no se escribió seed_data.py. Usa --write para aplicar.)")


def escribir_en_seed(resultados: dict):
    """Parchea app/seed_data.py: actualiza lat, lng, dir y agrega place_id,
    rating y price_level a cada lugar encontrado. Hace backup antes."""
    path = os.path.join("app", "seed_data.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()

    with open(path + ".bak", "w", encoding="utf-8") as f:
        f.write(src)
    print(f"\nBackup: {path}.bak")

    cambios = 0
    for nombre, info in resultados.items():
        # Localiza el bloque del lugar por su "name": "..."
        # y reemplaza/inyecta los campos duros dentro de ese dict.
        patron_nombre = re.escape(f'"name": "{nombre}"')
        m = re.search(patron_nombre, src)
        if not m:
            continue
        # Encuentra el cierre del dict (la primera "}," o "}\n    ]" tras el name)
        ini = src.rfind("{", 0, m.start())
        fin = src.find("},", m.start())
        if ini == -1 or fin == -1:
            continue
        bloque = src[ini:fin + 1]

        nuevo = bloque
        nuevo = _set_campo(nuevo, "lat", info["lat"])
        nuevo = _set_campo(nuevo, "lng", info["lng"])
        if info["dir"]:
            nuevo = _set_campo(nuevo, "dir", info["dir"], es_str=True)
        nuevo = _set_campo(nuevo, "place_id", info["place_id"], es_str=True)
        if info.get("rating") is not None:
            nuevo = _set_campo(nuevo, "rating", info["rating"])
        if info.get("price_level"):
            nuevo = _set_campo(nuevo, "price_level", info["price_level"], es_str=True)

        src = src[:ini] + nuevo + src[fin + 1:]
        cambios += 1

    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    print(f"✓ Actualizados {cambios} lugares en {path}")
    print("  Revisa el diff con git antes de commitear.")


def _set_campo(bloque: str, campo: str, valor, es_str: bool = False) -> str:
    """Inserta o reemplaza "campo": valor dentro de un bloque dict (texto)."""
    val_txt = f'"{valor}"' if es_str else f"{valor}"
    patron = re.compile(rf'"{campo}":\s*[^,\n}}]+')
    if patron.search(bloque):
        return patron.sub(f'"{campo}": {val_txt}', bloque, count=1)
    # No existe: lo insertamos tras "name"
    return re.sub(r'("name":\s*"[^"]*")',
                  rf'\1, "{campo}": {val_txt}', bloque, count=1)


if __name__ == "__main__":
    main()
