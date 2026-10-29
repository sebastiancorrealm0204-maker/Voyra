"""Ficha completa de un lugar curado: lo que se abre al tocar una notificación de «cómo llegar a X» o una tarjeta del chat.

Junta en una sola respuesta todo lo que sabemos del lugar: la descripción, el bloque LOCAL (clave, pedir, ver, momento, ojo,
dato, práctico), la capa PROFUNDA de investigar_lugares.py (historia, hacer, no hacer, probar, secretos), el horario de hoy y
una galería de fotos reales de Google Places. Solo lugares de NUESTRA curación: si el nombre no coincide con ninguno, devuelve
None y la app cae a «Cómo llegar» como antes.
"""
import logging

from . import db, horarios, timeutil, traducir, visual

log = logging.getLogger("companion.ficha")

N_FOTOS = 6
MIN_SCORE = 0.6
_CAMPOS_LOCAL = ("clave", "pedir", "preguntar", "frase", "ver", "momento", "ojo", "dato", "practico", "extranjero")
_LISTAS = ("hacer", "no_hacer", "probar", "secretos")


def buscar(nombre: str, ciudad: str) -> dict | None:
    """El lugar curado de `ciudad` que mejor coincide con `nombre`, o None si ninguno coincide con seguridad."""
    mejor, puntaje = None, 0.0
    consulta = db.place_tokens(nombre)
    for p in db.places_for_city(ciudad):
        s = db.place_match_score(nombre, p["name"])
        # Más estricto que el match del chat: las palabras compartidas deben cubrir al menos la mitad del nombre curado
        # («Restaurante Que No Existe» compartía solo «que» con «El Día Que Me Quieras» y abría la ficha equivocada).
        propio = db.place_tokens(p["name"])
        if propio and len(propio & consulta) / len(propio) < 0.5 and s < 1.0:
            continue
        if s > puntaje:
            mejor, puntaje = p, s
    return mejor if mejor and puntaje >= MIN_SCORE else None


def _texto(v) -> str:
    return " ".join(v.split()) if isinstance(v, str) else ""


def armar(p: dict, trip: dict) -> dict:
    local = p.get("local") or {}
    prof = local.get("profundo") if isinstance(local.get("profundo"), dict) else {}
    pid = visual.place_id_de(p)
    out = {
        "name": p["name"], "categoria": p.get("category", ""), "zona": p.get("zona", ""), "rating": p.get("rating"),
        "dir": p.get("dir") or "", "lat": p.get("lat"), "lng": p.get("lng"), "place_id": pid,
        "descripcion": _texto(p.get("descripcion")),
        "local": {k: _texto(local.get(k)) for k in _CAMPOS_LOCAL if _texto(local.get(k))},
        "historia": _texto((prof.get("historia") or {}).get("texto")) if isinstance(prof.get("historia"), dict) else "",
        **{k: [{"texto": _texto(it.get("texto")), "origen": it.get("origen"), "url": it.get("url")}
               for it in (prof.get(k) or []) if isinstance(it, dict) and _texto(it.get("texto"))] for k in _LISTAS},
        "horario_hoy": "",
        "fotos": [],
    }
    try:
        out["horario_hoy"] = horarios.desc_dia(pid, timeutil.now_local(trip))
    except Exception:
        log.warning("ficha_horario_fallo lugar=%s", p["name"], exc_info=True)
    out["fotos"] = visual.fotos(pid, N_FOTOS)
    idioma = (trip.get("idioma") or "es").lower()
    if idioma != "es":
        _traducir(out, idioma)
    return out


def _traducir(f: dict, idioma: str) -> None:
    """Traduce en un solo lote todos los textos de la ficha (los nombres propios los respeta el traductor)."""
    refs: list[tuple] = []
    for k in ("descripcion", "historia", "horario_hoy"):
        if f[k]:
            refs.append((f, k))
    for k in f["local"]:
        refs.append((f["local"], k))
    for k in _LISTAS:
        for it in f[k]:
            refs.append((it, "texto"))
    if not refs:
        return
    for (obj, k), t in zip(refs, traducir.traducir([obj[k] for obj, k in refs], idioma)):
        obj[k] = t


def ficha(trip: dict, nombre: str) -> dict | None:
    p = buscar(nombre, trip.get("ciudad", ""))
    return armar(p, trip) if p else None
