"""
Geocodifica los 37 lugares de seed_data.py usando Nominatim (OpenStreetMap).
Corre esto UNA VEZ en tu máquina local:

    pip install requests
    python geocode_seed.py

Genera seed_data.py con coordenadas verificadas para cada lugar.
Sin API key, sin costo.
"""
import requests, time, re

# Direcciones verificadas. Los que fallaron en el primer intento tienen
# queries alternativas más simples que Nominatim resuelve mejor.
LUGARES = [
    ("El Chato",                          "Calle 65 #3-55, Chapinero, Bogotá, Colombia"),
    ("Leo Cocina y Cava",                 "Calle 65 Bis #4-23, Chapinero, Bogotá, Colombia"),
    ("Humo Negro",                        "Carrera 5 #56-06, Chapinero, Bogotá, Colombia"),
    ("Mesa Franca",                       "Calle 69A #5-75, Chapinero, Bogotá, Colombia"),
    ("Salvo Patria",                      "Calle 54 #4-13, Chapinero, Bogotá, Colombia"),
    ("Harry Sasson",                      "Carrera 9 #75-70, Bogotá, Colombia"),
    ("La Puerta Falsa",                   "Calle 11 #6-50, La Candelaria, Bogotá, Colombia"),
    ("Restaurante Santa Fe",              "Calle 11 #5-11, La Candelaria, Bogotá, Colombia"),
    ("Pizzardi Artigianale",              "Carrera 4A #69A-32, Bogotá, Colombia"),
    ("La Cabrera",                        "Carrera 5 #69A-20, Bogotá, Colombia"),
    ("La Lucha Sanguchería",              "Carrera 5 #69B-31, Bogotá, Colombia"),
    ("Abasto",                            "Calle 119A #6-30, Usaquén, Bogotá, Colombia"),
    # Fallidos — queries simplificadas:
    ("Abasto Quinta Camacho",             "Carrera 10 70-48, Bogotá, Colombia"),
    ("El Don (Chapinero)",                "Calle 63 #9-60, Chapinero, Bogotá, Colombia"),
    ("Tropicalia Coffee",                 "Calle 81A 8-23, Bogotá, Colombia"),
    ("Amor Perfecto",                     "Calle 119 #6-22, Usaquén, Bogotá, Colombia"),
    ("Colo Coffee",                       "Carrera 13 83-19, Bogotá, Colombia"),
    ("Brot Bakery & Café",               "Calle 85 12-29, Bogotá, Colombia"),
    ("Masa (Rosales)",                    "Calle 70A 2-54, Bogotá, Colombia"),
    ("Árbol del Pan",                     "Calle 69 #4-32, Chapinero, Bogotá, Colombia"),
    ("KOSH",                              "Calle 82 11-40, Bogotá, Colombia"),
    ("Clandestino (Gallery Club)",        "Calle 85 12-09, Bogotá, Colombia"),
    ("Taninos Park Wines",                "Avenida 24 #38-35, Bogotá, Colombia"),
    ("Zona T / Zona Rosa (vida nocturna)","Calle 83 13-00, Bogotá, Colombia"),
    ("Museo del Oro",                     "Calle 16 #5-41, La Candelaria, Bogotá, Colombia"),
    ("Museo Botero",                      "Calle 11 #4-41, La Candelaria, Bogotá, Colombia"),
    ("Plaza de Bolívar",                  "Plaza de Bolívar, La Candelaria, Bogotá, Colombia"),
    ("Chorro de Quevedo",                 "Calle 12B #2-83, La Candelaria, Bogotá, Colombia"),
    ("Caminata por La Candelaria (grafiti y arquitectura colonial)",
                                          "Calle 10 #3-16, La Candelaria, Bogotá, Colombia"),
    ("Cerro de Monserrate",               "Cerro de Monserrate, Bogotá, Colombia"),
    ("Mirador Torre Colpatria",           "Carrera 7 24-89, Bogotá, Colombia"),
    ("Jardín Botánico José Celestino Mutis", "Avenida Calle 63 68-95, Bogotá, Colombia"),
    ("Parque Metropolitano Simón Bolívar","Parque Simon Bolivar, Bogotá, Colombia"),
    ("Parque de la 93",                   "Calle 93A 11A-31, Bogotá, Colombia"),
    ("Mercado de pulgas de Usaquén",      "Calle 119A #6-30, Usaquén, Bogotá, Colombia"),
    ("Plaza de Mercado de Paloquemao",    "Carrera 25 #18-65, Bogotá, Colombia"),
    ("Catedral de Sal de Zipaquirá",      "Catedral de Sal, Zipaquirá, Colombia"),
]

def geocode(query):
    url = "https://nominatim.openstreetmap.org/search"
    r = requests.get(url,
        params={"q": query, "format": "json", "limit": 1, "countrycodes": "co"},
        headers={"User-Agent": "VoyraCompanion/1.0 geocoding seed data"},
        timeout=10)
    r.raise_for_status()
    data = r.json()
    if data:
        return round(float(data[0]["lat"]), 6), round(float(data[0]["lon"]), 6)
    return None, None

results = {}
no_encontrados = []

print(f"{'':2} {'Lugar':<50} {'lat':>10} {'lng':>11}")
print("─" * 80)

for nombre, direccion in LUGARES:
    lat, lng = geocode(direccion)
    if lat:
        print(f"OK {nombre:<50} {lat:>10} {lng:>11}")
        results[nombre] = (lat, lng)
    else:
        print(f"✗  {nombre:<50} {'?':>10} {'?':>11}  <-- REVISAR")
        no_encontrados.append(nombre)
    time.sleep(1.2)

print(f"\nGeocod: {len(results)}/{len(LUGARES)}")

# Parchear seed_data.py — encoding utf-8 explícito (fix para Windows)
seed = open("app/seed_data.py", encoding="utf-8").read()
patched = 0
for nombre, (lat, lng) in results.items():
    pattern = rf'("name": "{re.escape(nombre)}"[^\n]*\n\s*)"lat": [\d.]+, "lng": -[\d.]+'
    replacement = rf'\1"lat": {lat}, "lng": {lng}'
    new_seed, n = re.subn(pattern, replacement, seed)
    if n:
        seed = new_seed
        patched += 1
    else:
        print(f"  ⚠ Bloque no encontrado en seed_data.py: {nombre}")

# Escribir con utf-8 explícito
with open("app/seed_data.py", "w", encoding="utf-8") as f:
    f.write(seed)

print(f"Parcheados: {patched} lugares en app/seed_data.py")

if no_encontrados:
    print(f"\nNo geocodificados ({len(no_encontrados)}) — coordenadas sin cambio:")
    for n in no_encontrados:
        print(f"  - {n}")

print("\nListo. Revisa los NO geocodificados manualmente si los hay.")
print("Luego: git add app/seed_data.py && git push")
