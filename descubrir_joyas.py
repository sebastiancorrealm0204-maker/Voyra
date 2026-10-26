"""Busca JOYAS ESCONDIDAS de Bogotá en redes y la web, y las deja listas para revisar.

Complementa a descubrir_lugares.py: aquel trae lo bien valorado por zona (lo popular); este busca lo que
«pocos conocen». El flujo:

    1. BUSCAR (Tavily): «lugares escondidos en <zona> Bogotá», «cafés secretos…», etc., en TikTok/Instagram,
       Reddit y la web (blogs, prensa). De los videos y reels Tavily solo devuelve el título/descripción
       (~130 caracteres): no hay transcripción, pero ahí suelen venir los nombres.
    2. EXTRAER (modelo barato): de cada texto saca los lugares CON NOMBRE que menciona y qué dice de ellos.
       No inventa: si el texto no nombra un lugar concreto, no sale nada.
    3. VERIFICAR (Google Places): cada nombre se busca en Bogotá; solo pasa si existe, está operativo, el nombre
       coincide, no está ya en la curación y tiene pocas reseñas (si tuviera miles, ya no sería una joya).
    4. CLASIFICAR: «recomendado» (2+ fuentes, o 1 con rating alto y pocas reseñas), «dudoso», o descartado si algún
       texto habla de cierre.
    5. REPORTE para revisión humana: reportes/joyas_bogota_<fecha>.{json,md}, en el mismo formato que
       descubrir_lugares.py, así que se aprueba igual: `python aprobar_candidatos.py --archivo <json> --aprobar "X"`.
       NADA entra a la curación sin pasar por ahí (regla de confianza de Voyra). La `descripcion` viene
       pre-escrita con lo que dicen las fuentes: léela y corrígela antes de aprobar.

    # .env (junto a este script): GOOGLE_MAPS_API_KEY, TAVILY_API_KEY y una key de LLM (p. ej. DEEPSEEK_API_KEY)
    python descubrir_joyas.py --dry-run
    python descubrir_joyas.py                                   # corrida completa (límites por defecto)
    python descubrir_joyas.py --zonas "La Candelaria,Chapinero" --fuentes social,reddit
    python descubrir_joyas.py --max-tavily 15 --max-google 40   # gasta menos cuota

Costo orientativo: hasta 36 búsquedas de Tavily (de las 1.000 gratis al mes), ~15 llamadas al modelo barato
(centavos) y hasta 80 consultas de Google (~3 USD, dentro del cupo gratuito mensual).
"""
import argparse
import datetime
import difflib
import hashlib
import json
import os
import sys
import time

import httpx

from app import geo
from descubrir_lugares import (MASK, SEARCH_URL, SENAL_CIERRE, ZONAS, _norm, a_entrada, categoria_de, cargar_curados,
                               dominio, es_duplicado, es_ruido, normalizar, puntaje)
from verificar_lugares import cargar_env

cargar_env()
GOOGLE_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_URL = "https://api.tavily.com/search"

CENTRO_BOGOTA = (4.65, -74.07)
RADIO_BOGOTA_KM = 20.0
RESENAS_MIN = 15      # menos que esto: la ficha de Google no es fiable todavía
RESENAS_MAX = 800     # más que esto: ya no es una joya escondida
RATING_MIN = 4.3

FUENTES = {
    "social": ["tiktok.com", "instagram.com"],
    "reddit": ["reddit.com"],
    "web": None,   # blogs, prensa, guías: sin filtro de dominio
}
# Búsquedas base (se completan por zona más abajo). Los TikToks suelen titularse justo así.
BASE = [
    "lugares escondidos en Bogotá que pocos conocen",
    "restaurantes ocultos en Bogotá secretos",
    "cafés escondidos en Bogotá joyas ocultas",
    "bares secretos en Bogotá que solo conocen los locales",
    "planes diferentes en Bogotá poco conocidos",
    "joyas escondidas de Bogotá qué comer como bogotano",
]
POR_ZONA = "lugares escondidos en {zona} Bogotá"
ZONAS_JOYAS = ["La Candelaria", "Chapinero", "Usaquén", "Teusaquillo", "La Macarena", "Quinta Camacho"]
TIPOS_VALIDOS = {"restaurante", "cafe", "bar", "atraccion", "parque", "mercado", "experiencia"}
# Cadenas y sitios masivos: no son joyas.
CADENAS = {"starbucks", "juan valdez", "crepes waffles", "mcdonalds", "burger king", "kfc", "dominos", "subway",
           "frisby", "el corral", "andres carne de res", "tostao", "oma", "wok", "home burgers"}

SISTEMA = (
    "Eres el extractor de 'joyas escondidas' de Voyra, una guía local de Bogotá. Recibes textos numerados (títulos y "
    "descripciones de TikToks, reels, hilos de Reddit y blogs). Devuelve SOLO JSON con esta forma:\n"
    '{"lugares": [{"nombre": "nombre exacto del lugar", "tipo": "restaurante|cafe|bar|atraccion|parque|mercado|'
    'experiencia", "barrio": "barrio o zona si el texto lo dice, o vacío", "detalle": "una frase de máximo 160 '
    'caracteres con lo que el texto dice del lugar", "fuentes": [números de los textos que lo mencionan]}]}\n'
    "REGLAS:\n"
    "• Solo lugares CONCRETOS con nombre propio que el texto menciona de forma explícita y que estén en Bogotá.\n"
    "• NO inventes nombres ni completes con tu conocimiento. Si el texto solo dice «un lugar escondido» sin nombre, "
    "no lo incluyas.\n"
    "• Excluye barrios, calles, ciudades, cadenas conocidas, centros comerciales y los grandes íconos turísticos "
    "(Monserrate, Museo del Oro, Plaza de Bolívar…): buscamos lo que pocos conocen.\n"
    "• `detalle` solo con lo que dice el texto sobre ESE lugar (qué es, qué pedir, dónde queda), sin hype ('increíble', "
    "'el mejor'). Si el texto solo lo lista o lo nombra sin decir nada concreto, deja `detalle` vacío. Sin emojis.\n"
    "• Si el texto habla de otra ciudad (Guadalajara, Medellín, Cartagena…) y no de Bogotá, ignóralo.\n"
    "• Si no hay ningún lugar válido, devuelve {\"lugares\": []}."
)


# ─────────────────────────── lógica pura (con tests) ───────────────────────────
def plan_busquedas(zonas: list[str], fuentes: list[str], maximo: int) -> list[tuple[str, str]]:
    """[(consulta, fuente)] intercalando consultas y fuentes para que un tope bajo siga cubriendo variedad."""
    consultas = BASE + [POR_ZONA.format(zona=z) for z in zonas]
    out = [(q, f) for q in consultas for f in fuentes]
    return out[:maximo]


def lotes(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def prompt_usuario(textos: list[dict]) -> str:
    return "\n".join(f"[{i}] ({t['dominio']}) {t['titulo']} — {t['contenido']}" for i, t in enumerate(textos))


def parsear_extraccion(raw: str, textos: list[dict]) -> list[dict]:
    """JSON del modelo → lugares limpios con sus fuentes resueltas. Tolerante: lo que no cuadre se ignora."""
    try:
        data = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except (ValueError, AttributeError):
        return []
    out = []
    for p in (data.get("lugares") if isinstance(data, dict) else None) or []:
        if not isinstance(p, dict):
            continue
        nombre = str(p.get("nombre") or "").strip()
        if len(nombre) < 3 or _norm(nombre) in CADENAS or any(c in _norm(nombre) for c in CADENAS):
            continue
        idx = [i for i in (p.get("fuentes") or []) if isinstance(i, int) and 0 <= i < len(textos)]
        if not idx:
            continue
        tipo = str(p.get("tipo") or "").strip().lower()
        out.append({"nombre": nombre, "tipo": tipo if tipo in TIPOS_VALIDOS else "",
                    "barrio": str(p.get("barrio") or "").strip(), "detalle": str(p.get("detalle") or "").strip()[:200],
                    "fuentes": [{"dominio": textos[i]["dominio"], "url": textos[i]["url"],
                                 "titulo": textos[i]["titulo"][:120], "texto": textos[i]["contenido"][:320]} for i in idx]})
    return out


def agrupar(extracciones: list[dict]) -> dict[str, dict]:
    """Une el mismo lugar nombrado en varios textos: {nombre normalizado: {nombre, tipo, barrio, detalles, fuentes}}."""
    por: dict[str, dict] = {}
    for e in extracciones:
        k = _norm(e["nombre"])
        g = por.setdefault(k, {"nombre": e["nombre"], "tipo": "", "barrio": "", "detalles": [], "fuentes": []})
        g["tipo"] = g["tipo"] or e["tipo"]
        g["barrio"] = g["barrio"] or e["barrio"]
        if e["detalle"] and e["detalle"] not in g["detalles"]:
            g["detalles"].append(e["detalle"])
        urls = {f["url"] for f in g["fuentes"]}
        g["fuentes"] += [f for f in e["fuentes"] if f["url"] not in urls]
    return por


def evidencia(grupo: dict) -> dict:
    """Formato que usan los reportes de descubrir_lugares.py (mismas claves que lee aprobar_candidatos.py)."""
    fuentes = grupo["fuentes"]
    return {"dominios": sorted({f["dominio"] for f in fuentes}), "menciones": len(fuentes),
            "cierre": any(SENAL_CIERRE.search(f"{f['titulo']} {f['texto']}") for f in fuentes),
            "fuentes": [{"dominio": f["dominio"], "url": f["url"], "titulo": f["titulo"]} for f in fuentes[:4]],
            "extractos": [f["texto"] for f in fuentes[:3]]}


OTRAS_CIUDADES = ("guadalajara", "medellin", "cartagena", "cali ", "mexico", "cdmx", "lima", "quito", "buenos aires",
                  "santiago de chile", "barranquilla", "bucaramanga", "madrid", "barcelona")


def texto_de_otra_ciudad(titulo: str, contenido: str, url: str = "") -> bool:
    """Un hilo de r/Guadalajara o un reel de Medellín no habla de Bogotá aunque la búsqueda haya traído el nombre."""
    t = _norm(f"{titulo} {contenido} {url.replace('/', ' ').replace('_', ' ')}")
    t = f" {' '.join(t.split())} "
    return "bogot" not in t and any(f" {c.strip()} " in t for c in OTRAS_CIUDADES)  # palabra completa: «clima» no es «lima»


def nombre_coincide(buscado: str, de_google: str) -> bool:
    """Más estricto que verificar_lugares._nombres_parecidos: compartir una sola palabra no basta (asociaba «Éntrenos»,
    un speakeasy, con «Evoluciona Entrenamientos», un gimnasio). Pasa si todas las palabras del nombre más corto están
    en el otro, o si el texto es casi igual."""
    a, b = _norm(buscado), _norm(de_google)
    if not a or not b:
        return False
    ta, tb = {w for w in a.split() if len(w) > 2}, {w for w in b.split() if len(w) > 2}
    if ta and tb and (ta <= tb or tb <= ta):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def dentro_de_bogota(g: dict) -> bool:
    return g.get("lat") is not None and geo.haversine_km(CENTRO_BOGOTA[0], CENTRO_BOGOTA[1], g["lat"], g["lng"]) <= RADIO_BOGOTA_KM


def es_joya(g: dict, resenas_min: int = RESENAS_MIN, resenas_max: int = RESENAS_MAX, rating_min: float = RATING_MIN) -> bool:
    """Operativo, buen rating, y pocas reseñas: bien valorado pero poco conocido."""
    r, n = g.get("rating") or 0, g.get("resenas") or 0
    return g.get("estado") == "OPERATIONAL" and r >= rating_min and resenas_min <= n <= resenas_max


def clasificar(corro: dict, g: dict) -> str:
    if corro["cierre"]:
        return "descartado_cierre"
    if corro["menciones"] >= 2 or (corro["menciones"] >= 1 and (g.get("rating") or 0) >= 4.6 and (g.get("resenas") or 0) <= 400):
        return "recomendado"
    return "dudoso"


def zona_de(g: dict, barrio: str) -> str:
    """La zona curada más cercana (≤ 2.5 km); si no hay, el barrio que dijo la fuente."""
    mejor = min(ZONAS.items(), key=lambda kv: geo.haversine_km(kv[1][0], kv[1][1], g["lat"], g["lng"]))
    if geo.haversine_km(mejor[1][0], mejor[1][1], g["lat"], g["lng"]) <= 2.5:
        return mejor[0]
    return barrio or "Bogotá"


def a_entrada_joya(g: dict, grupo: dict, corro: dict, estado: str) -> dict:
    cat = categoria_de(g["primary"], g["tipos"], "") or grupo["tipo"] or "atraccion"
    e = a_entrada(g, cat, zona_de(g, grupo["barrio"]), corro, estado)
    detalle = " ".join(grupo["detalles"][:2]).strip()
    e["descripcion"] = (f"Según redes y guías locales: {detalle}" if detalle else "")  # para REVISAR antes de aprobar
    e["tags"] = ["Joyas escondidas"]
    e["_evidencia"]["origen"] = "joyas"
    e["_evidencia"]["detalles"] = grupo["detalles"]
    return e


def resumen_md(entradas: list[dict]) -> str:
    por = {"recomendado": [], "dudoso": [], "descartado_cierre": []}
    for e in entradas:
        por[e["_evidencia"]["estado"]].append(e)
    L = [f"# Joyas escondidas de Bogotá — {datetime.date.today().isoformat()}",
         f"_{len(entradas)} candidatas: {len(por['recomendado'])} recomendadas, {len(por['dudoso'])} dudosas, "
         f"{len(por['descartado_cierre'])} descartadas por señal de cierre._", "",
         "La `descripcion` viene pre-escrita con lo que dicen las fuentes: **léela y corrígela** en el JSON antes de aprobar con "
         "`python aprobar_candidatos.py --archivo <json> --aprobar \"Nombre\"`.", ""]
    for titulo, clave in (("Recomendadas", "recomendado"), ("Dudosas (1 sola fuente)", "dudoso"),
                          ("Descartadas: algún texto habla de cierre", "descartado_cierre")):
        if not por[clave]:
            continue
        L += [f"## {titulo}", ""]
        for e in sorted(por[clave], key=lambda x: -x["_evidencia"]["menciones"]):
            ev = e["_evidencia"]
            L.append(f"### {e['name']} — {e['zona']} · {e['category']}")
            L.append(f"- Google: {e.get('rating')} ({ev['resenas']} reseñas) · {e['dir']}")
            L.append(f"- Mencionada en {ev['menciones']} fuente(s): {', '.join(ev['dominios'])}")
            for f in ev["fuentes"][:3]:
                L.append(f"  - [{f['titulo'] or f['dominio']}]({f['url']})")
            for d in ev.get("detalles", [])[:2]:
                L.append(f"  - «{d}»")
            if ev.get("maps"):
                L.append(f"- [Google Maps]({ev['maps']})")
            L.append("")
    return "\n".join(L) + "\n"


# ─────────────────────────── acceso a Tavily, LLM y Google ───────────────────────────
CACHE_RUTA = "reportes/cache_joyas.json"
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


def tavily(cl: httpx.Client, consulta: str, fuente: str) -> list[dict]:
    clave = f"t|{fuente}|{consulta}"
    if clave not in _cache:
        cuerpo = {"api_key": TAVILY_KEY, "query": consulta, "topic": "general", "search_depth": "basic",
                  "max_results": 8, "include_answer": False}
        if FUENTES[fuente]:
            cuerpo["include_domains"] = FUENTES[fuente]
        r = cl.post(TAVILY_URL, json=cuerpo)
        r.raise_for_status()
        _cache[clave] = r.json().get("results", [])
    return _cache[clave]


def llm_extraer(textos: list[dict]) -> list[dict]:
    from app import llm
    clave = "l|" + hashlib.sha1(SISTEMA.encode("utf-8")).hexdigest()[:8] + "|" + "|".join(sorted(t["url"] for t in textos))
    if clave not in _cache:
        _cache[clave] = llm._call(llm.EXTRACT_PROVIDER, SISTEMA, [{"role": "user", "content": prompt_usuario(textos)}],
                                  max_tokens=1800)
    return parsear_extraccion(_cache[clave], textos)


def google_verificar(cl: httpx.Client, nombre: str) -> dict | None:
    """La ficha de Google del lugar (normalizada) o None si no hay una que coincida claramente."""
    clave = f"g|{nombre}"
    if clave not in _cache:
        for intento in range(4):
            r = cl.post(SEARCH_URL, headers={"X-Goog-Api-Key": GOOGLE_KEY, "X-Goog-FieldMask": MASK},
                        json={"textQuery": f"{nombre} Bogotá", "languageCode": "es", "regionCode": "CO", "maxResultCount": 3,
                              "locationBias": {"circle": {"center": {"latitude": CENTRO_BOGOTA[0], "longitude": CENTRO_BOGOTA[1]},
                                                          "radius": 15000.0}}})
            if r.status_code == 429:
                time.sleep((2 ** intento) * 2)
                continue
            r.raise_for_status()
            _cache[clave] = r.json().get("places", [])
            break
        else:
            return None
    for raw in _cache[clave]:
        g = normalizar(raw)
        if g["place_id"] and nombre_coincide(nombre, g["nombre"]) and dentro_de_bogota(g):
            return g
    return None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Busca joyas escondidas de Bogotá (Tavily + LLM + Google Places). Solo genera un reporte.")
    ap.add_argument("--zonas", default="", help=f"Lista separada por comas (por defecto: {', '.join(ZONAS_JOYAS)})")
    ap.add_argument("--fuentes", default="social,reddit,web", help="social, reddit, web (separadas por comas)")
    ap.add_argument("--max-tavily", type=int, default=36)
    ap.add_argument("--max-google", type=int, default=80)
    ap.add_argument("--resenas-max", type=int, default=RESENAS_MAX)
    ap.add_argument("--rating-min", type=float, default=RATING_MIN)
    ap.add_argument("--dry-run", action="store_true", help="Solo muestra el plan y el costo")
    a = ap.parse_args()

    zonas = [z.strip() for z in a.zonas.split(",") if z.strip()] or ZONAS_JOYAS
    fuentes = [f.strip() for f in a.fuentes.split(",") if f.strip()]
    if [f for f in fuentes if f not in FUENTES]:
        sys.exit(f"Fuentes válidas: {list(FUENTES)}")
    plan = plan_busquedas(zonas, fuentes, a.max_tavily)
    print(f"Plan: {len(plan)} búsquedas en Tavily ({', '.join(fuentes)}), unas {-(-len(plan) * 8 // 10)} llamadas al "
          f"modelo y hasta {a.max_google} consultas a Google (~{a.max_google * 0.035:.1f} USD).")
    if a.dry_run:
        for q, f in plan[:10]:
            print(f"   · [{f}] {q}")
        print("   …")
        return
    from app import llm
    if not GOOGLE_KEY or not TAVILY_KEY:
        sys.exit("Faltan GOOGLE_MAPS_API_KEY y/o TAVILY_API_KEY (ponlas en .env).")
    if not llm._available():
        sys.exit("Falta una key de LLM en .env (p. ej. DEEPSEEK_API_KEY=...): hace falta para extraer los nombres de los textos.")

    _cargar_cache()
    curados = cargar_curados()
    with httpx.Client(timeout=30) as cl:
        textos: dict[str, dict] = {}
        for i, (q, f) in enumerate(plan, 1):
            try:
                res = tavily(cl, q, f)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    sys.exit(f"Tavily rechazó la key ({e.response.status_code}). Revisa TAVILY_API_KEY en .env: sin comillas, "
                             "sin espacios y completa (empieza por «tvly-»). Si la cambiaste hoy, confirma en app.tavily.com que sigue activa.")
                if e.response.status_code in (429, 432, 433):
                    print(f"[{i}/{len(plan)}] Tavily sin cupo ({e.response.status_code}); sigo con lo que ya tengo.")
                    break
                print(f"[{i}/{len(plan)}] {f}: {q} -> error {e.response.status_code}")
                continue
            except Exception as e:
                print(f"[{i}/{len(plan)}] {f}: {q} -> error {e}")
                continue
            nuevos = 0
            for r in res:
                u = r.get("url", "")
                if u and u not in textos and (r.get("content") or r.get("title")) \
                        and not texto_de_otra_ciudad(r.get("title") or "", r.get("content") or "", u):
                    textos[u] = {"url": u, "dominio": dominio(u), "titulo": (r.get("title") or "").strip(),
                                 "contenido": (r.get("content") or "").strip()[:400]}
                    nuevos += 1
            print(f"[{i}/{len(plan)}] {f}: {q} -> {len(res)} resultados, {nuevos} textos nuevos")
            time.sleep(0.3)
        _guardar_cache()

        if not textos:
            sys.exit("Tavily no devolvió ningún texto; no hay nada que analizar (no se escribe reporte).")
        print(f"\n{len(textos)} textos únicos; extrayendo lugares con el modelo…")
        extr = []
        for lote in lotes(list(textos.values()), 10):
            try:
                extr += llm_extraer(lote)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    sys.exit(f"El proveedor del modelo rechazó la key ({e.response.status_code}). Revisa DEEPSEEK_API_KEY en .env: "
                             "sin comillas ni espacios, completa (empieza por «sk-»), y que la cuenta tenga saldo en platform.deepseek.com. "
                             "Los textos de Tavily ya quedaron en caché: al repetir no se gasta Tavily otra vez.")
                print(f"   el modelo falló en un lote: {e.response.status_code}")
            except Exception as e:
                print(f"   el modelo falló en un lote: {e}")
        _guardar_cache()
        grupos = agrupar(extr)
        print(f"{len(grupos)} lugares distintos nombrados; verificando en Google (tope {a.max_google})…")

        entradas, vistos = [], set()
        orden = sorted(grupos.values(), key=lambda g: -len(g["fuentes"]))
        for k, grupo in enumerate(orden[: a.max_google], 1):
            try:
                g = google_verificar(cl, grupo["nombre"])
            except Exception as e:
                print(f"   Google falló en {grupo['nombre']}: {e}")
                continue
            if not g or g["place_id"] in vistos:
                print(f"   no encontrado  {grupo['nombre']}")
                continue
            vistos.add(g["place_id"])
            if es_ruido(g, list(ZONAS)) or es_duplicado(g["nombre"], g["place_id"], curados):
                print(f"   ya curado/ruido {g['nombre']}")
                continue
            if not categoria_de(g["primary"], g["tipos"], ""):
                print(f"   tipo no válido  {g['nombre']} ({g['primary'] or 'sin tipo'}): no es gastronomía, cultura ni ocio")
                continue
            if not es_joya(g, resenas_max=a.resenas_max, rating_min=a.rating_min):
                print(f"   no es joya      {g['nombre']} ({g.get('rating')}, {g.get('resenas')} reseñas, {g.get('estado')})")
                continue
            corro = evidencia(grupo)
            estado = clasificar(corro, g)
            print(f"   {estado[:11]:<11} {g['nombre'][:38]:<38} {g.get('rating')} ({g.get('resenas')}) {corro['menciones']} fuentes")
            entradas.append(a_entrada_joya(g, grupo, corro, estado))
            if k % 10 == 0:
                _guardar_cache()

    _guardar_cache()
    os.makedirs("reportes", exist_ok=True)
    base = f"reportes/joyas_bogota_{datetime.date.today().isoformat()}"
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(entradas, f, ensure_ascii=False, indent=1)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(resumen_md(entradas))
    rec = sum(1 for e in entradas if e["_evidencia"]["estado"] == "recomendado")
    print(f"\nListo: {len(entradas)} joyas candidatas ({rec} recomendadas).\nReporte: {base}.md")


if __name__ == "__main__":
    main()
