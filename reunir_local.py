"""Reúne la evidencia para redactar el «dato local» de lugares nuevos (bloque `local` de la curación).

El bloque LOCAL (clave, pedir, ver, momento, ojo, dato, practico) es lo que hace que el Companion hable
como un local y no como Wikipedia (ver app/local_bogota.py). Este script prepara la materia prima de los
lugares aprobados en app/seed_candidatos.json que todavía no tienen `local`:

  · HECHOS de Google (fiables): horario, nivel de precio, resumen editorial y web. De ahí sale `practico`.
  · TEXTO de guías (Tavily): fragmentos que nombran el lugar y hablan de Bogotá, priorizando los que traen
    pistas útiles (qué pedir, recomendaciones, consejos, reserva, hora).

NO redacta ni inventa nada: deja un reporte para que una persona escriba cada campo con lo que las fuentes
sostienen (regla anti-alucinación del producto: mejor un campo vacío que uno falso).

    # Keys en .env: GOOGLE_MAPS_API_KEY y TAVILY_API_KEY
    python reunir_local.py                  # todos los aprobados sin `local`
    python reunir_local.py --solo "Pino Negro,Matik Matik"
    python reunir_local.py --max-tavily 40

Salida: reportes/local_bogota_<fecha>.json y .md.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

import httpx

from descubrir_lugares import _norm, dominio, es_propio_o_red, TAVILY_URL
from verificar_lugares import cargar_env

cargar_env()
GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
DETAILS = "https://places.googleapis.com/v1/places/{pid}"
MASK = "id,displayName,regularOpeningHours.weekdayDescriptions,priceLevel,websiteUri,editorialSummary,rating,userRatingCount"
CACHE_RUTA = "reportes/cache_local.json"

PRECIO = {"PRICE_LEVEL_FREE": "gratis", "PRICE_LEVEL_INEXPENSIVE": "económico", "PRICE_LEVEL_MODERATE": "precio medio",
          "PRICE_LEVEL_EXPENSIVE": "caro", "PRICE_LEVEL_VERY_EXPENSIVE": "muy caro"}
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
ABREV = {"lunes": "lun", "martes": "mar", "miércoles": "mié", "jueves": "jue", "viernes": "vie", "sábado": "sáb", "domingo": "dom"}
PISTAS = re.compile(r"pide|pedir|recomend|especialidad|plato|estrella|imperdible|tip|consejo|reserv|mejor hora|fila|"
                    r"favorito|famos|destaca|no te pierdas|llega|temprano|fin de semana|precio|efectivo|"
                    r"evita|cuidado|ojo|trampa|cobr|lleno|llena|gratis|entrada|boleta|tiquete|cierra|domingo", re.I)
CONSULTAS = {
    "nuevo": '"{n}" Bogotá qué pedir recomendaciones consejos',
    "tips": '"{n}" Bogotá consejos tips evitar filas mejor hora cuidado',
}


# ─────────────────────────── lógica pura (con tests) ───────────────────────────
def resumir_horario(descripciones: list[str]) -> str:
    """['lunes: 12:00–22:00', …] → 'lun–vie 12:00–22:00; sáb 12:00–23:00; dom cerrado' (agrupa días contiguos
    iguales). Vacío si Google no publica horario."""
    por_dia: dict[str, str] = {}
    for d in descripciones or []:
        if ":" not in d:
            continue
        dia, horas = d.split(":", 1)
        dia = dia.strip().lower()
        if dia in ABREV:
            por_dia[dia] = " ".join(horas.replace(" ", " ").replace(" ", " ").split()).lower()
    if not por_dia:
        return ""
    grupos: list[list] = []  # [horas, primer_dia, ultimo_dia]
    for dia in DIAS:
        if dia not in por_dia:
            continue
        if grupos and grupos[-1][0] == por_dia[dia]:
            grupos[-1][2] = dia
        else:
            grupos.append([por_dia[dia], dia, dia])
    partes = []
    for horas, a, b in grupos:
        rango = ABREV[a] if a == b else f"{ABREV[a]}–{ABREV[b]}"
        partes.append(f"{rango} {horas}")
    return "; ".join(partes)


def practico_de(hechos: dict) -> str:
    """Campo `practico` solo con hechos de Google: precio y horario (siempre «según Google»)."""
    bits = []
    if hechos.get("precio"):
        bits.append(hechos["precio"].capitalize())
    if hechos.get("horario"):
        bits.append(f"horario según Google: {hechos['horario']}")
    return ". ".join(bits) + ("." if bits else "")


def mejores_fragmentos(nombre: str, resultados: list[dict], n: int = 6) -> list[dict]:
    """Fragmentos que nombran el lugar Y hablan de Bogotá; primero los que traen pistas útiles."""
    clave = _norm(nombre)
    out = []
    for r in resultados:
        texto = " ".join(f"{r.get('title', '')}. {r.get('content', '')}".split())
        tn = _norm(texto)
        if clave not in tn or "bogot" not in tn:
            continue
        d = dominio(r.get("url", ""))
        out.append({"dominio": d, "propio": es_propio_o_red(d, nombre), "url": r.get("url", ""),
                    "pistas": len(PISTAS.findall(texto)), "texto": texto[:420]})
    out.sort(key=lambda f: (-f["pistas"], f["propio"]))
    return out[:n]


# ─────────────────────────── acceso a Google y Tavily ───────────────────────────
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


def hechos_google(cl: httpx.Client, place_id: str) -> dict:
    k = f"g|{place_id}"
    if k not in _cache:
        r = cl.get(DETAILS.format(pid=place_id), headers={"X-Goog-Api-Key": GOOGLE_KEY, "X-Goog-FieldMask": MASK,
                                                          "Accept-Language": "es"})
        r.raise_for_status()
        g = r.json()
        _cache[k] = {"horario": resumir_horario((g.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []),
                     "precio": PRECIO.get(g.get("priceLevel", ""), ""), "web": g.get("websiteUri", ""),
                     "resumen_google": (g.get("editorialSummary") or {}).get("text", ""),
                     "rating": g.get("rating"), "resenas": g.get("userRatingCount")}
    return _cache[k]


def textos_tavily(cl: httpx.Client, nombre: str, plantilla: str = CONSULTAS["nuevo"]) -> list[dict]:
    q = plantilla.format(n=nombre)
    k = f"t|{q}"
    if k not in _cache:
        r = cl.post(TAVILY_URL, json={"api_key": TAVILY_KEY, "query": q, "topic": "general", "search_depth": "basic",
                                      "max_results": 8, "include_answer": False})
        r.raise_for_status()
        _cache[k] = r.json().get("results", [])
    return _cache[k]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Reúne evidencia para el dato local (no escribe nada en la curación).")
    ap.add_argument("--solo", default="", help="Nombres separados por comas (por defecto, todos los aprobados sin local)")
    ap.add_argument("--max-tavily", type=int, default=40)
    ap.add_argument("--existentes", default="", help="Modo lugares ya curados: nombres separados por comas. "
                    "Busca consejos/trampas (dato y ojo) y muestra su bloque `local` actual.")
    args = ap.parse_args()
    if not GOOGLE_KEY or not TAVILY_KEY:
        sys.exit("Faltan GOOGLE_MAPS_API_KEY y/o TAVILY_API_KEY (ponlas en .env).")

    existentes = [s.strip() for s in args.existentes.split(",") if s.strip()]
    plantilla = CONSULTAS["tips"] if existentes else CONSULTAS["nuevo"]
    if existentes:
        from app import seed_data
        por = {p["name"]: p for p in seed_data.all_seeds() if p["city_display"] == "Bogotá"}
        faltan = [n for n in existentes if n not in por]
        if faltan:
            sys.exit(f"No están en la curación: {faltan}")
        lugares = [por[n] for n in existentes]
        print(f"{len(lugares)} lugares ya curados; hasta {args.max_tavily} búsquedas de Tavily (modo consejos).")
    else:
        ruta = os.path.join("app", "seed_candidatos.json")
        aprobados = json.load(open(ruta, encoding="utf-8"))
        solo = [s.strip() for s in args.solo.split(",") if s.strip()]
        lugares = [p for p in aprobados if (not solo or p["name"] in solo) and not p.get("local")]
        print(f"{len(lugares)} lugares sin `local`; hasta {args.max_tavily} búsquedas de Tavily.")

    _cargar_cache()
    salida = []
    with httpx.Client(timeout=25) as cl:
        for i, p in enumerate(lugares):
            h = hechos_google(cl, p["place_id"]) if p.get("place_id") else {"horario": "", "precio": ""}
            h["practico"] = practico_de(h)
            frags = []
            if i < args.max_tavily:
                try:
                    frags = mejores_fragmentos(p["name"], textos_tavily(cl, p["name"], plantilla))
                except Exception as e:
                    print(f"   Tavily falló en {p['name']}: {e}")
                time.sleep(0.3)
            print(f"[{i + 1}/{len(lugares)}] {p['name']}: {len(frags)} fragmentos · {h['practico'][:60]}")
            salida.append({"name": p["name"], "category": p["category"], "zona": p["zona"], "descripcion": p["descripcion"],
                           "local_actual": p.get("local") or {}, "hechos": h, "fragmentos": frags})
            if (i + 1) % 8 == 0:
                _guardar_cache()
    _guardar_cache()

    os.makedirs("reportes", exist_ok=True)
    base = f"reportes/{'local_existentes' if existentes else 'local_bogota'}_{datetime.date.today().isoformat()}"
    json.dump(salida, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = [f"# Evidencia para el dato local — {datetime.date.today().isoformat()}", ""]
    for s in salida:
        L += [f"## {s['name']} — {s['zona']} · {s['category']}", f"_Descripción actual:_ {s['descripcion']}", ""]
        if s.get("local_actual"):
            L.append("- LOCAL actual: " + " | ".join(f"{k}: {v}" for k, v in s["local_actual"].items()))
        if s["hechos"].get("resumen_google"):
            L.append(f"- Resumen de Google: {s['hechos']['resumen_google']}")
        L.append(f"- practico (Google): {s['hechos']['practico'] or '—'}")
        for f in s["fragmentos"]:
            L.append(f"  - ({f['dominio']}{' · propio' if f['propio'] else ''}) {f['texto']}")
        L.append("")
    open(base + ".md", "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\nListo. Reporte: {base}.md")


if __name__ == "__main__":
    main()
