"""Hora local del destino — base para quiet hours y el scheduler proactivo.

El servidor en Railway corre en UTC. Si calculamos la hora con datetime.now()
"a secas", las quiet hours y los check-ins caen a la hora equivocada (5h de
diferencia con Bogotá). Aquí mapeamos la ciudad del viaje a su timezone IANA
y damos helpers que SIEMPRE razonan en hora del destino.

Sin dependencias externas: zoneinfo es stdlib desde Python 3.9.
"""
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

# Ciudad (normalizada) → timezone IANA. Ampliar = agregar una línea.
CITY_TZ = {
    "bogota": "America/Bogota",
    "cartagena": "America/Bogota",
    "medellin": "America/Bogota",
    "cali": "America/Bogota",
}

DEFAULT_TZ = "America/Bogota"  # razonable para el mercado LATAM inicial


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.strip().lower()


def tz_for_city(city: str) -> ZoneInfo:
    """Devuelve el ZoneInfo del destino. Cae a DEFAULT_TZ si la ciudad no está mapeada."""
    name = CITY_TZ.get(_norm(city), DEFAULT_TZ)
    return ZoneInfo(name)


def now_local(trip: dict) -> datetime:
    """Datetime actual en la hora del destino del viaje."""
    return datetime.now(tz_for_city(trip.get("ciudad", "")))


def hour_float(trip: dict) -> float:
    """Hora local del destino como float (ej. 22.5 = 22:30). Para quiet hours."""
    n = now_local(trip)
    return n.hour + n.minute / 60
