"""
Verifica/corrige las coordenadas de TODOS los lugares de app/seed_data.py
usando Nominatim (OpenStreetMap). Gratis, sin API key.

Corre esto en tu máquina local (NO en Railway), una vez cada vez que
agregues o cambies lugares:

    pip install requests
    python geocode_seed.py

Lee el nombre y la dirección ("dir") de cada lugar directamente desde
app/seed_data.py — así nunca se desincroniza con la curación. Geocodifica,
te muestra una tabla para revisar, guarda seed_coords.json como respaldo y
parchea las coordenadas dentro de app/seed_data.py.

Los lugares que Nominatim no encuentre quedan con su coordenada actual
(ya curada a mano) y se listan al final para revisión manual.
"""
import json, re, time
import requests
from app import seed_data

# (nombre, dirección) leídos directamente de la curación — fuente única de verdad
LUGARES = [(p["name"], p.get("dir", "")) for p in seed_data.BOGOTA if p.get("dir")]


def geocode(query):
    r = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "co"},
        headers={"User-Agent": "VoyraCompanion/1.0 (seed geocoding)"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data:
        return round(float(data[0]["lat"]), 6), round(float(data[0]["lon"]), 6)
    return None, None


def main():
    results, faltantes = {}, []
    print(f"{'':3}{'Lugar':<52}{'lat':>11}{'lng':>12}")
    print("─" * 78)
    for nombre, direccion in LUGARES:
        try:
            lat, lng = geocode(direccion)
        except Exception as e:
            lat, lng = None, None
            print(f"!  {nombre:<52}  error: {e}")
        if lat:
            print(f"OK {nombre:<52}{lat:>11}{lng:>12}")
            results[nombre] = (lat, lng)
        else:
            print(f"✗  {nombre:<52}{'?':>11}{'?':>12}   <-- revisar manual")
            faltantes.append(nombre)
        time.sleep(1.2)  # respeta el rate-limit de Nominatim (1 req/seg)

    print(f"\nGeocodificados: {len(results)}/{len(LUGARES)}")

    # respaldo
    with open("seed_coords.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("Respaldo guardado en seed_coords.json")

    # parcheo robusto: por cada nombre (único), reemplaza la línea lat/lng siguiente
    seed = open("app/seed_data.py", encoding="utf-8").read()
    patched = 0
    for nombre, (lat, lng) in results.items():
        pattern = rf'("name":\s*"{re.escape(nombre)}"[\s\S]*?)"lat":\s*[\d.\-]+,\s*"lng":\s*[\d.\-]+'
        new_seed, n = re.subn(pattern, rf'\1"lat": {lat}, "lng": {lng}', seed, count=1)
        if n:
            seed, patched = new_seed, patched + 1
        else:
            print(f"  ⚠ no encontré el bloque de: {nombre}")
    with open("app/seed_data.py", "w", encoding="utf-8") as f:
        f.write(seed)
    print(f"Parcheados {patched} lugares en app/seed_data.py")

    if faltantes:
        print(f"\nSin geocodificar ({len(faltantes)}) — conservan su coordenada actual:")
        for n in faltantes:
            print(f"  - {n}")

    print("\nListo. Revisa la tabla, y si todo se ve bien:")
    print("  git add app/seed_data.py && git commit -m 'coords verificadas' && git push")


if __name__ == "__main__":
    main()
