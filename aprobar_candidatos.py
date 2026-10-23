"""Aprueba candidatos de descubrir_lugares.py y los agrega a la curación de Bogotá.

Los aprobados se guardan en app/seed_candidatos.json, una CAPA que seed_data.all_seeds() suma a la
curación (seed_data.py no se toca; para revertir, borra la entrada o el archivo).

    python aprobar_candidatos.py --archivo reportes/candidatos_bogota_2026-10-22.json --listar
    python aprobar_candidatos.py --archivo reportes/candidatos_bogota_2026-10-22.json --aprobar "Nombre A" "Nombre B"
    python aprobar_candidatos.py --archivo ... --todos-recomendados --descripcion-auto
    python aprobar_candidatos.py --quitar "Nombre A"

Antes de aprobar hay que tener la `descripcion` redactada en el JSON del reporte (es lo que el
Companion usa para recomendar). `--descripcion-auto` genera una genérica y objetiva para salir del paso,
pero una descripción escrita con criterio vale mucho más. Nunca se aprueba un candidato con señal de cierre.
"""
import argparse
import json
import os
import sys

DESTINO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "seed_candidatos.json")
TIPO_LEGIBLE = {"restaurante": "Restaurante", "cafe": "Café", "bar": "Bar", "atraccion": "Lugar de interés",
                "parque": "Parque", "mercado": "Mercado"}


def descripcion_auto(e: dict) -> str:
    ev = e["_evidencia"]
    tipo = TIPO_LEGIBLE.get(e["category"], "Lugar")
    return (f"{tipo} de {e['zona']} muy bien valorado en Google (★{e.get('rating')}, {ev.get('resenas')} reseñas) "
            f"y mencionado en {ev.get('menciones', 0)} sitios de guías de la ciudad.")


def a_seed(e: dict, auto: bool = False) -> dict:
    """Candidato del reporte → entrada lista para la curación. Lanza ValueError si no se puede aprobar."""
    ev = e.get("_evidencia", {})
    if ev.get("estado") == "descartado_cierre":
        raise ValueError(f"{e['name']}: hay una señal de cierre en las fuentes; no se aprueba.")
    desc = (e.get("descripcion") or "").strip()
    if not desc:
        if not auto:
            raise ValueError(f"{e['name']}: falta redactar la `descripcion` (o usa --descripcion-auto).")
        desc = descripcion_auto(e)
    out = {k: v for k, v in e.items() if k != "_evidencia"}
    out["descripcion"] = desc
    out["confianza"] = "media_alta" if ev.get("menciones", 0) >= 3 else "media"
    out["_fuentes"] = ev.get("dominios", [])
    return out


def cargar_destino() -> list[dict]:
    try:
        with open(DESTINO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def guardar_destino(lista: list[dict]) -> None:
    with open(DESTINO, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)


def agregar(actuales: list[dict], nuevos: list[dict]) -> tuple[list[dict], int]:
    """Suma sin duplicar (por place_id o por nombre). Devuelve (lista, cuántos se agregaron)."""
    ids = {p.get("place_id") for p in actuales if p.get("place_id")}
    nombres = {p["name"] for p in actuales}
    n = 0
    for p in nuevos:
        if p["name"] in nombres or (p.get("place_id") and p["place_id"] in ids):
            continue
        actuales.append(p)
        nombres.add(p["name"])
        n += 1
    return actuales, n


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Aprueba candidatos y los suma a la curación (capa app/seed_candidatos.json).")
    ap.add_argument("--archivo", help="JSON de reportes/candidatos_bogota_<fecha>.json")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--aprobar", nargs="+", metavar="NOMBRE")
    ap.add_argument("--todos-recomendados", action="store_true")
    ap.add_argument("--descripcion-auto", action="store_true")
    ap.add_argument("--quitar", nargs="+", metavar="NOMBRE")
    a = ap.parse_args()

    if a.quitar:
        act = cargar_destino()
        quedan = [p for p in act if p["name"] not in a.quitar]
        guardar_destino(quedan)
        print(f"Quitados {len(act) - len(quedan)}; quedan {len(quedan)} en la capa.")
        return
    if not a.archivo:
        sys.exit("Falta --archivo (el JSON del reporte).")
    cand = json.load(open(a.archivo, encoding="utf-8"))
    if a.listar:
        for e in cand:
            ev = e["_evidencia"]
            print(f"{ev['estado'][:11]:<11} {e['name'][:40]:<40} {e['zona']:<15} ★{e.get('rating')} ({ev.get('resenas')}) "
                  f"{ev.get('menciones')} sitios {'· falta descripcion' if not e.get('descripcion') else ''}")
        return
    elegidos = [e for e in cand if (a.todos_recomendados and e["_evidencia"]["estado"] == "recomendado")
                or (a.aprobar and e["name"] in a.aprobar)]
    if a.aprobar:
        faltan = [n for n in a.aprobar if n not in {e["name"] for e in cand}]
        if faltan:
            sys.exit(f"No están en el reporte: {faltan}")
    if not elegidos:
        sys.exit("Nada que aprobar (usa --aprobar \"Nombre\" o --todos-recomendados).")
    nuevos, errores = [], []
    for e in elegidos:
        try:
            nuevos.append(a_seed(e, a.descripcion_auto))
        except ValueError as ex:
            errores.append(str(ex))
    if errores:
        print("No se aprobó nada de lo siguiente:\n  - " + "\n  - ".join(errores))
    lista, n = agregar(cargar_destino(), nuevos)
    guardar_destino(lista)
    print(f"Aprobados: {n} nuevos (la capa tiene {len(lista)}). Corre los tests y haz commit.")


if __name__ == "__main__":
    main()
