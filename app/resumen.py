"""Cierre del viaje y fotos del itinerario.

- `terminado`: el viaje se cierra cuando ya pasó su último día (en la hora del destino). Desde ahí el chat no
  recibe mensajes nuevos y la app muestra el resumen.
- `fotos_de_planes`: la tarjeta con foto REAL (Google Places) de cada plan del itinerario que corresponde a un
  lugar de nuestra curación. Los planes que no son un lugar curado (vuelos, transporte…) no llevan foto.
- `resumen`: lo que hizo el viajero (sus planes que son lugares curados) y los «ganchos»: lugares curados de la
  ciudad a los que no fue, para dejarle la puerta abierta a volver. Nada se inventa: todo sale de la curación.
"""
import logging
from datetime import date, datetime

from . import timeutil, visual

log = logging.getLogger("voyra.resumen")

# Un gancho por tipo de plan, en este orden: comer, conocer, salir.
_GRUPOS_GANCHO = (
    {"restaurante"},
    {"atraccion", "museo", "parque", "mercado", "experiencia", "excursion"},
    {"bar", "cafe", "compras"},
)
MAX_GANCHOS = 3


def _fecha(s: str | None) -> date | None:
    try:
        return datetime.strptime(s or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def terminado(trip: dict) -> bool:
    """True si ya pasó el último día del viaje (hora del destino). Sin fecha de fin válida, nunca se cierra."""
    fin = _fecha(trip.get("fin"))
    return bool(fin) and timeutil.now_local(trip).date() > fin


def _lugar_del_plan(plan: dict, lugares: list[dict]) -> dict | None:
    if plan.get("tipo") in ("vuelo", "transporte"):
        return None
    texto = f"{plan.get('lugar') or ''} . {plan.get('titulo') or ''}"
    hallados = visual.mencionados(texto, lugares, maximo=1)
    return hallados[0] if hallados else None


def fotos_de_planes(planes: list[dict], lugares: list[dict], idioma: str = "es") -> dict[str, dict]:
    """{plan_id: tarjeta} para los planes que son un lugar curado. La tarjeta trae `foto`/`fotos` solo si Google
    las tiene; si no, el plan se queda sin foto."""
    por_plan = {}
    for p in planes:
        lugar = _lugar_del_plan(p, lugares)
        if lugar and p.get("id"):
            por_plan[p["id"]] = lugar
    if not por_plan:
        return {}
    unicos = list({l["name"]: l for l in por_plan.values()}.values())
    cards = {c["name"]: c for c in visual.tarjetas(unicos, idioma, n_fotos=3)}
    return {pid: cards[l["name"]] for pid, l in por_plan.items() if l["name"] in cards}


def _ganchos(lugares: list[dict], visitados: set[str]) -> list[dict]:
    """Hasta MAX_GANCHOS lugares curados NO visitados: el mejor calificado de cada grupo (comer / conocer / salir),
    con preferencia por los que traen dato local (la razón concreta de ir)."""
    def puntaje(p: dict) -> tuple:
        local = p.get("local")
        return (1 if isinstance(local, dict) and local.get("clave") else 0, float(p.get("rating") or 0))

    candidatos = [p for p in lugares
                  if p.get("category") in visual.CATEGORIAS_VISUALES and p.get("category") != "hotel"
                  and p["name"] not in visitados]
    out: list[dict] = []
    for grupo in _GRUPOS_GANCHO:
        mejores = sorted((p for p in candidatos if p.get("category") in grupo), key=puntaje, reverse=True)
        if mejores:
            out.append(mejores[0])
    return out[:MAX_GANCHOS]


def resumen(trip: dict, planes: list[dict], lugares: list[dict]) -> dict:
    idioma = trip.get("idioma") or "es"
    visitados: dict[str, dict] = {}
    for p in planes:
        lugar = _lugar_del_plan(p, lugares)
        if lugar:
            visitados.setdefault(lugar["name"], lugar)
    ini, fin = _fecha(trip.get("inicio")), _fecha(trip.get("fin"))
    return {
        "ciudad": trip.get("ciudad", ""),
        "inicio": trip.get("inicio"), "fin": trip.get("fin"),
        "dias": (fin - ini).days + 1 if ini and fin else None,
        "planes": sum(1 for p in planes if p.get("tipo") not in ("vuelo", "transporte")),
        "terminado": terminado(trip),
        "encuesta_respondida": encuesta_respondida(trip["id"]),
        "visitados": visual.tarjetas(list(visitados.values())[:8], idioma, n_fotos=1),
        "ganchos": visual.tarjetas(_ganchos(lugares, set(visitados)), idioma, n_fotos=3),
    }


# ── Encuesta de cierre ──
GUSTOS = ("comida", "lugares", "tips", "rutas", "itinerario", "chat")
MAX_TEXTO = 500


def guardar_encuesta(trip: dict, user_id: str | None, gusto: list[str], mejora: str, feature: str) -> bool:
    """Guarda (o reemplaza) la encuesta del viaje. False si llegó vacía. Las claves de `gusto` se filtran a las
    conocidas y los textos se recortan: es entrada de usuario."""
    from . import db
    gusto = [g for g in dict.fromkeys(gusto) if g in GUSTOS]
    mejora, feature = (mejora or "").strip()[:MAX_TEXTO], (feature or "").strip()[:MAX_TEXTO]
    if not (gusto or mejora or feature):
        return False
    with db.conn() as c:
        c.execute("DELETE FROM encuestas WHERE trip_id=?", (trip["id"],))
    db.insert("encuestas", {"trip_id": trip["id"], "user_id": user_id, "ciudad": trip.get("ciudad", ""),
                            "gusto": ",".join(gusto), "mejora": mejora, "feature": feature})
    return True


def encuesta_respondida(trip_id: str) -> bool:
    from . import db
    with db.conn() as c:
        return c.execute("SELECT 1 FROM encuestas WHERE trip_id=?", (trip_id,)).fetchone() is not None
