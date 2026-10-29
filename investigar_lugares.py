"""Investigación PROFUNDA de lugares curados: historia, qué hacer, qué NO hacer, qué probar y SECRETOS.

El bloque `local` (redactar_local.py) responde lo que un local te diría en la puerta en una frase. Esta capa va más allá: es la
ficha completa que se abre al tocar una notificación o una tarjeta, y lo que hace que Companion no suene a un chat genérico.

Para cada lugar:
    1. WEB OFICIAL (Google Places → websiteUri, leída con Tavily Extract): la carta, la historia, lo que ellos destacan.
    2. GUÍAS, BLOGS, PRENSA Y FOROS (Tavily con texto completo): dos búsquedas, una general y otra pidiendo consejos y
       secretos; de cada página que nombra el lugar se guarda la ventana de texto alrededor del nombre.
    3. REDACCIÓN (modelo): historia, hacer, no_hacer, probar y secretos. Cada ítem trae una `cita` de la evidencia si la hay.
    4. ORIGEN: si la cita aparece literal en la evidencia, el ítem queda `origen: "fuente"` con la URL de la página; si no,
       queda `origen: "modelo"` (conocimiento del modelo). Decisión del fundador: se permite, pero marcado, para revisarlo
       en el reporte. A los ítems del modelo se les quitan precios, horarios y cifras (lo que más se inventa).

Escribe la capa app/lugares_profundo.json (seed_data la pega como local["profundo"]) y un reporte para revisar:
reportes/profundo_<fecha>.md. Léelo antes de hacer commit.

    python investigar_lugares.py --piloto 20 --dry-run     # muestra qué lugares tomaría
    python investigar_lugares.py --piloto 20               # corre el piloto
    python investigar_lugares.py --solo "Andrés Carne de Res,Museo del Oro"
    python investigar_lugares.py --todos --max 100         # siguientes 100 sin capa profunda

Costo orientativo por lugar: 2 búsquedas de Tavily (basic, con texto completo) + 1 extract de la web oficial + 1 llamada al modelo.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

import httpx

from ampliar_evidencia import SIN_TEXTO, nombre_busqueda, ventana
from descubrir_lugares import dominio
from redactar_local import _META, _PRECIO_HORA, _VAGO, _n, _tokens
from verificar_lugares import cargar_env

RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "lugares_profundo.json")
TAVILY_SEARCH = "https://api.tavily.com/search"
TAVILY_EXTRACT = "https://api.tavily.com/extract"
DETAILS = "https://places.googleapis.com/v1/places/{pid}"
CONSULTAS = ('"{n}" Bogotá', '{n} Bogotá consejos secretos tips reseña blog')
CONSULTA_EXTRA = '{n} Bogotá historia guía qué hacer'   # solo si las dos primeras trajeron menos de MIN_PAGINAS
MIN_PAGINAS = 3
VENTANA_PAG = 2600   # caracteres alrededor del nombre en cada página
MAX_PAGINAS = 7
MAX_WEB = 3500       # caracteres de la web oficial
MIN_CITA = 18
CATEGORIAS = ("restaurante", "cafe", "bar", "atraccion", "museo", "mercado", "parque", "experiencia", "excursion", "compras")
LISTAS = {"hacer": 4, "no_hacer": 3, "probar": 4, "secretos": 4}

SISTEMA = (
    "Eres el investigador de Companion, una guía local de Bogotá que tiene que saber MÁS que cualquier chat genérico. Con la "
    "evidencia que te doy (web oficial del lugar, guías, blogs, prensa, foros) y lo que sepas con seguridad del lugar, escribe su "
    "ficha profunda. Devuelve SOLO JSON:\n"
    '{"historia": {"texto": "2-4 frases: origen, quién lo creó, qué lo hace único, contexto del barrio", "cita": "..."}, '
    '"hacer": [{"texto": "...", "cita": "..."}], "no_hacer": [...], "probar": [...], "secretos": [...]}\n'
    "- hacer (2-4): qué hacer o ver ahí, concreto (una sala, una terraza, un recorrido, una actividad).\n"
    "- no_hacer (1-3): errores típicos del visitante: lo que decepciona, la trampa, cuándo no ir, lo que no se permite.\n"
    "- probar (1-4): platos, bebidas, productos o experiencias concretas, con su nombre propio.\n"
    "- secretos (2-4): lo que casi nadie sabe y un local sí: la mesa o el rincón que pedir, lo que está fuera de carta, el detalle "
    "escondido en la arquitectura, la historia detrás del nombre, la hora mágica, el truco para entrar sin fila. Nada obvio: si "
    "sale en la primera línea de Google, no es secreto.\n"
    "`cita`: fragmento COPIADO LITERALMENTE de la evidencia (mínimo 18 caracteres) que respalda el ítem. Si el ítem sale de lo que "
    "sabes y no de la evidencia, deja la cita vacía \"\" (se marcará como conocimiento propio para revisión).\n"
    "REGLAS: solo afirma lo que sabes con seguridad que es cierto de ESTE lugar (no de otro con nombre parecido ni de otra sede). "
    "Ante la duda, omítelo: es MUCHO mejor devolver 1 secreto real que 3 genéricos. Un ítem que serviría para cualquier lugar de su "
    "tipo («llega temprano», «pregunta por las promociones», «reserva el fin de semana», «siéntate cerca de la tarima») NO sirve: "
    "cada ítem debe nombrar algo propio de ESTE lugar. Si no tienes evidencia ni certeza sobre un campo, devuelve la lista vacía. "
    "No inventes detalles físicos (mesas, enchufes, rincones) ni platos que no estén en la evidencia. No gastes ítems en horarios, "
    "en «revisa el menú» o en «verifica antes de ir»: eso ya lo muestra la app. No inventes precios, horarios, fechas ni nombres de chefs. Sin hype («increíble», «el mejor»), sin emojis, "
    "sin signos de exclamación. Español neutro, tono de amigo local, una o dos frases por ítem. No repitas lo que ya dice la ficha "
    "actual que te paso: aporta lo NUEVO."
)


def tavily_buscar(cl: httpx.Client, key: str, q: str) -> list[dict]:
    r = cl.post(TAVILY_SEARCH, json={"api_key": key, "query": q, "search_depth": "basic", "max_results": 8,
                                     "include_raw_content": True, "include_answer": False})
    r.raise_for_status()
    return r.json().get("results", [])


def tavily_extraer(cl: httpx.Client, key: str, url: str) -> str:
    r = cl.post(TAVILY_EXTRACT, json={"api_key": key, "urls": [url]})
    r.raise_for_status()
    res = r.json().get("results") or []
    return " ".join(((res[0].get("raw_content") if res else "") or "").split())


def web_oficial(cl: httpx.Client, gkey: str, place_id: str | None) -> str:
    if not place_id or not gkey:
        return ""
    r = cl.get(DETAILS.format(pid=place_id), headers={"X-Goog-Api-Key": gkey, "X-Goog-FieldMask": "websiteUri"})
    return (r.json().get("websiteUri") or "") if r.status_code == 200 else ""


def paginas(resultados: list[dict], nombre: str, ya: set[str]) -> list[dict]:
    """Páginas (no redes ni domicilios) que nombran el lugar y hablan de Bogotá, con la ventana de texto alrededor del nombre."""
    out = []
    for r in resultados:
        url = r.get("url", "")
        dom = dominio(url)
        if not dom or dom in ya or any(dom == d or dom.endswith("." + d) for d in SIN_TEXTO):
            continue
        texto = r.get("raw_content") or r.get("content") or ""
        v = ventana(texto, nombre, VENTANA_PAG)
        if not v or "bogot" not in _n(texto + " " + (r.get("title") or "")):
            continue
        ya.add(dom)
        out.append({"dominio": dom, "url": url, "titulo": r.get("title") or "", "texto": v})
    return out


def nombre_nucleo(nombre: str) -> str:
    """«Sala de Despecho Bogotá» → «Sala de Despecho»; «Suesca (senderismo y escalada)» → «Suesca». Con la ciudad pegada al
    nombre, la búsqueda exacta y la ventana de texto fallaban en lugares muy documentados."""
    n = re.sub(r"\s+(en\s+)?bogot[aá]\s*$", "", nombre_busqueda(nombre), flags=re.I).strip()
    return n or nombre_busqueda(nombre)


def reunir(cl: httpx.Client, tkey: str, gkey: str, p: dict) -> dict:
    nombre = nombre_nucleo(p["name"])
    ev = {"name": p["name"], "web": "", "web_texto": "", "paginas": []}
    try:
        ev["web"] = web_oficial(cl, gkey, p.get("place_id"))
    except Exception as ex:
        print(f"   google: {type(ex).__name__}")
    if ev["web"] and not any(d in ev["web"] for d in SIN_TEXTO):
        try:
            ev["web_texto"] = tavily_extraer(cl, tkey, ev["web"])[:MAX_WEB]
        except Exception as ex:
            print(f"   extract: {type(ex).__name__}")
    ya = {dominio(ev["web"])} if ev["web"] else set()
    for plantilla in CONSULTAS:
        ev["paginas"] += paginas(tavily_buscar(cl, tkey, plantilla.format(n=nombre)), nombre, ya)
        time.sleep(0.3)
    if len(ev["paginas"]) < MIN_PAGINAS:
        ev["paginas"] += paginas(tavily_buscar(cl, tkey, CONSULTA_EXTRA.format(n=nombre)), nombre, ya)
    ev["paginas"] = ev["paginas"][:MAX_PAGINAS]
    return ev


def ficha_actual(p: dict) -> str:
    L = [f"Descripción: {p.get('descripcion', '')}"]
    for k, v in (p.get("local") or {}).items():
        if isinstance(v, str) and v and k not in ("practico", "extranjero"):
            L.append(f"{k}: {v}")
    return "\n".join(L)


def prompt_usuario(p: dict, ev: dict) -> str:
    L = [f"Lugar: {p['name']} ({p.get('category')}, {p.get('zona')}, Bogotá)", "", "FICHA ACTUAL (no la repitas):", ficha_actual(p), ""]
    if ev["web_texto"]:
        L += [f"WEB OFICIAL ({ev['web']}):", ev["web_texto"], ""]
    for i, pg in enumerate(ev["paginas"], 1):
        L += [f"FUENTE {i} ({pg['dominio']} — {pg['titulo'][:90]}):", pg["texto"], ""]
    if not ev["web_texto"] and not ev["paginas"]:
        L.append("(No hay evidencia web: usa solo lo que sepas con seguridad y deja las citas vacías.)")
    return "\n".join(L)


def _fuentes(ev: dict) -> list[tuple[str, str]]:
    """[(url, texto normalizado)] de cada fuente, para ubicar de qué página sale una cita."""
    out = [(ev["web"], _n(ev["web_texto"]))] if ev["web_texto"] else []
    return out + [(pg["url"], _n(pg["texto"])) for pg in ev["paginas"]]


def item(raw, fuentes: list[tuple[str, str]]) -> dict | None:
    """{'texto','cita'} del modelo → {'texto','origen','url'?}, o None si no sirve."""
    if isinstance(raw, str):
        raw = {"texto": raw, "cita": ""}
    if not isinstance(raw, dict):
        return None
    t = " ".join((raw.get("texto") or "").split())
    if len(t.split()) < 4 or _META.search(t) or _VAGO.search(t) or "!" in t:
        return None
    c = _n(raw.get("cita") or "")
    url = next((u for u, txt in fuentes if len(c) >= MIN_CITA and c in txt), None) if c else None
    if url is None and _PRECIO_HORA.search(t):
        return None   # cifras sin respaldo: lo que más se inventa
    return {"texto": t[:320], "origen": "fuente", "url": url} if url else {"texto": t[:320], "origen": "modelo"}


def validar(raw: str, ev: dict) -> dict | None:
    try:
        data = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    fuentes = _fuentes(ev)
    out: dict = {}
    h = item(data.get("historia"), fuentes)
    if h:
        out["historia"] = h
    vistos: list[set[str]] = [_tokens(h["texto"])] if h else []
    for k, maximo in LISTAS.items():
        lst = []
        for r in data.get(k) or []:
            it = item(r, fuentes)
            if not it:
                continue
            tv = _tokens(it["texto"])
            if any(tv and len(tv & o) / max(1, min(len(tv), len(o))) >= 0.6 for o in vistos):
                continue   # repite algo ya dicho
            vistos.append(tv)
            lst.append(it)
        if lst:
            out[k] = lst[:maximo]
    if not out.get("secretos") and not out.get("hacer"):
        return None
    if not any(it["origen"] == "fuente" for k in LISTAS for it in out.get(k, [])):
        return None   # ni un ítem respaldado por una fuente: sería contenido genérico
    out["fuentes"] = [u for u, _ in fuentes]
    out["fecha"] = datetime.date.today().isoformat()
    return out


def llamar_modelo(p: dict, ev: dict) -> str:
    from app import llm
    return llm._call(llm.EXTRACT_PROVIDER, SISTEMA, [{"role": "user", "content": prompt_usuario(p, ev)}], max_tokens=1600)


def piloto(lugares: list[dict], n: int) -> list[dict]:
    """Los mejor calificados de cada categoría, alternando categorías para que el piloto muestre de todo."""
    por_cat: dict[str, list[dict]] = {}
    vistos: set[str] = set()
    for p in sorted(lugares, key=lambda q: -(q.get("rating") or 0)):
        if p["name"] in vistos:
            continue   # el mismo lugar puede venir en dos zonas del seed
        vistos.add(p["name"])
        if p.get("category") in CATEGORIAS and p.get("place_id") and p.get("local"):
            por_cat.setdefault(p["category"], []).append(p)
    out: list[dict] = []
    while len(out) < n and any(por_cat.values()):
        for cat in CATEGORIAS:
            if por_cat.get(cat) and len(out) < n:
                out.append(por_cat[cat].pop(0))
    return out


def resumen_md(hechos: list[tuple[dict, dict]], fallidos: list[str]) -> str:
    L = [f"# Investigación profunda — {datetime.date.today().isoformat()}", "",
         "🔗 = respaldado por una cita literal de la fuente enlazada. 🧠 = conocimiento del modelo, sin cita: **revísalo**.", ""]
    for p, r in hechos:
        L += [f"## {p['name']} — {p.get('zona')} · {p.get('category')}", ""]
        if r.get("historia"):
            L += [f"**Historia** {_marca(r['historia'])} {r['historia']['texto']}", ""]
        for k, titulo in (("hacer", "Qué hacer"), ("no_hacer", "Qué NO hacer"), ("probar", "Qué probar"), ("secretos", "Secretos")):
            if r.get(k):
                L.append(f"**{titulo}**")
                L += [f"- {_marca(it)} {it['texto']}" + (f" ([fuente]({it['url']}))" if it.get("url") else "") for it in r[k]]
                L.append("")
        L += [f"_Fuentes leídas: {len(r['fuentes'])}_", ""]
    if fallidos:
        L += ["## Sin ficha profunda (evidencia o respuesta insuficiente)", ""] + [f"- {n}" for n in fallidos]
    return "\n".join(L) + "\n"


def _marca(it: dict) -> str:
    return "🔗" if it.get("origen") == "fuente" else "🧠"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Investigación profunda de lugares curados (historia, hacer, no hacer, probar, secretos).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--piloto", type=int, help="N lugares mejor calificados, mezclando categorías")
    g.add_argument("--solo", help="Nombres separados por coma")
    g.add_argument("--todos", action="store_true", help="Todos los de Bogotá que aún no tienen capa profunda")
    ap.add_argument("--max", type=int, default=50, help="Tope de lugares con --todos")
    ap.add_argument("--forzar", action="store_true", help="Reinvestiga aunque ya tengan capa")
    ap.add_argument("--rebuscar", action="store_true", help="Vuelve a buscar evidencia de los que tenían menos de 3 páginas")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cargar_env()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app import seed_data
    bogota = [p for p in seed_data.all_seeds() if p.get("city_display") == "Bogotá"]
    capa = json.load(open(RUTA, encoding="utf-8")) if os.path.exists(RUTA) else {}
    if a.solo:
        pedidos = {s.strip() for s in a.solo.split(",") if s.strip()}
        objetivo = [p for p in bogota if p["name"] in pedidos]
        faltan = pedidos - {p["name"] for p in objetivo}
        if faltan:
            print(f"No están en la curación: {', '.join(sorted(faltan))}")
    elif a.piloto:
        objetivo = piloto(bogota, a.piloto)
    else:
        objetivo = [p for p in sorted(bogota, key=lambda q: -(q.get("rating") or 0)) if p.get("category") in CATEGORIAS]
    if not a.forzar:
        objetivo = [p for p in objetivo if p["name"] not in capa]
    if a.todos:
        objetivo = objetivo[: a.max]
    print(f"{len(objetivo)} lugares por investigar.")
    if a.dry_run:
        for p in objetivo:
            print(f"  - {p['name']} ({p['category']}, {p['zona']}, {p.get('rating')})")
        return

    tkey, gkey = os.environ.get("TAVILY_API_KEY", ""), os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not tkey:
        sys.exit("Falta TAVILY_API_KEY en .env.")
    from app import llm
    if not llm._available():
        sys.exit("Falta una key de LLM en .env (p. ej. DEEPSEEK_API_KEY).")

    os.makedirs("reportes", exist_ok=True)
    evid_ruta = f"reportes/evidencia_profunda_{datetime.date.today().isoformat()}.json"
    evidencias = json.load(open(evid_ruta, encoding="utf-8")) if os.path.exists(evid_ruta) else {}
    hechos, fallidos = [], []
    with httpx.Client(timeout=45) as cl:
        for i, p in enumerate(objetivo, 1):
            try:
                ev = evidencias.get(p["name"])
                if not ev or (a.rebuscar and len(ev["paginas"]) < MIN_PAGINAS):
                    ev = reunir(cl, tkey, gkey, p)
                evidencias[p["name"]] = ev
                if not ev["paginas"] and not ev["web_texto"]:
                    fallidos.append(p["name"])
                    print(f"[{i}/{len(objetivo)}] --   {p['name'][:40]} (sin fuentes en la web)")
                    continue   # sin evidencia, el modelo solo rellena con generalidades: no entra
                r = validar(llamar_modelo(p, ev), ev)
            except httpx.HTTPStatusError as ex:
                if ex.response.status_code in (401, 403):
                    sys.exit(f"Tavily rechazó la key ({ex.response.status_code}).")
                if ex.response.status_code in (429, 432, 433):
                    print("Tavily sin cupo; me detengo aquí.")
                    break
                print(f"[{i}/{len(objetivo)}] error {ex.response.status_code} en {p['name'][:40]}")
                continue
            except Exception as ex:
                print(f"[{i}/{len(objetivo)}] error en {p['name'][:40]}: {ex}")
                continue
            if r:
                capa[p["name"]] = r
                hechos.append((p, r))
                n_f = sum(1 for k in LISTAS for it in r.get(k, []) if it["origen"] == "fuente")
                n_m = sum(1 for k in LISTAS for it in r.get(k, []) if it["origen"] == "modelo")
                print(f"[{i}/{len(objetivo)}] ok   {p['name'][:40]} · {len(ev['paginas'])} págs{' + web' if ev['web_texto'] else ''}"
                      f" · {len(r.get('secretos', []))} secretos · 🔗{n_f} 🧠{n_m}")
            else:
                fallidos.append(p["name"])
                print(f"[{i}/{len(objetivo)}] --   {p['name'][:40]} (respuesta insuficiente)")
            if i % 5 == 0:
                json.dump(capa, open(RUTA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                json.dump(evidencias, open(evid_ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(capa, open(RUTA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(evidencias, open(evid_ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = f"reportes/profundo_{datetime.date.today().isoformat()}.md"
    with open(md, "w", encoding="utf-8") as f:
        f.write(resumen_md(hechos, fallidos))
    print(f"\nListo: {len(hechos)} fichas profundas, {len(fallidos)} sin ficha. Revisión: {md}")


if __name__ == "__main__":
    main()
