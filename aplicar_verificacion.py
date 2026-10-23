"""Convierte el último reporte de verificar_lugares.py en app/seed_verificacion.json.

Ese archivo es una CAPA sobre app/seed_data.py (que no se toca): seed_data.all_seeds() la aplica
al cargar. Así los cambios son auditables (un solo JSON legible), reversibles (borra el archivo) y
sobreviven a una nueva corrida de enrich_places.py.

La capa tiene:
  · excluir   — lugares que cerraron o que no son de la ciudad (con el motivo y la fuente).
  · ajustes   — lat/lng/place_id/rating verificados contra Google, SOLO cuando el match es fiable:
                nombre parecido, operativo, no es cadena de varias sedes y a menos de 3 km del
                punto curado (más lejos suele ser OTRA sede u otro lugar → se deja para revisión).
  · confianza_baja — cerrados temporalmente: se quedan pero con menos peso.
  · revisar   — lo que NO se tocó y necesita ojos humanos.

    python verificar_lugares.py          # genera el reporte
    python aplicar_verificacion.py       # genera la capa
"""
import datetime
import glob
import json
import sys

from app import geo
from verificar_lugares import CIUDADES, SIMILITUD_MIN, buscar, cargar_env, evaluar, _nombres_parecidos  # noqa: F401
import httpx
import os

cargar_env()

# Decisiones editoriales confirmadas con prensa / Google (oct. 2026). Revisarlas al volver a correr.
EXCLUIR_MANUAL = {
    "Criterión": "Cerró en marzo de 2024 (anuncio de Jorge Rausch; planean reabrir con otro concepto). El Tiempo / El País.",
    "Baum": "Cerró definitivamente (enero de 2020, presión inmobiliaria). La FM / Resident Advisor.",
    "Villanos en Bermudas": "Cerró durante la pandemia. El Tiempo.",
    "Oci.Mde": "Es un restaurante de Medellín (Provenza), no de Bogotá; Google no lo encuentra en la ciudad.",
    "Crepes & Waffles (Zona T / Calle 85)": "Google lo marca cerrado permanentemente.",
    "Dalia Plant Power": "Google lo marca cerrado permanentemente.",
    "Mesa Salvaje": "Google lo marca cerrado permanentemente.",
    "Pan Fino": "Google lo marca cerrado permanentemente.",
    "Club Colombia": "Cerró definitivamente en Bogotá (La República: «los restaurantes Harry's Bar y Club Colombia cerrarán definitivamente»).",
    "Gordo": "Google no lo ubica en Carrera 4A #66-84; la sede operativa de Gordo Brooklyn está en Cra. 72 #145-60 (otra zona).",
    "Misia": "Google no lo lista; la antigua sede de Tv. 6 #27-50 aparece hoy como BACU y la de Carrera 7 #67-39 es un Holiday Inn Express. Un blog lo da por cerrado.",
    "Devoción": "Google no lo ubica en Carrera 6 #66-25 (solo hay un pin de dirección); no hay evidencia de una tienda vigente ahí.",
    "Donostiarra": "No hay rastro de un restaurante así en Bogotá (ni en Google ni en prensa) en Carrera 5 #67-23.",
}
# Lugares reales que Google no halló con la búsqueda original: se re-buscan con una consulta mejor.
BUSQUEDA_MEJORADA = {
    "Astoria Rooftop": "Astoria Rooftop Calle 85 #12-66 Bogotá",
    "Smoking Molly": "Smoking Molly Blues Jazz Restobar Bogotá",
}
# Correcciones revisadas una por una (oct. 2026): dirección/zona reales y place_id de la ficha CORRECTA en Google.
AJUSTES_MANUALES = {
    "Leo Cocina y Cava": {
        "lat": 4.648032,
        "lng": -74.056437,
        "place_id": "ChIJf0x6pJuZP44RbF-3kBjiZLs",
        "rating": 4.4,
        "dir": "Calle 65 Bis #4-23, Chapinero Alto, Bogotá, Colombia",
        "zona": "Chapinero Alto"
    },
    "Oda": {
        "lat": 4.718735,
        "lng": -74.03606,
        "place_id": "ChIJwX6B3iqbP44RfYU44hHXmEk",
        "rating": 4.6,
        "dir": "Calle 140 #11-45, Torre HHC, Usaquén, Bogotá, Colombia",
        "zona": "Cedritos / Norte",
        "descripcion": "#76 en la lista extendida de Latin America's 50 Best 2025 y ganador del Sustainable Restaurant Award. Dentro del G Lounge (Cedritos, al norte de la ciudad): menú contemporáneo de ingredientes autóctonos con foco en sostenibilidad, de la chef Natalia Cocomá."
    },
    "Catación Pública": {
        "lat": 4.696023,
        "lng": -74.028417,
        "place_id": "ChIJAUS02weQP44RlItRLPHS8So",
        "rating": 4.8,
        "dir": "Calle 120A #3A-57, Usaquén, Bogotá, Colombia",
        "zona": "Usaquén"
    },
    "Amor Perfecto": {
        "lat": 4.648912,
        "lng": -74.055376,
        "place_id": "ChIJwXLng0CaP44RdFS3YFjewS0",
        "rating": 4.6,
        "dir": "Carrera 4 #66-46, Chapinero Alto, Bogotá, Colombia",
        "zona": "Chapinero Alto",
        "descripcion": "Pionera colombiana del café de especialidad — acercó los cafés premium de origen único al consumidor local. Su sede de Chapinero Alto tiene laboratorio de café y hace catas públicas y cursos de barismo los fines de semana."
    },
    "Árbol del Pan": {
        "lat": 4.648916,
        "lng": -74.055953,
        "place_id": "ChIJKYDV-UCaP44R8N8ovfrF4Gk",
        "rating": 4.7,
        "dir": "Calle 66 Bis #4-34, Chapinero Alto, Bogotá, Colombia",
        "zona": "Chapinero Alto",
        "descripcion": "Panadería artesanal encantadora y destino de brunch muy querido en Chapinero — de las panaderías mejor valoradas de la ciudad. Calle 66 Bis #4-34."
    },
    "Varietale": {
        "lat": 4.628765,
        "lng": -74.066364,
        "place_id": "ChIJRyluYSiaP44RaoAGVqb67FI",
        "rating": 4.7,
        "dir": "Calle 41 #8-43, Bogotá, Colombia",
        "zona": "Teusaquillo / La Javeriana"
    },
    "Arte y Pasión Café": {
        "lat": 4.601618,
        "lng": -74.072283,
        "place_id": "ChIJqTYXeACZP44R1QhrzXqfB4g",
        "dir": "Carrera 6 #15-32, La Candelaria, Bogotá, Colombia",
        "zona": "La Candelaria / Centro"
    },
    "Abasto": {
        "lat": 4.653186,
        "lng": -74.059011,
        "place_id": "ChIJhW3-hEOaP44RDcZUkVBVfpE",
        "rating": 4.6,
        "dir": "Calle 69A #9-09, Quinta Camacho, Bogotá, Colombia",
        "zona": "Quinta Camacho"
    },
    "Tábula": {
        "lat": 4.615786,
        "lng": -74.067464,
        "place_id": "ChIJrzt515qZP44RzbxTfqgiiuE",
        "rating": 4.6,
        "dir": "Calle 29 Bis #5-90, La Macarena, Bogotá, Colombia",
        "zona": "La Macarena"
    },
    "Casa de Nariño": {
        "lat": 4.595574,
        "lng": -74.077447,
        "place_id": "ChIJp_l-ogeZP44R5lkZJLO07t8",
        "rating": 4.5
    },
    "Mirador de La Calera": {
        "lat": 4.675752,
        "lng": -74.023709,
        "place_id": "ChIJU-WVq3CQP44RjX-H7NyVlbs",
        "rating": 4.6
    },
    "Fundación Cardioinfantil": {
        "lat": 4.741228,
        "lng": -74.034068,
        "place_id": "ChIJWwCslH6FP44RUIg2mrwjl7k",
        "rating": 3.9
    },
    "El Corral (Zona T)": {
        "lat": 4.667006,
        "lng": -74.052815,
        "place_id": "ChIJnWRNsIqaP44R8hyP3j2TmJQ",
        "rating": 4.3
    },
    "Iglesia de San Francisco": {
        "lat": 4.601898,
        "lng": -74.073333,
        "place_id": "ChIJrWmTMqGZP44RymsrzAQrfnc",
        "rating": 4.6
    },
    "Parque Nacional Enrique Olaya Herrera": {
        "lat": 4.623875,
        "lng": -74.063069,
        "place_id": "ChIJT3j00IeZP44RpQpxWMx8yx0",
        "rating": 4.5
    },
    "Chorro de Quevedo": {
        "lat": 4.597196,
        "lng": -74.06977,
        "place_id": "ChIJt3QY8qWZP44RArvZi8MtD24",
        "rating": 4.6
    }
}

SIN_AJUSTE_CATEGORIAS = {"supermercado", "farmacia", "transporte"}  # cadenas: muchas sedes con el mismo nombre
DIST_MAX_M = 3000


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ruta = sorted(glob.glob("reportes/verificacion_bogota_*.json"))[-1]
    resultados = json.load(open(ruta, encoding="utf-8"))
    from app import seed_data
    por_nombre = {p["name"]: p for p in seed_data.BOGOTA}

    excluir = dict(EXCLUIR_MANUAL)
    ajustes, baja, revisar = {}, [], {}

    # Re-búsquedas puntuales
    extra = {}
    if BUSQUEDA_MEJORADA:
        with httpx.Client(timeout=20) as cl:
            for nombre, consulta in BUSQUEDA_MEJORADA.items():
                g = buscar(cl, consulta, "", CIUDADES["Bogotá"], "Bogotá")
                if g and g.get("estado") == "OPERATIONAL":
                    extra[nombre] = g
                    print(f"re-búsqueda OK: {nombre} → {g['nombre']} ({g['lat']:.5f},{g['lng']:.5f})")
                else:
                    revisar[nombre] = "Sigue sin aparecer en Google; verificar a mano."

    for x in resultados:
        n = x["name"]
        if n in excluir:
            continue
        codigos = {h["codigo"] for h in x["hallazgos"]}
        g = x.get("google") or {}
        seed = por_nombre.get(n, {})
        if n in extra:
            e = extra[n]
            ajustes[n] = {"lat": round(e["lat"], 6), "lng": round(e["lng"], 6), "place_id": e["place_id"],
                          **({"rating": e["rating"]} if e.get("rating") else {})}
            continue
        if "CERRADO_TEMPORAL" in codigos:
            baja.append(n)
        if codigos & {"CERRADO_PERMANENTE", "NO_ENCONTRADO", "NOMBRE_DISTINTO"}:
            revisar[n] = "; ".join(h["detalle"] for h in x["hallazgos"] if h["nivel"] != "bajo")
            continue
        if seed.get("category") in SIN_AJUSTE_CATEGORIAS or "sedes" in (seed.get("zona") or "").lower():
            continue
        if g.get("lat") is None or x.get("estado") != "OPERATIONAL" or not x.get("place_id"):
            continue
        d_m = geo.haversine_km(seed["lat"] if not isinstance(seed["lat"], str) else float(seed["lat"]),
                               seed["lng"] if not isinstance(seed["lng"], str) else float(seed["lng"]),
                               g["lat"], g["lng"]) * 1000
        if d_m > DIST_MAX_M:
            revisar[n] = f"Google lo ubica a {round(d_m / 1000, 1)} km del punto curado: ¿otra sede u otro lugar?"
            continue
        ajustes[n] = {"lat": round(g["lat"], 6), "lng": round(g["lng"], 6), "place_id": x["place_id"],
                      **({"rating": x["rating"]} if x.get("rating") else {})}

    # Los textos «local» de lugares que cambian de barrio se reescriben para no mentir sobre la zona.
    from app.local_bogota import LOCAL_BOGOTA
    clave_nueva = {
        "Oda": "Cocina sostenible premiada dentro del G Lounge, en Cedritos (norte de Bogotá).",
        "Amor Perfecto": "Pionera del café de especialidad en Colombia — su sede de Chapinero Alto tiene laboratorio y catas públicas.",
    }
    for n, a in AJUSTES_MANUALES.items():
        ajustes[n] = dict(a)
        if n in clave_nueva and n in LOCAL_BOGOTA:
            ajustes[n]["local"] = {**LOCAL_BOGOTA[n], "clave": clave_nueva[n]}
        revisar.pop(n, None)
    for n in excluir:
        revisar.pop(n, None)
        ajustes.pop(n, None)
    # «Crepes & Waffles» (varias sedes) se conserva —el detector de platos reconoce el lugar por su nombre—,
    # pero su pin apuntaba a la sede de la Calle 85, cerrada: se mueve a la sede abierta de Parque 93.
    ref = ajustes.get("Crepes & Waffles (Parque 93)")
    if ref:
        ajustes["Crepes & Waffles"] = {"lat": ref["lat"], "lng": ref["lng"]}
        revisar.pop("Crepes & Waffles", None)

    capa = {"generado": datetime.date.today().isoformat(), "fuente": ruta,
            "excluir": excluir, "confianza_baja": sorted(set(baja)), "ajustes": ajustes, "revisar": revisar}
    with open("app/seed_verificacion.json", "w", encoding="utf-8") as f:
        json.dump(capa, f, ensure_ascii=False, indent=1)
    print(f"\nexcluir: {len(excluir)} · ajustes: {len(ajustes)} · confianza baja: {len(set(baja))} · a revisar: {len(revisar)}")


if __name__ == "__main__":
    main()
