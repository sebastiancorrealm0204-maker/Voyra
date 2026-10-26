"""Descubre lugares de CULTURA, NATURALEZA, MERCADOS y ESCAPADAS para la curación de Bogotá.

descubrir_lugares.py busca por zona (restaurantes, cafés, bares…) y casi no trae museos, miradores, parques, plazas de
mercado ni excursiones: ya están casi todos curados dentro de esas zonas y lo que falta queda fuera de ellas. Este script
busca en TODA la ciudad con consultas pensadas para eso, y en los pueblos de las escapadas de un día (Zipaquirá,
Guatavita, Villa de Leyva…), con umbrales más bajos de reseñas (un museo o una reserva no tiene miles).

Mismo método y mismo formato de reporte que descubrir_lugares.py (Google Places + corroboración con Tavily), así que se
revisa y se aprueba igual: `python aprobar_candidatos.py --archivo reportes/candidatos_cultura_bogota_<fecha>.json ...`.
NADA entra a la curación sin pasar por ahí.

    python descubrir_cultura.py --dry-run
    python descubrir_cultura.py                          # corrida completa
    python descubrir_cultura.py --solo escapadas         # solo pueblos (o: museos, cultura, naturaleza, mercados)
    python descubrir_cultura.py --max-tavily 25

Costo orientativo: ~40 consultas de Google (≈ 1.5 USD, dentro del cupo gratuito) y hasta 40 búsquedas de Tavily.
"""
import argparse
import datetime
import json
import os
import sys
import time

import httpx

from app import geo
from descubrir_lugares import (GOOGLE_KEY, MASK, SEARCH_URL, TAVILY_KEY, ZONAS, _cache, _cargar_cache, _guardar_cache, a_entrada,
                               categoria_de, cargar_curados, clasificar, corroborar, es_duplicado, es_ruido, motivo, normalizar,
                               puntaje, resumen_md, tavily_buscar)

CENTRO = (4.65, -74.07)
RADIO_CIUDAD_KM = 25.0
RADIO_PUEBLO_KM = 15.0
RATING_MIN = 4.3
RESENAS_MIN = 80        # más bajo que en restaurantes: un museo o una reserva no suma miles de reseñas

# grupo → [(consulta, categoría por defecto)]
CIUDAD = {
    "museos": [("museos en Bogotá", "atraccion"), ("casas museo en Bogotá", "atraccion"),
               ("museos de arte en Bogotá", "atraccion"), ("museos de ciencia y para niños en Bogotá", "atraccion"),
               ("museos de historia en Bogotá", "atraccion")],
    "cultura": [("centros culturales en Bogotá", "atraccion"), ("teatros en Bogotá", "atraccion"),
                ("galerías de arte en Bogotá", "atraccion"), ("espacios culturales y bibliotecas en Bogotá", "atraccion"),
                ("iglesias históricas y monumentos en Bogotá", "atraccion"), ("murales y arte urbano en Bogotá", "atraccion")],
    "naturaleza": [("miradores y vistas panorámicas en Bogotá", "mirador"), ("parques metropolitanos y jardines en Bogotá", "parque"),
                   ("senderismo en los cerros orientales de Bogotá", "parque"), ("humedales y reservas naturales en Bogotá", "parque"),
                   ("parques ecológicos y jardín botánico en Bogotá", "parque")],
    "mercados": [("plazas de mercado en Bogotá", "mercado"), ("mercados de pulgas y artesanías en Bogotá", "mercado"),
                 ("mercados campesinos y gastronómicos en Bogotá", "mercado")],
}
# pueblo → centro. Se buscan sus atracciones; todo lo que salga se clasifica como «excursion».
PUEBLOS = {
    "Zipaquirá": (5.0225, -74.0044), "Guatavita": (4.9347, -73.8326), "Nemocón": (5.0677, -73.8786),
    "Chía y Cajicá": (4.8600, -74.0300), "Sopó": (4.9087, -73.9419), "Suesca": (5.1031, -73.7977),
    "Tabio y Tenjo": (4.9150, -74.0960), "Villa de Leyva": (5.6350, -73.5256),
}
EXTRA_NATURALEZA = {"hiking_area", "nature_preserve", "wildlife_park", "national_park", "state_park", "scenic_spot",
                    "botanical_garden", "garden", "park"}


def categoria(g: dict, defecto: str) -> str:
    """Categoría de la curación: por tipo de Google; los miradores por nombre; lo desconocido, la de la consulta."""
    n = (g.get("nombre") or "").lower()
    if "mirador" in n:
        return "mirador"
    cat = categoria_de(g.get("primary", ""), g.get("tipos", []), "")
    if cat:
        return cat
    if (set(g.get("tipos", [])) | {g.get("primary", "")}) & EXTRA_NATURALEZA:
        return "parque"
    return defecto


def google_buscar(cl: httpx.Client, consulta: str, centro: tuple[float, float], radio_m: float) -> list[dict]:
    """Text Search de Google sesgado a un círculo del tamaño pedido (el de descubrir_lugares es de zona: 1.8 km)."""
    clave = f"g2|{consulta}|{int(radio_m)}"
    if clave in _cache:
        return _cache[clave]
    for intento in range(4):
        r = cl.post(SEARCH_URL, headers={"X-Goog-Api-Key": GOOGLE_KEY, "X-Goog-FieldMask": MASK},
                    json={"textQuery": consulta, "languageCode": "es", "regionCode": "CO", "maxResultCount": 20,
                          "locationBias": {"circle": {"center": {"latitude": centro[0], "longitude": centro[1]}, "radius": radio_m}}})
        if r.status_code == 429:
            time.sleep((2 ** intento) * 2)
            continue
        r.raise_for_status()
        _cache[clave] = r.json().get("places", [])
        return _cache[clave]
    return []


def plan(solo: str | None) -> list[tuple[str, str, tuple[float, float], str, float, str]]:
    """[(grupo, consulta, centro, categoría por defecto, radio máx km, zona/pueblo)]."""
    out = []
    for grupo, qs in CIUDAD.items():
        if solo in (None, grupo):
            out += [(grupo, q, CENTRO, cat, RADIO_CIUDAD_KM, "") for q, cat in qs]
    if solo in (None, "escapadas"):
        for pueblo, centro in PUEBLOS.items():
            out += [("escapadas", f"qué visitar en {pueblo}", centro, "excursion", RADIO_PUEBLO_KM, pueblo),
                    ("escapadas", f"atracciones turísticas y naturaleza en {pueblo}", centro, "excursion", RADIO_PUEBLO_KM, pueblo)]
    return out


def zona_de(g: dict, pueblo: str) -> str:
    if pueblo:
        return pueblo
    mejor = min(ZONAS.items(), key=lambda kv: geo.haversine_km(kv[1][0], kv[1][1], g["lat"], g["lng"]))
    return mejor[0] if geo.haversine_km(mejor[1][0], mejor[1][1], g["lat"], g["lng"]) <= 2.5 else "Bogotá"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Cultura, naturaleza, mercados y escapadas de Bogotá (Google Places + Tavily).")
    ap.add_argument("--solo", choices=[*CIUDAD, "escapadas"], default=None)
    ap.add_argument("--rating-min", type=float, default=RATING_MIN)
    ap.add_argument("--resenas-min", type=int, default=RESENAS_MIN)
    ap.add_argument("--max-tavily", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    consultas = plan(a.solo)
    print(f"Plan: {len(consultas)} consultas a Google (≈ {len(consultas) * 0.035:.1f} USD) y hasta {a.max_tavily} búsquedas a Tavily.")
    if a.dry_run:
        for g, q, _, cat, _, _ in consultas[:8]:
            print(f"   · [{g}] {q} → {cat}")
        print("   …")
        return
    if not GOOGLE_KEY or not TAVILY_KEY:
        sys.exit("Faltan GOOGLE_MAPS_API_KEY y/o TAVILY_API_KEY (ponlas en .env).")

    curados = cargar_curados()
    _cargar_cache()
    candidatos: dict[str, dict] = {}
    with httpx.Client(timeout=25) as cl:
        for i, (grupo, q, centro, defecto, radio, pueblo) in enumerate(consultas, 1):
            try:
                lugares = google_buscar(cl, q, centro, 20000.0 if radio == RADIO_CIUDAD_KM else 8000.0)
            except Exception as e:
                print(f"[{i}/{len(consultas)}] {q}: error {e}")
                continue
            nuevos = 0
            for raw in lugares:
                g = normalizar(raw)
                if not g["place_id"] or g["place_id"] in candidatos or g["lat"] is None:
                    continue
                if g["estado"] != "OPERATIONAL" or (g["rating"] or 0) < a.rating_min or (g["resenas"] or 0) < a.resenas_min:
                    continue
                if geo.haversine_km(centro[0], centro[1], g["lat"], g["lng"]) > radio:
                    continue
                if es_ruido(g, list(ZONAS)) or es_duplicado(g["nombre"], g["place_id"], curados):
                    continue
                cat = "excursion" if grupo == "escapadas" else categoria(g, defecto)
                candidatos[g["place_id"]] = {"g": g, "cat": cat, "zona": zona_de(g, pueblo), "grupo": grupo}
                nuevos += 1
            print(f"[{i}/{len(consultas)}] [{grupo}] {q}: {len(lugares)} resultados, {nuevos} candidatos nuevos")
            time.sleep(0.25)
        _guardar_cache()

        orden = sorted(candidatos.values(), key=lambda c: -puntaje(c["g"]["rating"], c["g"]["resenas"]))
        print(f"\n{len(orden)} candidatos únicos; corroborando los {min(len(orden), a.max_tavily)} mejores con Tavily…")
        entradas = []
        for k, c in enumerate(orden):
            g = c["g"]
            if k < a.max_tavily:
                try:
                    res = tavily_buscar(cl, f"\"{g['nombre']}\" {c['zona']} Bogotá")
                except Exception as e:
                    print(f"   Tavily falló en {g['nombre']}: {e}")
                    res = []
                corro = corroborar(g["nombre"], res)
                time.sleep(0.3)
            else:
                corro = {"dominios": [], "menciones": 0, "cierre": False, "fuentes": [], "extractos": []}
            estado = clasificar(corro, g=g)
            e = a_entrada(g, c["cat"], c["zona"], corro, estado)
            e["_evidencia"]["motivo"] = motivo(corro, estado)
            e["_evidencia"]["grupo"] = c["grupo"]
            entradas.append(e)
            print(f"   {estado[:11]:<11} {c['cat']:<10} {g['nombre'][:36]:<36} {g['rating']} ({g['resenas']}) {corro['menciones']} sitios")

    _guardar_cache()
    os.makedirs("reportes", exist_ok=True)
    base = f"reportes/candidatos_cultura_bogota_{datetime.date.today().isoformat()}"
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(entradas, f, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(resumen_md(entradas))
    rec = sum(1 for e in entradas if e["_evidencia"]["estado"] == "recomendado")
    print(f"\nListo: {len(entradas)} candidatos ({rec} recomendados).\nReporte: {base}.md")


if __name__ == "__main__":
    main()
