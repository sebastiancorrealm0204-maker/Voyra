"""Redacta la `descripcion` (y los `tags`) de los candidatos de un reporte, a partir de SUS datos reales.

La `descripcion` es lo que el Companion lee para recomendar un lugar, así que tiene que ser concreta y verdadera.
Para cada candidato el modelo recibe SOLO lo que sabemos (nombre, categoría, zona, dirección, rating, reseñas, y los
extractos y títulos de las fuentes que lo mencionan) y escribe 1-2 frases en el estilo de la curación de Companion. No
inventa: si los datos son escasos, escribe algo corto y neutro y lo marca como `poca_informacion` para que lo revises.

    python redactar_descripciones.py --archivo reportes/candidatos_cultura_bogota_2026-10-26.json --dry-run
    python redactar_descripciones.py --archivo reportes/candidatos_cultura_bogota_2026-10-26.json
    python redactar_descripciones.py --archivo <json> --estados recomendado,dudoso --forzar   # también reescribe las existentes
    python redactar_descripciones.py --archivo <json> --nombres "Pasaje Rivas,Humedal Santa María" --enriquecer --forzar
        # ENRIQUECER: para esos lugares lee el texto de las páginas que los describen (Tavily) y reescribe con más detalle

Escribe las descripciones en el mismo JSON (deja una copia .bak.json) y un `.md` de revisión al lado
(reportes/descripciones_<reporte>.md). Después se aprueba como siempre:
    python aprobar_candidatos.py --archivo <json> --aprobar "Nombre"
La redacción es de un modelo: léela antes de aprobar.
"""
import argparse
import json
import os
import shutil
import sys
import time
import unicodedata

from app import seed_data
from verificar_lugares import cargar_env

cargar_env()
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "")
SIN_TEXTO = ("tiktok.com", "instagram.com", "facebook.com", "youtube.com", "x.com", "twitter.com", "pinterest.com")

SISTEMA = (
    "Redactas las fichas de lugares de Companion, una guía local de Bogotá. Para cada lugar que te doy escribe una "
    "`descripcion` en español neutro y natural, de 1 a 2 frases (máximo ~240 caracteres), con este estilo:\n"
    "- «Una de las colecciones de orfebrería prehispánica más importantes del mundo. Imprescindible; entrada económica y "
    "gratis los domingos. Dedícale 1.5–2 horas.»\n"
    "- «Bosque de niebla a ~1 h al suroccidente: senderos, cabañas en los árboles y aire de páramo. Naturaleza de verdad "
    "sin alejarse mucho; ideal para un día de caminata.»\n"
    "- «El jardín botánico de la ciudad: lago, palmetum, plantas de páramo y bosque andino. Plan tranquilo de medio día, "
    "ideal con buen clima.»\n"
    "REGLAS:\n"
    "• Si hay «Texto de la página», úsalo para dar el gancho: qué tiene de particular el lugar, qué se ve o se hace, y un consejo "
    "práctico SI el texto lo dice. Ese texto manda sobre tu conocimiento previo.\n"
    "• Usa SOLO los datos que te doy. No inventes horarios, precios, tiempos de viaje, colecciones ni historia que no estén "
    "en los datos. Si no sabes algo, no lo digas.\n"
    "• Primero qué es el lugar; luego, solo si los datos lo respaldan, por qué ir o un consejo práctico.\n"
    "• Sin hype («increíble», «imperdible», «el mejor»), sin emojis, sin signos de exclamación, sin hablarle al lector de usted.\n"
    "• Si los datos son escasos (solo nombre y categoría), escribe UNA frase corta y neutra y pon `poca_informacion` en true.\n"
    "• `tags`: de 1 a 3, SOLO de esta lista y solo si encajan con claridad: {tags}.\n"
    "Responde SOLO con JSON: {{\"lugares\": [{{\"nombre\": \"...\", \"descripcion\": \"...\", \"tags\": [\"...\"], "
    "\"poca_informacion\": false}}]}}"
)


def contexto(e: dict) -> str:
    ev = e.get("_evidencia", {})
    L = [f"Nombre: {e['name']}", f"Categoría: {e['category']}", f"Zona: {e['zona']}", f"Dirección: {e.get('dir', '')}",
         f"Google: rating {e.get('rating')} con {ev.get('resenas')} reseñas"]
    for f in (ev.get("fuentes") or [])[:3]:
        L.append(f"Fuente ({f.get('dominio')}): {f.get('titulo')}")
    for x in (ev.get("extractos") or [])[:3]:
        if x.strip():
            L.append(f"Extracto: {x.strip()[:320]}")
    for d in (ev.get("detalles") or [])[:2]:
        L.append(f"Dato de redes: {d}")
    for t in (ev.get("texto_fuentes") or [])[:3]:
        L.append(f"Texto de la página ({t.get('dominio')}): {t.get('texto', '')[:1800]}")
    return "\n".join(L)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def texto_enriquecido(resultados: list[dict], nombre: str, zona: str = "") -> list[dict]:
    """De los resultados de Tavily (con el texto completo de cada página), los que de verdad hablan de este lugar:
    nombran el lugar y Bogotá (o su zona), y no son redes sociales. [{dominio, url, texto}] (máx. 3, ~2000 caracteres)."""
    clave = _norm(nombre)
    out, vistos = [], set()
    for r in resultados:
        url = r.get("url", "")
        dom = url.split("/")[2].removeprefix("www.") if url.count("/") >= 2 else ""
        texto = " ".join((r.get("raw_content") or r.get("content") or "").split())
        t = _norm(texto + " " + (r.get("title") or ""))
        if not texto or dom in vistos or any(dom == d or dom.endswith("." + d) for d in SIN_TEXTO):
            continue
        if clave not in t or not ("bogot" in t or (zona and _norm(zona) in t)):
            continue
        vistos.add(dom)
        out.append({"dominio": dom, "url": url, "texto": texto[:2000]})
    return out[:3]


def enriquecer(e: dict, cl, alias: str | None = None) -> int:
    """Trae el texto de las páginas que describen el lugar y lo guarda en la evidencia. Devuelve cuántas páginas útiles."""
    r = cl.post("https://api.tavily.com/search", json={
        "api_key": TAVILY_KEY, "query": f"{alias or e['name']} {e['zona']} Bogotá", "search_depth": "basic",
        "max_results": 6, "include_raw_content": True, "include_answer": False})
    r.raise_for_status()
    textos = texto_enriquecido(r.json().get("results", []), alias or e["name"], e["zona"])
    e["_evidencia"]["texto_fuentes"] = textos
    return len(textos)


def prompt_usuario(lote: list[dict]) -> str:
    return "\n\n".join(f"### Lugar {i + 1}\n{contexto(e)}" for i, e in enumerate(lote))


def parsear(raw: str, lote: list[dict]) -> dict[str, dict]:
    """JSON del modelo → {nombre: {descripcion, tags, poca_informacion}} solo para los lugares del lote. Tolerante."""
    try:
        data = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except (ValueError, AttributeError):
        return {}
    nombres = {e["name"] for e in lote}
    out = {}
    for p in (data.get("lugares") if isinstance(data, dict) else None) or []:
        if not isinstance(p, dict) or p.get("nombre") not in nombres:
            continue
        desc = " ".join(str(p.get("descripcion") or "").split())
        if len(desc) < 20:
            continue
        out[p["nombre"]] = {
            "descripcion": desc[:400],
            "tags": [t for t in dict.fromkeys(p.get("tags") or []) if t in seed_data.TAG_VOCAB][:3],
            "poca_informacion": bool(p.get("poca_informacion")),
        }
    return out


def aplicar(e: dict, r: dict) -> None:
    e["descripcion"] = r["descripcion"]
    e["tags"] = list(dict.fromkeys((e.get("tags") or []) + r["tags"]))
    e["_evidencia"]["descripcion_ia"] = True
    e["_evidencia"]["poca_informacion"] = r["poca_informacion"]


def resumen_md(cands: list[dict], titulo: str) -> str:
    L = [f"# Descripciones redactadas — {titulo}", "",
         "Las escribió un modelo con los datos de cada lugar. **Léelas y corrígelas** en el JSON antes de aprobar "
         "(`python aprobar_candidatos.py --archivo <json> --aprobar \"Nombre\"`). Las marcadas «poca información» son las más débiles.", ""]
    for e in cands:
        ev = e["_evidencia"]
        marca = " · poca información" if ev.get("poca_informacion") else ""
        L += [f"### {e['name']} — {e['zona']} · {e['category']}{marca}", f"- {e['descripcion']}",
              f"- Tags: {', '.join(e.get('tags') or []) or '—'} · Google {e.get('rating')} ({ev.get('resenas')} reseñas)"]
        for f in (ev.get("fuentes") or [])[:2]:
            L.append(f"  - [{f.get('titulo') or f.get('dominio')}]({f.get('url')})")
        L.append("")
    return "\n".join(L) + "\n"


def llamar_modelo(lote: list[dict]) -> str:
    from app import llm
    sistema = SISTEMA.format(tags=", ".join(sorted(seed_data.TAG_VOCAB)))
    return llm._call(llm.EXTRACT_PROVIDER, sistema, [{"role": "user", "content": prompt_usuario(lote)}], max_tokens=1600)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Redacta descripciones y tags de los candidatos de un reporte.")
    ap.add_argument("--archivo", required=True, help="JSON del reporte (candidatos_*.json / joyas_*.json)")
    ap.add_argument("--estados", default="recomendado", help="Estados a redactar, separados por comas")
    ap.add_argument("--nombres", default="", help="Solo estos lugares (por el inicio del nombre), separados por comas")
    ap.add_argument("--alias", action="append", default=[], metavar="NOMBRE=BUSQUEDA",
                    help="Nombre con el que las páginas suelen llamar al lugar (p. ej. «Sendero a Monserrate=Monserrate»)")
    ap.add_argument("--enriquecer", action="store_true", help="Lee el texto de las páginas que describen cada lugar (Tavily)")
    ap.add_argument("--forzar", action="store_true", help="Reescribe también las descripciones que ya existen")
    ap.add_argument("--lote", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cands = json.load(open(a.archivo, encoding="utf-8"))
    estados = {s.strip() for s in a.estados.split(",")}
    nombres = [_norm(n) for n in a.nombres.split(",") if n.strip()]
    pend = [e for e in cands if e["_evidencia"]["estado"] in estados and (a.forzar or not (e.get("descripcion") or "").strip())
            and (not nombres or any(_norm(e["name"]).startswith(n) for n in nombres))]
    print(f"{len(pend)} candidatos por redactar (de {len(cands)}), en lotes de {a.lote}.")
    if a.dry_run or not pend:
        for e in pend[:5]:
            print("\n" + contexto(e))
        return
    from app import llm
    if not llm._available():
        sys.exit("Falta una key de LLM en .env (p. ej. DEEPSEEK_API_KEY).")

    shutil.copyfile(a.archivo, a.archivo.replace(".json", ".bak.json"))
    alias = {_norm(k): v.strip() for k, _, v in (x.partition("=") for x in a.alias) if v.strip()}
    if a.enriquecer:
        import httpx
        if not TAVILY_KEY:
            sys.exit("Falta TAVILY_API_KEY en .env para --enriquecer.")
        with httpx.Client(timeout=40) as cl:
            for e in pend:
                try:
                    n = enriquecer(e, cl, alias=alias.get(_norm(e["name"])))
                    print(f"   páginas útiles: {n}  {e['name'][:40]}")
                except Exception as ex:
                    print(f"   no se pudo enriquecer {e['name'][:40]}: {ex}")
                time.sleep(0.4)
        json.dump(cands, open(a.archivo, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    hechos = 0
    for i in range(0, len(pend), a.lote):
        lote = pend[i:i + a.lote]
        try:
            res = parsear(llamar_modelo(lote), lote)
        except Exception as ex:
            print(f"   el modelo falló en el lote {i // a.lote + 1}: {ex}")
            continue
        for e in lote:
            if e["name"] in res:
                aplicar(e, res[e["name"]])
                hechos += 1
                print(f"   ok  {e['name'][:40]}")
            else:
                print(f"   --  {e['name'][:40]} (sin respuesta válida; se puede repetir)")
        json.dump(cands, open(a.archivo, "w", encoding="utf-8"), ensure_ascii=False, indent=1)   # guarda en cada lote

    base = os.path.basename(a.archivo).replace(".json", "")
    md = f"reportes/descripciones_{base}.md"
    redactados = [e for e in cands if e["_evidencia"].get("descripcion_ia")]
    with open(md, "w", encoding="utf-8") as f:
        f.write(resumen_md(redactados, base))
    print(f"\nListo: {hechos} descripciones. Revisión: {md}")


if __name__ == "__main__":
    main()
