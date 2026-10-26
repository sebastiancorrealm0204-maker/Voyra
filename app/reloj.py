"""Sentido del tiempo del Companion: hora local, tráfico y logística REAL antes de recomendar una hora.

El modelo, solo, improvisa horas ("sal a las 7 de tu hotel") sin sumar lo que de verdad pasa: aterrizar, migración y
equipaje, el trancón del aeropuerto al hotel, alistarse y el trayecto al restaurante. Este módulo hace esas cuentas
con código (y con Google Routes con tráfico cuando hay key) y le entrega al modelo los NÚMEROS ya calculados para
que los use, no que los invente.

Entradas:   el mensaje del usuario ("llego a las 6 pm", "quiero cenar en X"), el viaje y la hora local.
Salida:     un bloque de texto para el system prompt (`bloque`) y helpers puros con tests.
"""
import logging
import re
import unicodedata
from datetime import datetime, timedelta

from . import geo, timeutil

log = logging.getLogger("voyra.reloj")

AEROPUERTO = (4.7016, -74.1469)       # El Dorado
CENTRO = (4.65, -74.07)

# Minutos desde que el avión aterriza hasta estar en la acera con el equipaje (migración + equipaje + aduana + salida).
SALIDA_AEROPUERTO_INTERNACIONAL = 75   # rango real 60–90
SALIDA_AEROPUERTO_NACIONAL = 35        # rango real 30–45
ALISTARSE_EN_HOTEL = 30                # dejar maletas, check-in rápido, bañarse/cambiarse
MARGEN_LLEGADA = 10                    # colchón para llegar a una cita a tiempo

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


# ─────────────────────────── lectura de horas del mensaje ───────────────────────────
_HORA = r"(\d{1,2})(?:[:.h](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|de la manana|de la tarde|de la noche|de la madrugada|am|pm)?"
_LLEGADA = re.compile(
    r"\b(?:llego|llegamos|llegare|aterrizo|aterrizamos|aterrizare|aterriza|llega|mi llegada|la llegada)\b[^.?!\d]{0,45}?"
    r"(?:a la[s]?|sobre las|como a las|a eso de las|despues de las|a las)\s*" + _HORA)
_A_LAS = re.compile(r"\b(?:a la[s]?|sobre las|como a las|a eso de las)\s*" + _HORA)
_NACIONAL = re.compile(r"\b(?:nacional|domestic|vuelo domestico|desde (?:medellin|cali|cartagena|barranquilla|pereira|"
                       r"bucaramanga|santa marta|cucuta|manizales|armenia|monteria|san andres|leticia))\b")


def hora_24(h: int, m: int | None, marca: str | None) -> tuple[str, bool] | None:
    """('HH:MM', ambigua). Con «pm»/«de la tarde» o formato de 24 h no hay duda; «6» a secas sí la tiene."""
    if not (0 <= h <= 24) or (m is not None and not 0 <= m <= 59):
        return None
    m = m or 0
    marca = _norm(marca or "").replace(".", "").replace(" ", "")
    if marca.startswith("p") or "tarde" in marca or "noche" in marca:
        h = h % 12 + 12
        ambigua = False
    elif marca.startswith("a") or "manana" in marca or "madrugada" in marca:
        h = h % 12
        ambigua = False
    else:
        ambigua = 1 <= h <= 11
    return f"{h % 24:02d}:{m:02d}", ambigua


def leer_llegada(texto: str) -> dict | None:
    """«llego a las 6 pm» → {'hora': '18:00', 'ambigua': False, 'nacional': False}; None si no anuncia una llegada."""
    t = _norm(texto)
    g = _LLEGADA.search(t)
    if not g:
        return None
    r = hora_24(int(g.group(1)), int(g.group(2)) if g.group(2) else None, g.group(3))
    if not r:
        return None
    return {"hora": r[0], "ambigua": r[1], "nacional": bool(_NACIONAL.search(t))}


def leer_hora_cita(texto: str) -> str | None:
    """«a las 8 pm» → '20:00' (hora de un plan, no de una llegada). None si no hay o es ambigua sin pista."""
    t = _norm(texto)
    g = _A_LAS.search(t)
    if not g:
        return None
    r = hora_24(int(g.group(1)), int(g.group(2)) if g.group(2) else None, g.group(3))
    return r[0] if r else None


# ─────────────────────────── tráfico y trayectos ───────────────────────────
def hora_pico(dt: datetime) -> bool:
    """Bogotá, días hábiles: 5:30–8:30 y 16:30–19:30."""
    if dt.weekday() >= 5:
        return False
    m = dt.hour * 60 + dt.minute
    return 5 * 60 + 30 <= m <= 8 * 60 + 30 or 16 * 60 + 30 <= m <= 19 * 60 + 30


def taxi_min(origen: tuple[float, float], destino: tuple[float, float], salida: datetime, ahora: datetime) -> tuple[int, str]:
    """(minutos en taxi/app con el tráfico de esa hora, fuente 'google'|'estimado'). Nunca lanza."""
    try:
        from . import transporte
        sal = max(0, int((salida - ahora).total_seconds() // 60))
        r = transporte.rutas((origen[0], origen[1], ""), (destino[0], destino[1], ""), min(sal, 24 * 60))
        tx = (r.get("opciones") or {}).get("taxi")
        if tx:
            m = int(tx["min"])
            if r.get("fuente") == "google" and hora_pico(salida):
                m = round(m * 1.3)   # la predicción de Google a horas de pico suele quedarse corta en Bogotá: margen
            return m, ("google" if r.get("fuente") == "google" else "estimado")
    except Exception as e:
        log.warning("taxi_min_fallo error=%s", type(e).__name__)
    km = geo.haversine_km(origen[0], origen[1], destino[0], destino[1]) * 1.3
    return max(10, round(km / (13.0 if hora_pico(salida) else 24.0) * 60)), "estimado"


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _coords_hotel(trip: dict) -> tuple[float, float]:
    if trip.get("lat_hotel") is not None and trip.get("lng_hotel") is not None:
        return float(trip["lat_hotel"]), float(trip["lng_hotel"])
    return geo.resolve_origin(trip) or CENTRO


def plan_de_llegada(trip: dict, llegada: dict, ahora: datetime, destino: tuple[float, float, str] | None = None) -> dict:
    """Las cuentas de la llegada. Devuelve cada hito (datetime) y los tramos, para el prompt y para los tests."""
    hh, mm = (int(x) for x in llegada["hora"].split(":"))
    dia = ahora.date()
    try:
        ini = datetime.strptime(trip.get("inicio") or "", "%Y-%m-%d").date()
        if ini > dia:
            dia = ini   # aún no empieza el viaje: la llegada es el primer día
    except ValueError:
        pass
    aterriza = datetime.combine(dia, datetime.min.time()).replace(hour=hh, minute=mm, tzinfo=ahora.tzinfo)
    buf = SALIDA_AEROPUERTO_NACIONAL if llegada.get("nacional") else SALIDA_AEROPUERTO_INTERNACIONAL
    sale_terminal = aterriza + timedelta(minutes=buf)
    hotel = _coords_hotel(trip)
    t1, f1 = taxi_min(AEROPUERTO, hotel, sale_terminal, ahora)
    en_hotel = sale_terminal + timedelta(minutes=t1)
    listo = en_hotel + timedelta(minutes=ALISTARSE_EN_HOTEL)
    out = {"aterriza": aterriza, "buffer": buf, "sale_terminal": sale_terminal, "tramo1": t1, "fuente1": f1,
           "en_hotel": en_hotel, "listo": listo, "pico_salida": hora_pico(sale_terminal)}
    if destino:
        t2, f2 = taxi_min(hotel, (destino[0], destino[1]), listo, ahora)
        out |= {"destino": destino[2], "tramo2": t2, "fuente2": f2, "llega_destino": listo + timedelta(minutes=t2 + MARGEN_LLEGADA)}
    return out


def texto_llegada(p: dict, llegada: dict) -> str:
    L = [f"- El usuario dijo que llega al aeropuerto a las {llegada['hora']}"
         + (" (¡hora ambigua! pregúntale si es de la mañana o de la tarde ANTES de planear nada; estos cálculos suponen la hora tal cual)" if llegada.get("ambigua") else "") + ".",
         f"  · Aterriza {_hm(p['aterriza'])} → sale de la terminal ~{_hm(p['sale_terminal'])} (+{p['buffer']} min de migración, equipaje y salida; "
         + ("vuelo nacional" if llegada.get("nacional") else "supón vuelo internacional: si es nacional se ahorra ~40 min") + ").",
         f"  · Aeropuerto → hotel a esa hora: ~{p['tramo1']} min en taxi/app"
         + (" (HORA PICO: trancón fuerte)" if p["pico_salida"] else "") + f" → llega al hotel ~{_hm(p['en_hotel'])}.",
         f"  · +{ALISTARSE_EN_HOTEL} min para dejar maletas y alistarse → puede salir del hotel ~{_hm(p['listo'])}."]
    if p.get("destino"):
        L.append(f"  · Hotel → {p['destino']}: ~{p['tramo2']} min → llegaría ~{_hm(p['llega_destino'])} (con {MARGEN_LLEGADA} min de margen).")
    L.append(f"  REGLA: NO propongas salir del hotel antes de las {_hm(p['listo'])}, ni llegar a ningún lugar antes de "
             f"{_hm(p.get('llega_destino') or p['listo'] + timedelta(minutes=20))}. Si lo que quiere (cenar, ir a un lugar) "
             "queda muy tarde o la cocina/lugar cierra antes, díselo claro y ofrécele una alternativa realista "
             "(algo cerca del hotel, domicilio, dejarlo para mañana).")
    return "\n".join(L)


# ─────────────────────────── bloque del prompt ───────────────────────────
def _proximo_plan(trip: dict, ahora: datetime) -> str:
    """El siguiente plan de HOY con hora, y a qué hora hay que salir para llegar (tráfico incluido)."""
    try:
        from . import plans as plans_mod, scheduler
        hoy = ahora.date().isoformat()
        for p in plans_mod.ordenar(plans_mod.normalizar_lista(trip.get("planes", []))):
            if p.get("fecha") != hoy or not p.get("hora") or p.get("tipo") in ("vuelo",):
                continue
            hh, mm = (int(x) for x in p["hora"].split(":")[:2])
            cuando = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if cuando < ahora - timedelta(minutes=15):
                continue
            falta = int((cuando - ahora).total_seconds() // 60)
            linea = f"- Próximo plan de hoy: {p['hora']} {p['titulo']}" + (f" @ {p['lugar']}" if p.get("lugar") else "") + f" (faltan {falta} min)."
            lat, lng = scheduler._plan_coords(p, trip.get("ciudad", ""))
            origen = geo.resolve_origin(trip)
            if lat is not None and lng is not None and origen:
                t, _ = taxi_min(origen, (lat, lng), cuando, ahora)
                sale = cuando - timedelta(minutes=t + MARGEN_LLEGADA)
                linea += f" Trayecto ~{t} min con el tráfico de esa hora → debe salir a más tardar a las {_hm(sale)}."
            return linea
    except Exception as e:
        log.warning("proximo_plan_fallo error=%s", type(e).__name__)
    return ""


def bloque(trip: dict, mensaje: str = "", lugares: list[dict] | None = None, ahora: datetime | None = None) -> str:
    """El bloque RELOJ Y LOGÍSTICA del system prompt. Nunca lanza."""
    try:
        ahora = ahora or timeutil.now_local(trip)
        L = [">>> RELOJ Y LOGÍSTICA (calculado por el sistema con la hora REAL del destino; ÚSALO, no improvises tiempos) <<<",
             f"- Ahora en {trip.get('ciudad', '')}: {_DIAS[ahora.weekday()]} {ahora.strftime('%d/%m')} {_hm(ahora)}. "
             + ("AHORA es hora pico de tráfico (trancón fuerte, TransMilenio lleno)." if hora_pico(ahora) else
                "Hora pico de tráfico: 5:30–8:30 y 16:30–19:30 en días hábiles (si el trayecto cae ahí, suma 40-60%).")]
        prox = _proximo_plan(trip, ahora)
        if prox:
            L.append(prox)
        llegada = leer_llegada(mensaje)
        if llegada:
            destino = None
            if lugares:
                try:
                    from . import visual
                    hall = visual.mencionados(mensaje, lugares, maximo=1)
                    if hall and hall[0].get("lat") is not None:
                        destino = (float(hall[0]["lat"]), float(hall[0]["lng"]), hall[0]["name"])
                except Exception:
                    pass
            L.append(texto_llegada(plan_de_llegada(trip, llegada, ahora, destino), llegada))
        return "\n".join(L) + "\n"
    except Exception as e:
        log.warning("bloque_reloj_fallo error=%s", type(e).__name__)
        return ""
