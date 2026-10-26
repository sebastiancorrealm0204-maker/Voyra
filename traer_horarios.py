"""Descarga los horarios de apertura (Google Places) de todos los lugares curados de Bogotá a app/horarios_bogota.json.

    python traer_horarios.py --dry-run          # cuántos faltan y cuánto cuesta
    python traer_horarios.py                    # baja los que faltan (guarda cada 20)
    python traer_horarios.py --refrescar        # vuelve a bajarlos todos (los horarios cambian)

Lo lee app/horarios.py para marcar cada lugar como ABIERTO/CERRADO a la hora que pregunta el usuario. Usa Place Details
(~0.017 USD por lugar, dentro del cupo gratuito mensual); un lugar sin horario en Google queda sin entrada (el Companion
no afirma nada de él). Vuelve a correrlo cada tanto y tras sumar lugares nuevos.
"""
import argparse
import json
import os
import sys
import time

from app import net, seed_data
from verificar_lugares import cargar_env

DETALLES = "https://places.googleapis.com/v1/places/{pid}"
MASK = "id,regularOpeningHours.periods,regularOpeningHours.weekdayDescriptions"
RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "horarios_bogota.json")


def lugares_bogota() -> dict[str, str]:
    """{place_id: nombre} de la curación de Bogotá (base + capa de aprobados)."""
    return {p["place_id"]: p["name"] for p in seed_data.all_seeds()
            if p.get("city_display") == "Bogotá" and p.get("place_id")}


def normalizar(resp: dict) -> dict | None:
    h = resp.get("regularOpeningHours") or {}
    per = h.get("periods") or []
    if not per:
        return None
    return {"periods": per, "desc": h.get("weekdayDescriptions") or []}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Descarga horarios de apertura de los lugares curados de Bogotá.")
    ap.add_argument("--refrescar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cargar_env()
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")

    try:
        actuales = json.load(open(RUTA, encoding="utf-8"))
    except (OSError, ValueError):
        actuales = {}
    todos = lugares_bogota()
    faltan = {pid: n for pid, n in todos.items() if a.refrescar or pid not in actuales}
    print(f"{len(todos)} lugares con place_id; {len(faltan)} por descargar (≈ {len(faltan) * 0.017:.1f} USD).")
    if a.dry_run or not faltan:
        return
    if not key:
        sys.exit("Falta GOOGLE_MAPS_API_KEY en .env.")
    sin = 0
    for i, (pid, nombre) in enumerate(faltan.items(), 1):
        try:
            r = net.request("GET", DETALLES.format(pid=pid), retries=2, timeout=12,
                            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": MASK})
            if r.status_code == 429:
                time.sleep(5)
                continue
            h = normalizar(r.json()) if r.status_code == 200 else None
        except Exception as e:
            print(f"[{i}/{len(faltan)}] error en {nombre[:36]}: {type(e).__name__}")
            continue
        if h:
            actuales[pid] = h
        else:
            sin += 1
        if i % 20 == 0:
            json.dump(actuales, open(RUTA, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"[{i}/{len(faltan)}] guardado ({len(actuales)} con horario)")
        time.sleep(0.15)
    json.dump(actuales, open(RUTA, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Listo: {len(actuales)} lugares con horario; {sin} sin horario en Google.")


if __name__ == "__main__":
    main()
