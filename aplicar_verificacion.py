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
}
# Lugares reales que Google no halló con la búsqueda original: se re-buscan con una consulta mejor.
BUSQUEDA_MEJORADA = {
    "Astoria Rooftop": "Astoria Rooftop Calle 85 #12-66 Bogotá",
    "Smoking Molly": "Smoking Molly Blues Jazz Restobar Bogotá",
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

    capa = {"generado": datetime.date.today().isoformat(), "fuente": ruta,
            "excluir": excluir, "confianza_baja": sorted(set(baja)), "ajustes": ajustes, "revisar": revisar}
    with open("app/seed_verificacion.json", "w", encoding="utf-8") as f:
        json.dump(capa, f, ensure_ascii=False, indent=1)
    print(f"\nexcluir: {len(excluir)} · ajustes: {len(ajustes)} · confianza baja: {len(set(baja))} · a revisar: {len(revisar)}")


if __name__ == "__main__":
    main()
