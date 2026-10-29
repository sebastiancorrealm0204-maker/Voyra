"""Capa para viajeros EXTRANJEROS: «si vas a este lugar, pide / pregunta por X, y se dice así», más lo práctico que un extranjero necesita.

Agrega tres cosas a la ficha `local` de un lugar (todas con la misma regla anti-alucinación del producto: mejor vacío que falso):
  · preguntar  : algo ESPECÍFICO que pedir o preguntar al llegar. Lo redacta el modelo, pero con CITA LITERAL de la evidencia, verificada por código.
  · frase      : cómo decirlo en español. Solo se acepta si repite una palabra distintiva del `pedir`/`preguntar` ya respaldado.
  · extranjero : tarjeta / reserva / opciones vegetarianas / teléfono / reseñas en inglés. Sale SOLO de hechos estructurados de Google: el modelo no escribe nada.

Dos tipos de lugar (los dos van a un REPORTE para que lo revises; nada se escribe en la curación hasta que lo apruebes):
  · EXISTENTES  (ya curados, con `pedir`): el resultado va a app/local_extranjeros.json con --aplicar (no pisa nada: solo suma campos).
  · CANDIDATOS  (descubiertos, aún sin aprobar): la ficha completa sale en el reporte; se aprueban con aprobar_candidatos.py como siempre.

    # Keys en .env: GOOGLE_MAPS_API_KEY, TAVILY_API_KEY y una de LLM (p. ej. DEEPSEEK_API_KEY)
    python enriquecer_extranjeros.py --auto-existentes 10 --auto-candidatos 10 --dry-run   # qué haría y cuántas llamadas gasta
    python enriquecer_extranjeros.py --auto-existentes 10 --auto-candidatos 10             # piloto: genera reportes/extranjeros_<fecha>.md
    python enriquecer_extranjeros.py --existentes "Andrés Carne de Res,Prudencia"          # lugares concretos
    python enriquecer_extranjeros.py --aplicar reportes/extranjeros_<fecha>.json           # tras revisar: escribe los EXISTENTES aprobados

Costo por lugar: 1 llamada de Google Places (Details, con campos de nivel más caro: confírmalo en tu consola), 1 de Tavily y 1 del LLM.
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time

import httpx

import redactar_local as W
import reunir_local as R
from verificar_lugares import cargar_env

CATEGORIAS = ("restaurante", "cafe", "bar")
OVERLAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "local_extranjeros.json")

SISTEMA_EXT = (
    "Completas la ficha de un lugar de Companion, una guía local de Bogotá para viajeros EXTRANJEROS, SOLO con la evidencia que te doy "
    "(resumen de Google y fragmentos de guías o reseñas). Ya existe una ficha curada; no la repitas. Devuelve SOLO JSON:\n"
    '{"preguntar": {"texto": "...", "cita": "frase TEXTUAL de la evidencia"} | null, '
    '"frase": {"texto": "frase corta en español", "sobre": "pedir|preguntar"} | null}\n'
    "- preguntar: algo ESPECÍFICO y distinto de lo que ya dice `pedir` en la ficha: un plato fuera de carta, una mesa o sección, una sede, "
    "algo por lo que preguntar al llegar. Un turista lo usa tal cual: «pregunta por X». Una frase.\n"
    "- frase: UNA oración natural en español (máx. 14 palabras) que el turista diría para pedir lo de `pedir` (de la ficha) o lo de `preguntar` "
    "(el que escribiste). Usa el nombre del plato o de la cosa tal cual. Sin precios.\n"
    "REGLAS ESTRICTAS: `preguntar` lleva una `cita` COPIADA LITERALMENTE de la evidencia (mínimo 18 caracteres); si no hay, devuelve null. "
    "No inventes platos, precios, horarios ni historia; no uses lo que sepas del lugar por fuera de la evidencia. Sin hype ni emojis."
)


# ─────────────────────────── lógica pura (con tests) ───────────────────────────
def validar_existente(raw: str, e: dict) -> dict:
    """JSON del modelo → {'preguntar'?, 'frase'?} con solo lo respaldado. `e` trae `local_actual` (la ficha ya curada) y la evidencia."""
    try:
        data = json.loads(raw.replace("```json", "").replace("```", "").strip())
    except (ValueError, AttributeError):
        return {}
    if not isinstance(data, dict):
        return {}
    actual = e.get("local_actual") or {}
    out: dict = {}
    p = data.get("preguntar")
    if isinstance(p, dict) and (p.get("texto") or "").strip() and W.cita_valida(p.get("cita", ""), W._n(W.corpus(e))):
        txt = W.limpiar({"preguntar": " ".join(p["texto"].split())[:240]}).get("preguntar")
        tv = W._tokens(txt or "")
        # que no repita lo que la ficha ya dice (en `pedir` o `ver`)
        repite = any(tv and len(tv & W._tokens(actual.get(k) or "")) / min(len(tv), max(1, len(W._tokens(actual.get(k) or "")))) >= 0.5
                     for k in ("pedir", "ver", "clave"))
        if txt and not repite:
            out["preguntar"] = txt
    base = {**{k: actual.get(k) for k in ("pedir",) if actual.get(k)}, **({"preguntar": out["preguntar"]} if "preguntar" in out else {})}
    frase = W.frase_valida(data.get("frase"), base)
    if frase:
        out["frase"] = frase
    return out


def _sin_sucursales(items: list[dict]) -> list[dict]:
    """Una por marca: «Izumi Zona T» e «Izumi Bogotá» son el mismo lugar para la ficha (se conserva la primera de la lista ya ordenada)."""
    vistos, out = set(), []
    for p in items:
        base = " ".join(W._n(p["name"]).split()[:2])
        if base not in vistos:
            vistos.add(base)
            out.append(p)
    return out


def elegir_existentes(seeds: list[dict], ya: set[str], n: int) -> list[dict]:
    """Los N lugares de comida ya curados que más valen para un extranjero y aún no tienen la capa: con `pedir`, ordenados por la calidad
    de la ficha curada (prestigio, experiencia, rating): las calificaciones de Google están saturadas y por sí solas no distinguen."""
    from app import estrategia
    cand = [p for p in seeds if p.get("city_display") == "Bogotá" and p.get("category") in CATEGORIAS
            and (p.get("local") or {}).get("pedir") and p["name"] not in ya and p.get("place_id")]
    cand.sort(key=lambda p: -estrategia._calidad(p))
    return _sin_sucursales(cand)[:n]


def _senal_turistas(c: dict) -> tuple:
    """Qué tan probable es que un extranjero llegue ahí: aparece en TripAdvisor (guía de viajeros), en cuántos sitios independientes se
    menciona y cuántas reseñas tiene. Se ordena de mayor a menor."""
    ev = c.get("_evidencia") or {}
    return (any("tripadvisor" in d for d in ev.get("dominios") or []), ev.get("menciones") or 0, ev.get("resenas") or 0, c.get("rating") or 0)


def elegir_candidatos(candidatos: list[dict], conocidos_nombres: set[str], conocidos_ids: set[str], n: int) -> list[dict]:
    """Los N candidatos de comida descubiertos y aún no incorporados, los que más probablemente visite un extranjero."""
    cand = [c for c in candidatos if c.get("category") in CATEGORIAS and c.get("place_id")
            and W._n(c["name"]) not in conocidos_nombres and c["place_id"] not in conocidos_ids]
    cand.sort(key=_senal_turistas, reverse=True)
    return _sin_sucursales(cand)[:n]


def cargar_overlay() -> dict:
    try:
        with open(OVERLAY, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def aplicar_overlay(existentes: dict[str, dict], overlay: dict) -> tuple[dict, int]:
    """Suma al overlay los resultados (solo campos no vacíos; nunca borra lo que ya había). Devuelve (overlay, lugares tocados)."""
    tocados = 0
    for nombre, campos in existentes.items():
        nuevos = {k: v for k, v in campos.items() if k in ("preguntar", "frase", "extranjero") and v}
        if nuevos:
            overlay[nombre] = {**overlay.get(nombre, {}), **nuevos}
            tocados += 1
    return overlay, tocados


# ─────────────────────────── ejecución ───────────────────────────
def evidencia(cl: httpx.Client, p: dict, max_tavily_restantes: int) -> dict:
    h = R.hechos_google(cl, p["place_id"])
    h["practico"] = R.practico_de(h)
    h["extranjero_texto"] = R.extranjero_texto(h.get("extranjero") or {})
    frags = []
    if max_tavily_restantes > 0:
        try:
            frags = R.mejores_fragmentos(p["name"], R.textos_tavily(cl, p["name"], R.CONSULTAS["nuevo"]))
        except Exception as ex:
            print(f"   Tavily falló en {p['name']}: {ex}")
        time.sleep(0.3)
    return {"name": p["name"], "category": p["category"], "zona": p["zona"], "descripcion": p.get("descripcion", ""),
            "local_actual": p.get("local") or {}, "hechos": h, "fragmentos": frags}


def prompt_existente(e: dict) -> str:
    act = e.get("local_actual") or {}
    ficha = " | ".join(f"{k}: {act[k]}" for k in ("clave", "pedir", "ver", "momento") if act.get(k))
    return W.prompt_usuario(e) + f"\nFicha ya curada (no la repitas): {ficha}"


def md(existentes: list[dict], candidatos: list[dict]) -> str:
    L = [f"# Capa para extranjeros — piloto {datetime.date.today().isoformat()}", "",
         "Cada `preguntar` lleva cita literal de la fuente (verificada por código); `frase` repite una palabra del `pedir`/`preguntar`; "
         "`extranjero` sale solo de hechos de Google. Léelo igual antes de aprobar: tú conoces Bogotá.", "",
         "## Lugares ya curados (se aplican con --aplicar)", ""]
    for r in existentes:
        L += [f"### {r['name']} — {r['zona']} · {r['category']}", f"- ficha actual · pedir: {r['pedir_actual']}"]
        for k in ("preguntar", "frase", "extranjero"):
            L.append(f"  - **{k}**: {r.get(k) or '—'}")
        L.append("")
    L += ["## Candidatos nuevos (ficha completa; se aprueban con aprobar_candidatos.py)", ""]
    for r in candidatos:
        L += [f"### {r['name']} — {r['zona']} · {r['category']} · ★{r.get('rating')}"]
        if not r.get("local"):
            L += ["- sin ficha: evidencia insuficiente (no se recomienda aprobarlo todavía)", ""]
            continue
        L.append(f"- {r['descripcion']}")
        for k in W.CAMPOS + ("frase", "practico", "extranjero"):
            if r["local"].get(k):
                L.append(f"  - **{k}**: {r['local'][k]}")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Capa para extranjeros: preguntar / frase / extranjero (con citas verificadas).")
    ap.add_argument("--existentes", default="", help="Nombres de lugares ya curados, separados por comas")
    ap.add_argument("--auto-existentes", type=int, default=0, help="Los N mejores lugares de comida curados sin la capa")
    ap.add_argument("--candidatos", default="", help="Nombres de candidatos descubiertos, separados por comas")
    ap.add_argument("--auto-candidatos", type=int, default=0, help="Los N mejores candidatos de comida aún no incorporados")
    ap.add_argument("--max-tavily", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true", help="Solo muestra qué lugares y cuántas llamadas gastaría")
    ap.add_argument("--aplicar", default="", help="reportes/extranjeros_<fecha>.json: escribe los EXISTENTES en app/local_extranjeros.json")
    a = ap.parse_args()
    cargar_env()

    if a.aplicar:
        datos = json.load(open(a.aplicar, encoding="utf-8"))
        overlay, n = aplicar_overlay({r["name"]: r for r in datos["existentes"]}, cargar_overlay())
        overlay.setdefault("_nota", "Capa para extranjeros (preguntar/frase/extranjero): generada por enriquecer_extranjeros.py y revisada a mano.")
        json.dump(overlay, open(OVERLAY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"Listo: {n} lugares actualizados en {OVERLAY}. Revisa el diff antes de hacer commit.")
        return

    from app import seed_data
    seeds = seed_data.all_seeds()
    ya = {k for k in cargar_overlay() if not k.startswith("_")}
    nombres = [s.strip() for s in a.existentes.split(",") if s.strip()]
    por = {p["name"]: p for p in seeds if p.get("city_display") == "Bogotá"}
    if nombres:
        faltan = [n for n in nombres if n not in por]
        if faltan:
            sys.exit(f"No están en la curación: {faltan}")
    existentes = [por[n] for n in nombres] + elegir_existentes(seeds, ya | set(nombres), a.auto_existentes)

    cand_ruta = sorted(glob.glob("reportes/candidatos_bogota_*.json"))
    candidatos_all = json.load(open(cand_ruta[-1], encoding="utf-8")) if cand_ruta else []
    conocidos = [p for p in seeds if p.get("city_display") == "Bogotá"]
    pedidos = [s.strip() for s in a.candidatos.split(",") if s.strip()]
    candidatos = [c for c in candidatos_all if c["name"] in pedidos] + elegir_candidatos(
        [c for c in candidatos_all if c["name"] not in pedidos], {W._n(p["name"]) for p in conocidos},
        {p.get("place_id") for p in conocidos}, a.auto_candidatos)

    total = len(existentes) + len(candidatos)
    print(f"{len(existentes)} existentes + {len(candidatos)} candidatos = {total} lugares → {total} llamadas a Google Places (Details), "
          f"hasta {min(total, a.max_tavily)} a Tavily y {total} al LLM.")
    for p in existentes:
        print(f"  [existente] {p['name']} (★{p.get('rating')})")
    for p in candidatos:
        print(f"  [candidato] {p['name']} (★{p.get('rating')})")
    if a.dry_run or not total:
        return
    if not (R.GOOGLE_KEY and R.TAVILY_KEY):
        sys.exit("Faltan GOOGLE_MAPS_API_KEY y/o TAVILY_API_KEY (ponlas en .env).")
    from app import llm
    if not llm._available():
        sys.exit("Falta una key de LLM en .env (p. ej. DEEPSEEK_API_KEY).")

    R._cargar_cache()
    res_ex, res_ca, usadas = [], [], 0
    with httpx.Client(timeout=25) as cl:
        for i, p in enumerate(existentes, 1):
            e = evidencia(cl, p, a.max_tavily - usadas)
            usadas += 1
            try:
                r = validar_existente(llm._call(llm.EXTRACT_PROVIDER, SISTEMA_EXT, [{"role": "user", "content": prompt_existente(e)}], max_tokens=500), e)
            except Exception as ex:
                print(f"[existente {i}] error en {p['name']}: {ex}")
                r = {}
            ext = e["hechos"]["extranjero_texto"]
            res_ex.append({"name": p["name"], "zona": p["zona"], "category": p["category"],
                           "pedir_actual": (p.get("local") or {}).get("pedir", ""), **r, **({"extranjero": ext} if ext else {})})
            print(f"[existente {i}/{len(existentes)}] {p['name'][:40]}: {', '.join(k for k in ('preguntar', 'frase', 'extranjero') if res_ex[-1].get(k)) or 'nada respaldado'}")
        for i, c in enumerate(candidatos, 1):
            e = evidencia(cl, c, a.max_tavily - usadas)
            usadas += 1
            try:
                r = W.validar(W.llamar_modelo(e), e)
            except Exception as ex:
                print(f"[candidato {i}] error en {c['name']}: {ex}")
                r = None
            item = {"name": c["name"], "zona": c["zona"], "category": c["category"], "rating": c.get("rating"), "descripcion": "", "local": None}
            if r:
                W.aplicar(item, r, W.practico(e), W.extranjero(e))
            res_ca.append(item)
            print(f"[candidato {i}/{len(candidatos)}] {c['name'][:40]}: {'ficha ok' if r else 'sin ficha (evidencia insuficiente)'}")
    R._guardar_cache()
    os.makedirs("reportes", exist_ok=True)
    base = f"reportes/extranjeros_{datetime.date.today().isoformat()}"
    json.dump({"existentes": res_ex, "candidatos": res_ca}, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(base + ".md", "w", encoding="utf-8").write(md(res_ex, res_ca))
    print(f"\nListo. Revisa {base}.md. Para aplicar los existentes: python enriquecer_extranjeros.py --aplicar {base}.json")


if __name__ == "__main__":
    main()
