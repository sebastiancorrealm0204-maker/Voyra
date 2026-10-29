"""Redacta la `descripcion` y el bloque `local` de lugares aprobados a partir de la evidencia que reunió reunir_local.py.

Regla anti-alucinación del producto: mejor un campo vacío que uno falso. Por eso el modelo NO escribe libre: cada campo
(clave, pedir, ver, momento, ojo, dato) y la descripción deben traer una CITA TEXTUAL de la evidencia (resumen de Google
o fragmentos de guías/redes), y el código comprueba que esa cita aparece de verdad en el texto reunido. Si no aparece,
el campo se descarta. `practico` sale siempre de los hechos de Google (precio y horario), sin pasar por el modelo.
Un lugar sin `clave` respaldada no entra a la curación (se quita de la capa de aprobados).

    python reunir_local.py                       # 1) reúne la evidencia de los aprobados sin `local`
    python redactar_local.py --evidencia reportes/local_bogota_<fecha>.json --dry-run
    python redactar_local.py --evidencia reportes/local_bogota_<fecha>.json

Escribe en app/seed_candidatos.json y deja reportes/fichas_<fecha>.md para revisar. Léelo antes de hacer commit.
"""
import argparse
import datetime
import json
import os
import re
import sys
import unicodedata

from verificar_lugares import cargar_env

RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "seed_candidatos.json")
CAMPOS = ("clave", "pedir", "preguntar", "ver", "momento", "ojo", "dato")
MAX_FRASE_PALABRAS = 14
MIN_CITA = 18   # una cita de menos caracteres no prueba nada

SISTEMA = (
    "Redactas la ficha de un lugar de Companion, una guía local de Bogotá, SOLO con la evidencia que te doy (resumen de Google y "
    "fragmentos de guías o redes). El Companion usa la ficha para hablar como un local. Devuelve SOLO JSON:\n"
    '{"descripcion": "1-2 frases (~240 caracteres): qué es el lugar y por qué ir", "descripcion_cita": "frase TEXTUAL de la evidencia", '
    '"local": {"clave": {"texto": "...", "cita": "..."}, "pedir": {...}, "preguntar": {...}, "ver": {...}, "momento": {...}, "ojo": {...}, '
    '"dato": {...}}, "frase": {"texto": "frase corta en español", "sobre": "pedir|preguntar"}}\n'
    "Campos de `local` (todos opcionales salvo `clave`; omite el que no puedas respaldar):\n"
    "- clave: la frase de un local en 5 segundos: qué es y qué lo hace especial.\n"
    "- pedir: qué pedir, si la evidencia lo dice (platos, bebidas).\n"
    "- preguntar: algo ESPECÍFICO que el visitante debe preguntar o pedir al llegar (un plato fuera de carta, una mesa o sección, una sede, "
    "un tour o sala concreta), si la evidencia lo dice. Para un extranjero es lo que más vale: «pregunta por X».\n"
    "- frase (fuera de `local`): UNA oración natural y corta, en español, que un turista diría para pedir lo que dice `pedir` o `preguntar` "
    "(usa el nombre del plato o de la cosa tal cual está en ese campo; sin precios). `sobre` indica de cuál campo habla. Solo si ese campo existe.\n"
    "- ver: qué ver o probar en el lugar. - momento: mejor hora, fila, reserva. - ojo: una advertencia o trampa. - dato: un detalle curioso o práctico.\n"
    "REGLAS ESTRICTAS:\n"
    "• Cada campo lleva una `cita`: un fragmento COPIADO LITERALMENTE de la evidencia (mínimo 18 caracteres) que lo respalda. "
    "Si no hay cita literal, no escribas el campo.\n"
    "• No inventes platos, precios, horarios, fechas, chefs ni historia. No uses lo que sepas del lugar por fuera de la evidencia.\n"
    "• No incluyas horarios ni precios (eso va aparte). Sin hype («increíble», «el mejor»), sin emojis, sin signos de exclamación.\n"
    "• Español neutro, tono de amigo local, frases cortas. Cada campo, una frase.\n"
    "• Si la evidencia es muy escasa, escribe solo `clave` (y la descripción) con lo poco que sí dice, o devuelve {\"local\": {}}."
)


def _n(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def corpus(e: dict) -> str:
    h = e.get("hechos", {})
    partes = [h.get("resumen_google", "")] + [f.get("texto", "") for f in e.get("fragmentos", [])]
    return "\n".join(p for p in partes if p)


def prompt_usuario(e: dict) -> str:
    h = e.get("hechos", {})
    L = [f"Lugar: {e['name']} ({e['category']}, {e['zona']})", f"Google: {h.get('rating')} con {h.get('resenas')} reseñas"]
    if h.get("resumen_google"):
        L.append(f"Resumen de Google: {h['resumen_google']}")
    for f in e.get("fragmentos", [])[:6]:
        L.append(f"Fragmento ({f.get('dominio')}{', sitio propio' if f.get('propio') else ''}): {f.get('texto', '')}")
    return "\n".join(L)


# Filtros de calidad (lo que la cita no puede garantizar: que el campo sirva de verdad).
_PRECIO_HORA = re.compile(r"\$|\d{1,2}[:.]\d{2}|\b\d{2,3}\.\d{3}\b|\bcop\b|\busd\b|\bpesos\b", re.I)
_ADVERTENCIA = re.compile(r"cuidado|\bojo\b|evita|\bfila|\bcola\b|reserv|lleno|llena|ruido|espera|demora|tarda|solo efectivo|"
                          r"no aceptan|cobran|cobro|propina|parquead|caro|no tanto|no es para|no apto|cierra|agotad", re.I)
_META = re.compile(r"evidencia|no menciona|no se menciona|no especifica|no hay (?:datos|informaci)|sin informaci", re.I)   # el modelo hablando de sí mismo
_VAGO = re.compile(r"seg[uú]n su perfil|siempre pasa|todo se ve|sabe muy bien|as[ií] que elegir|delicios|incre[ií]ble|espectacular|"
                   r"hace la diferencia|buena energ[ií]a|ambiente (?:chévere|chevere|agradable)|sin prisa", re.I)   # frases sin información
_NO_ES_ESPECIFICO = re.compile(r"\bsedes?\b|sucursal|\bhorarios?\b|direcci[oó]n|c[oó]mo llegar|d[oó]nde queda|tripadvisor|rappi|domicilio", re.I)
_TRIVIAL = re.compile(r"instagram|facebook|tiktok|cuenta de|rappi|domicilio|sitio web|p[aá]gina|publican", re.I)


def _tokens(t: str) -> set[str]:
    return {w for w in _n(t).split() if len(w) > 3}


def limpiar(local: dict) -> dict:
    """Quita lo que, aunque tenga cita, no aporta: precios y horarios (van aparte), un `ojo` que no es una advertencia,
    trivialidades (redes, domicilios), campos de menos de 4 palabras y los que repiten lo ya dicho en otro campo."""
    out = {}
    for k in CAMPOS:
        v = (local.get(k) or "").strip()
        if not v or len(v.split()) < 4 or _PRECIO_HORA.search(v) or _META.search(v):
            continue
        if k == "ojo" and not _ADVERTENCIA.search(v):
            continue
        if k in ("ver", "dato") and _TRIVIAL.search(v):
            continue
        if k == "preguntar" and _NO_ES_ESPECIFICO.search(v):
            continue  # «pregunta por las sedes / el horario / cómo llegar» no es algo ESPECÍFICO que pedir: es información general
        if k != "clave" and _VAGO.search(v):
            continue
        tv = _tokens(v)
        if k != "clave" and any(tv and len(tv & _tokens(o)) / min(len(tv), max(1, len(_tokens(o)))) >= 0.5 for o in out.values()):
            continue
        out[k] = v
    return out


def cita_valida(cita: str, evidencia_norm: str) -> bool:
    c = _n(cita)
    return len(c) >= MIN_CITA and c in evidencia_norm


def frase_valida(fr, local: dict) -> str:
    """La `frase` («cómo decirlo en español») no es un dato del lugar sino una ayuda: se acepta solo si habla de un campo ya respaldado
    (`pedir` o `preguntar`), repite al menos una palabra distintiva de ESE campo (el nombre del plato, no una frase genérica) y no trae
    precios ni cifras. Si algo falla, se descarta: mejor sin frase que con una que pida otra cosa."""
    if not isinstance(fr, dict) or fr.get("sobre") not in ("pedir", "preguntar") or not local.get(fr["sobre"]):
        return ""
    t = " ".join((fr.get("texto") or "").split())
    if not (3 <= len(t.split()) <= MAX_FRASE_PALABRAS) or _PRECIO_HORA.search(t) or _META.search(t):
        return ""
    if not (_tokens(t) & _tokens(local[fr["sobre"]])):
        return ""
    return t[:110]


def validar(raw: str, e: dict) -> dict | None:
    """JSON del modelo → {'descripcion', 'local'} con SOLO lo que tiene cita literal en la evidencia. None si no hay `clave`."""
    try:
        data = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    ev = _n(corpus(e))
    local = {}
    for k, v in (data.get("local") or {}).items():
        if k in CAMPOS and isinstance(v, dict) and (v.get("texto") or "").strip() and cita_valida(v.get("cita", ""), ev):
            local[k] = " ".join(v["texto"].split())[:240]
    local = limpiar(local)
    if not local.get("clave"):
        return None
    frase = frase_valida(data.get("frase"), local)
    if frase:
        local["frase"] = frase
    desc = " ".join((data.get("descripcion") or "").split())
    if not (len(desc) >= 20 and cita_valida(data.get("descripcion_cita", ""), ev)):
        desc = local["clave"]   # sin cita para la descripción: se usa la clave, que sí está respaldada
    return {"descripcion": desc[:400], "local": local}


def practico(e: dict) -> str:
    return (e.get("hechos") or {}).get("practico") or ""


def extranjero(e: dict) -> str:
    """Texto de la capa para extranjeros (pago, reserva, vegetariano, teléfono, reseñas en inglés): sale SOLO de hechos de Google."""
    return (e.get("hechos") or {}).get("extranjero_texto") or ""


def aplicar(p: dict, r: dict, prac: str, ext: str = "") -> None:
    p["descripcion"] = r["descripcion"]
    p["local"] = {**r["local"], "practico": prac or "sin horario ni precio en Google: confírmalo antes de ir.",
                  **({"extranjero": ext} if ext else {})}


def resumen_md(items: list[tuple[dict, dict]], descartados: list[str]) -> str:
    L = [f"# Fichas redactadas — {datetime.date.today().isoformat()}", "",
         "Cada campo se respalda con una cita literal de la fuente (verificada por código). Léelas igual antes de hacer commit.", ""]
    for p, r in items:
        L += [f"### {p['name']} — {p['zona']} · {p['category']}", f"- {p['descripcion']}"]
        for k in CAMPOS + ("frase",):
            if p["local"].get(k):
                L.append(f"  - **{k}**: {p['local'][k]}")
        L.append(f"  - **practico**: {p['local']['practico']}")
        if p["local"].get("extranjero"):
            L.append(f"  - **extranjero**: {p['local']['extranjero']}")
        L.append("")
    if descartados:
        L += ["## Sin ficha (evidencia insuficiente): se quitaron de la capa", ""] + [f"- {n}" for n in descartados]
    return "\n".join(L) + "\n"


def llamar_modelo(e: dict) -> str:
    from app import llm
    return llm._call(llm.EXTRACT_PROVIDER, SISTEMA, [{"role": "user", "content": prompt_usuario(e)}], max_tokens=900)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Redacta descripción y bloque local con citas verificadas.")
    ap.add_argument("--evidencia", required=True, help="reportes/local_bogota_<fecha>.json (de reunir_local.py)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-quitar", action="store_true", help="No quita de la capa los lugares sin evidencia suficiente")
    a = ap.parse_args()
    cargar_env()   # en main(): cargar .env al importar contaminaba el entorno de los tests

    evid = {e["name"]: e for e in json.load(open(a.evidencia, encoding="utf-8"))}
    capa = json.load(open(RUTA, encoding="utf-8"))
    pend = [p for p in capa if p["name"] in evid and not p.get("local")]
    print(f"{len(pend)} lugares por redactar (con evidencia de {len(evid)}).")
    if a.dry_run:
        for p in pend[:3]:
            print("\n" + prompt_usuario(evid[p["name"]])[:900])
        return
    from app import llm
    if not llm._available():
        sys.exit("Falta una key de LLM en .env (p. ej. DEEPSEEK_API_KEY).")

    hechos, descartados = [], []
    for i, p in enumerate(pend, 1):
        e = evid[p["name"]]
        try:
            r = validar(llamar_modelo(e), e)
        except Exception as ex:
            print(f"[{i}/{len(pend)}] error en {p['name'][:36]}: {ex}")
            continue
        if r:
            aplicar(p, r, practico(e), extranjero(e))
            hechos.append((p, r))
            print(f"[{i}/{len(pend)}] ok   {p['name'][:40]} ({', '.join(k for k in CAMPOS if k in r['local'])})")
        else:
            descartados.append(p["name"])
            print(f"[{i}/{len(pend)}] --   {p['name'][:40]} (sin `clave` respaldada)")
        if i % 10 == 0:
            json.dump(capa, open(RUTA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if descartados and not a.no_quitar:
        capa = [p for p in capa if p["name"] not in set(descartados)]
    json.dump(capa, open(RUTA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = f"reportes/fichas_{datetime.date.today().isoformat()}.md"
    with open(md, "w", encoding="utf-8") as f:
        f.write(resumen_md(hechos, descartados))
    print(f"\nListo: {len(hechos)} fichas, {len(descartados)} sin evidencia suficiente. Revisión: {md}")


if __name__ == "__main__":
    main()
