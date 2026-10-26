"""Amplía la evidencia de lugares que quedaron SIN ficha porque reunir_local.py trajo muy poco texto.

reunir_local.py toma solo fragmentos cortos de la búsqueda de Tavily. Para un lugar con poca presencia en guías eso deja
la ficha vacía. Aquí se vuelve a buscar pidiendo el TEXTO COMPLETO de cada página (`include_raw_content`) y, de cada una
que nombra el lugar y habla de Bogotá, se guarda la ventana de texto alrededor del nombre (la carta, la especialidad, la
historia). Se suma a los fragmentos existentes en el mismo JSON de evidencia, así que después se redacta igual:

    python ampliar_evidencia.py --evidencia reportes/local_ola1.json --nombres reportes/sin_ficha_ola1.json
    python redactar_local.py --evidencia reportes/local_ola1.json

No inventa nada: solo junta texto de páginas reales. La regla de citas literales de redactar_local.py sigue mandando.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata

import httpx

from descubrir_lugares import dominio, es_propio_o_red
from verificar_lugares import cargar_env

TAVILY_KEY = ""   # se lee en main(): cargar .env al importar contaminaba el entorno de los tests
TAVILY_URL = "https://api.tavily.com/search"
SIN_TEXTO = ("tiktok.com", "instagram.com", "facebook.com", "youtube.com", "x.com", "twitter.com", "pinterest.com",
             "rappi.com", "rappi.com.co", "ubereats.com", "domicilios.com")
VENTANA = 1400   # caracteres de contexto alrededor del nombre


def _n(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def ventana(texto: str, nombre: str, largo: int = VENTANA) -> str:
    """El pedazo de `texto` alrededor de la primera vez que aparece `nombre` (sin mayúsculas ni tildes). '' si no aparece."""
    plano = " ".join((texto or "").split())
    i = _n(plano).find(_n(nombre))
    if i < 0:
        return ""
    ini = max(0, i - largo // 4)
    return plano[ini:ini + largo]


def nombre_busqueda(nombre: str) -> str:
    """Nombre sin sufijos de slogan o sede: «Qun Bogotá | Best Restaurant in Bogotá» → «Qun»."""
    base = re.split(r"\s[|•–—]\s|\s-\s", nombre)[0]
    return re.sub(r"\(.*?\)", "", base).strip()


def fragmentos_amplios(resultados: list[dict], nombre: str, zona: str = "") -> list[dict]:
    """Páginas (no redes ni domicilios) que nombran el lugar y a Bogotá, con la ventana de texto alrededor del nombre."""
    out, vistos = [], set()
    for r in resultados:
        url = r.get("url", "")
        dom = dominio(url)
        if not dom or dom in vistos or any(dom == d or dom.endswith("." + d) for d in SIN_TEXTO):
            continue
        texto = r.get("raw_content") or r.get("content") or ""
        v = ventana(texto, nombre)
        t = _n(texto + " " + (r.get("title") or ""))
        if not v or not ("bogot" in t or (zona and _n(zona) in t)):
            continue
        vistos.add(dom)
        out.append({"dominio": dom, "propio": es_propio_o_red(dom, nombre), "url": url, "pistas": 0, "texto": v})
    return out[:4]


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Amplía la evidencia (texto completo de páginas) de lugares con poca información.")
    ap.add_argument("--evidencia", required=True, help="JSON de evidencia de reunir_local.py (se actualiza en el lugar)")
    ap.add_argument("--nombres", required=True, help="JSON con la lista de nombres a ampliar")
    ap.add_argument("--max-tavily", type=int, default=60)
    a = ap.parse_args()
    global TAVILY_KEY
    cargar_env()
    TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
    if not TAVILY_KEY:
        sys.exit("Falta TAVILY_API_KEY en .env.")

    evid = json.load(open(a.evidencia, encoding="utf-8"))
    pedir = set(json.load(open(a.nombres, encoding="utf-8")))
    objetivo = [e for e in evid if e["name"] in pedir][: a.max_tavily]
    print(f"{len(objetivo)} lugares por ampliar.")
    with httpx.Client(timeout=45) as cl:
        for e in objetivo:
            q = f'"{nombre_busqueda(e["name"])}" {e["zona"]} Bogotá'
            try:
                r = cl.post(TAVILY_URL, json={"api_key": TAVILY_KEY, "query": q, "search_depth": "basic", "max_results": 8,
                                              "include_raw_content": True, "include_answer": False})
                r.raise_for_status()
                nuevos = fragmentos_amplios(r.json().get("results", []), nombre_busqueda(e["name"]), e["zona"])
            except httpx.HTTPStatusError as ex:
                if ex.response.status_code in (401, 403):
                    sys.exit(f"Tavily rechazó la key ({ex.response.status_code}).")
                if ex.response.status_code in (429, 432, 433):
                    print("Tavily sin cupo; me detengo aquí.")
                    break
                print(f"   error {ex.response.status_code} en {e['name'][:40]}")
                continue
            except Exception as ex:
                print(f"   error en {e['name'][:40]}: {ex}")
                continue
            urls = {f["url"] for f in e["fragmentos"]}
            e["fragmentos"] += [f for f in nuevos if f["url"] not in urls]
            print(f"   +{len(nuevos)} páginas  {e['name'][:44]}")
            time.sleep(0.4)
    json.dump(evid, open(a.evidencia, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Listo.")


if __name__ == "__main__":
    main()
