"""Horarios de apertura de los lugares curados: ¿está abierto a esta hora? ¿hasta cuándo?

Los horarios (de Google Places, `regularOpeningHours`) se descargan una vez con `traer_horarios.py` a
`app/horarios_bogota.json` ({place_id: {"periods": [...], "desc": [...]}}) y se leen aquí, sin llamar a Google en cada
mensaje. Con esto el Companion deja de recomendar a las 21:30 un restaurante que cierra a las 21:00: cada lugar llega al
prompt marcado como ABIERTO (hasta HH:MM) o CERRADO a la hora que pregunta el usuario.

Formato de Google: `periods` = [{"open": {"day": 0-6 (0 = domingo), "hour", "minute"}, "close": {...}}]. Un lugar abierto 24 h
trae un solo periodo con open day 0 hour 0 y sin `close`. Un periodo puede cruzar la medianoche (cierra al día siguiente).
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta

from . import plans as plans_mod, reloj

log = logging.getLogger("voyra.horarios")

RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "horarios_bogota.json")
_datos: dict | None = None


def datos() -> dict:
    global _datos
    if _datos is None:
        try:
            with open(RUTA, encoding="utf-8") as f:
                _datos = json.load(f)
        except (OSError, ValueError):
            _datos = {}
    return _datos


def _min_semana(dia: int, hora: int, minuto: int) -> int:
    return dia * 1440 + hora * 60 + minuto


def estado(place_id: str | None, cuando: datetime) -> dict | None:
    """{'abierto': bool, 'cierra': 'HH:MM'|None, 'abre': 'HH:MM'|None, '24h': bool} para `cuando` (hora local), o None si no
    conocemos el horario de ese lugar (en ese caso NO se afirma nada: ni abierto ni cerrado)."""
    h = datos().get(place_id or "")
    if not h or not h.get("periods"):
        return None
    periodos = h["periods"]
    if len(periodos) == 1 and not periodos[0].get("close") and periodos[0]["open"].get("hour", 0) == 0:
        return {"abierto": True, "cierra": None, "abre": None, "24h": True}
    ahora = _min_semana((cuando.weekday() + 1) % 7, cuando.hour, cuando.minute)   # Google: 0 = domingo
    sig_apertura = None
    for p in periodos:
        o, c = p["open"], p.get("close")
        if not c:
            continue
        ini = _min_semana(o["day"], o.get("hour", 0), o.get("minute", 0))
        fin = _min_semana(c["day"], c.get("hour", 0), c.get("minute", 0))
        if fin <= ini:
            fin += 7 * 1440                      # cruza el domingo→lunes
        for corr in (0, -7 * 1440, 7 * 1440):    # el periodo puede empezar la semana anterior (cruce de medianoche)
            a, b = ini + corr, fin + corr
            if a <= ahora < b:
                return {"abierto": True, "cierra": f"{c.get('hour', 0):02d}:{c.get('minute', 0):02d}", "abre": None, "24h": False}
        delta = (ini - ahora) % (7 * 1440)
        if sig_apertura is None or delta < sig_apertura[0]:
            sig_apertura = (delta, f"{o.get('hour', 0):02d}:{o.get('minute', 0):02d}")
    return {"abierto": False, "cierra": None, "abre": sig_apertura[1] if sig_apertura else None, "24h": False}


def etiqueta(place_id: str | None, cuando: datetime) -> str:
    """Marca corta para el prompt: « [ABIERTO hasta 22:00]», « [CERRADO a esa hora: NO lo ofrezcas para ese momento; abre 12:00]» o '' si no se sabe."""
    e = estado(place_id, cuando)
    if not e:
        return ""
    if e["abierto"]:
        return " [ABIERTO 24 h]" if e["24h"] else f" [ABIERTO hasta {e['cierra']}]"
    return f" [CERRADO a esa hora: NO lo ofrezcas para ese momento{'; abre ' + e['abre'] if e['abre'] else ''}]"


def esta_cerrado(place_id: str | None, cuando: datetime) -> bool:
    e = estado(place_id, cuando)
    return bool(e) and not e["abierto"]


def referencia(trip: dict, mensaje: str, ahora: datetime) -> tuple[datetime, bool]:
    """La hora a la que el usuario quiere el plan: la que dice («a las 9 pm», «mañana a las 8») o, si no dice, ahora.
    Devuelve (momento, ¿lo dijo explícitamente?)."""
    hora = reloj.leer_hora_cita(mensaje)
    fecha = plans_mod.resolver_fecha_relativa(mensaje, ahora)
    t = (mensaje or "").lower()
    # Sin hora explícita, la comida que menciona fija la hora típica (para saber qué está abierto cuando ese plan ocurra).
    if not hora:
        for patron, h in ((r"esta noche|\bcenar\b|\bcena\b|\bcenamos\b", "20:30"), (r"\balmuerzo\b|\balmorzar\b|\balmorzamos\b", "13:00"),
                          (r"\bdesayuno\b|\bdesayunar\b", "08:30"), (r"\bbrunch\b", "11:00")):
            if re.search(patron, t):
                hora = h
                break
    if not hora and not fecha:
        return ahora, False
    hh, mm = (int(x) for x in (hora or f"{ahora.hour}:{ahora.minute}").split(":")[:2])
    dia = datetime.strptime(fecha, "%Y-%m-%d").date() if fecha else ahora.date()
    return datetime.combine(dia, datetime.min.time()).replace(hour=hh, minute=mm, tzinfo=ahora.tzinfo), True
