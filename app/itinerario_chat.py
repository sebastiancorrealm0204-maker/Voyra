"""«Agrégalo a mi itinerario»: cuando el usuario lo pide en el chat, SIEMPRE se agrega (sin sheet de confirmación).

Antes el pedido pasaba por el router de intención y el extractor de planes (dos llamadas al modelo) y terminaba, si
acaso, como una propuesta que el usuario tenía que confirmar en otra pantalla: muchas veces no se agregaba nada.
Aquí es determinístico: detectamos el pedido, resolvemos QUÉ lugar (el que nombra, «el segundo», o el que el Companion
acaba de recomendar), lo guardamos y respondemos con una plantilla. Si no se puede saber de qué lugar habla, devuelve
None y el chat sigue su camino normal (el Companion pregunta cuál).
"""
import logging
import re
import unicodedata
from datetime import timedelta

from . import db, geo, plans as plans_mod, reloj, timeutil, visual

log = logging.getLogger("voyra.itinerario_chat")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


_CLITICO = re.compile(r"\b(?:agregal[oa]s?|anadel[oa]s?|ponl[oa]s?|anotal[oa]s?|guardal[oa]s?|incluyel[oa]s?|metel[oa]s?)\b")
_VERBO = re.compile(r"\b(?:agrega|agregar|agregame|agreguen?|anade|anadir|pon|poner|anota|anotar|guarda|guardar|incluye|incluir|mete|meter|sumar?|suma)\b")
_DONDE = re.compile(r"\b(?:itinerario|agenda|mis planes|mi plan|mi calendario|al plan)\b")
_ORDINALES = [(re.compile(r"\b(?:primer[oa]?|1|uno)\b"), 0), (re.compile(r"\b(?:segund[oa]|2|dos)\b"), 1),
              (re.compile(r"\b(?:tercer[oa]?|3|tres)\b"), 2), (re.compile(r"\b(?:ultim[oa])\b"), -1)]
_TODOS = re.compile(r"\b(?:todos|todas|los tres|las tres|ambos|ambas)\b")


def pide_agregar(mensaje: str) -> bool:
    """¿El usuario pide agregar algo a su itinerario? (verbo + itinerario, o un clítico tipo «agrégalo»)."""
    m = _norm(mensaje)
    if _CLITICO.search(m):
        return True
    return bool(_VERBO.search(m) and _DONDE.search(m))


def _lugares_de(texto: str, lugares: list[dict], maximo: int = 3) -> list[dict]:
    return visual.mencionados(texto, lugares, maximo=maximo)


def resolver_lugares(mensaje: str, ultimo_companion: str, lugares: list[dict]) -> list[dict]:
    """Qué lugar(es) quiere agregar: el que nombra; si no, uno de los que el Companion acaba de ofrecer
    («el segundo», «ese», «todos»). Vacío si hay duda (entonces el Companion pregunta)."""
    nombrados = _lugares_de(mensaje, lugares)
    if nombrados:
        return nombrados
    ofrecidos = _lugares_de(ultimo_companion, lugares)
    if not ofrecidos:
        return []
    m = _norm(mensaje)
    if _TODOS.search(m):
        return ofrecidos
    for rx, i in _ORDINALES:
        if rx.search(m):
            try:
                return [ofrecidos[i]]
            except IndexError:
                return []
    return ofrecidos[:1] if len(ofrecidos) == 1 else []


_TIPO = {"restaurante": "restaurante", "bar": "actividad", "cafe": "actividad"}

_TXT = {
    "agregado": {
        "es": "Listo, agregué {nombres} a tu itinerario{cuando}.{salida}",
        "en": "Done, I added {nombres} to your itinerary{cuando}.{salida}",
        "pt": "Pronto, adicionei {nombres} ao seu roteiro{cuando}.{salida}",
        "fr": "C'est fait, j'ai ajouté {nombres} à ton itinéraire{cuando}.{salida}",
        "de": "Erledigt, ich habe {nombres} zu deinem Reiseplan hinzugefügt{cuando}.{salida}",
        "it": "Fatto, ho aggiunto {nombres} al tuo itinerario{cuando}.{salida}",
    },
    "ya": {
        "es": "{nombres} ya estaba en tu itinerario para ese día.",
        "en": "{nombres} was already in your itinerary for that day.",
        "pt": "{nombres} já estava no seu roteiro para esse dia.",
        "fr": "{nombres} était déjà dans ton itinéraire pour ce jour-là.",
        "de": "{nombres} stand für diesen Tag schon in deinem Reiseplan.",
        "it": "{nombres} era già nel tuo itinerario per quel giorno.",
    },
    "salida": {
        "es": " Para llegar a las {hora}, sal a más tardar a las {sale} (trayecto de unos {min} min con el tráfico de esa hora).",
        "en": " To arrive at {hora}, leave by {sale} at the latest (about {min} min with traffic at that hour).",
        "pt": " Para chegar às {hora}, saia até às {sale} (cerca de {min} min com o trânsito dessa hora).",
        "fr": " Pour arriver à {hora}, pars au plus tard à {sale} (environ {min} min avec la circulation à cette heure).",
        "de": " Um um {hora} anzukommen, fahre spätestens um {sale} los (etwa {min} Min. mit dem Verkehr zu dieser Zeit).",
        "it": " Per arrivare alle {hora}, parti al più tardi alle {sale} (circa {min} min con il traffico a quell'ora).",
    },
}


def _salida(trip: dict, lugar: dict, fecha: str, hora: str, ahora, idioma: str) -> str:
    """«Sal a más tardar a las HH:MM» con el tráfico de esa hora, si se conoce el origen. Vacío si no se puede calcular."""
    try:
        origen = geo.resolve_origin(trip)
        if not origen or lugar.get("lat") is None:
            return ""
        hh, mm = (int(x) for x in hora.split(":")[:2])
        y, mo, d = (int(x) for x in fecha.split("-"))
        cuando = ahora.replace(year=y, month=mo, day=d, hour=hh, minute=mm, second=0, microsecond=0)
        t, _ = reloj.taxi_min(origen, (float(lugar["lat"]), float(lugar["lng"])), cuando, ahora)
        sale = cuando - timedelta(minutes=t + reloj.MARGEN_LLEGADA)
        return _TXT["salida"].get(idioma, _TXT["salida"]["es"]).format(hora=hora, sale=sale.strftime("%H:%M"), min=t)
    except Exception as e:
        log.warning("salida_fallo error=%s", type(e).__name__)
        return ""


def agregar_desde_chat(trip: dict, mensaje: str, ultimo_companion: str) -> dict | None:
    """Si el mensaje pide agregar al itinerario y sabemos qué lugar, lo guarda y devuelve
    {'reply', 'plan(es)', 'lugares'}; si no, None (el chat normal pregunta cuál)."""
    if not pide_agregar(mensaje):
        return None
    ciudad = trip.get("ciudad", "")
    lugares = db.places_for_city(ciudad)
    elegidos = resolver_lugares(mensaje, ultimo_companion, lugares)
    if not elegidos:
        return None
    ahora = timeutil.now_local(trip)
    fecha = plans_mod.resolver_fecha_relativa(mensaje, ahora) or ahora.date().isoformat()
    hora = reloj.leer_hora_cita(mensaje)
    idioma = (trip.get("idioma") or "es").lower()

    existentes = plans_mod.normalizar_lista(trip.get("planes", []))
    nuevos, ya = [], []
    for p in elegidos:
        if any(_norm(x.get("lugar") or x.get("titulo")) == _norm(p["name"]) and x.get("fecha") == fecha for x in existentes):
            ya.append(p["name"])
            continue
        local = p.get("local") if isinstance(p.get("local"), dict) else {}
        nuevos.append({"titulo": p["name"], "tipo": _TIPO.get(p.get("category"), "actividad"), "lugar": p["name"],
                       "fecha": fecha, "hora": hora, "detalle": (local.get("clave") or "")[:160] or None})
    if nuevos:
        db.add_plans(trip["id"], nuevos, origen="chat")
    cuando = f" ({fecha}{' ' + hora if hora else ''})"
    if nuevos:
        salida = _salida(trip, next(p for p in elegidos if p["name"] == nuevos[0]["lugar"]), fecha, hora, ahora, idioma) if hora else ""
        reply = _TXT["agregado"].get(idioma, _TXT["agregado"]["es"]).format(
            nombres=", ".join(n["titulo"] for n in nuevos), cuando=cuando, salida=salida)
    else:
        reply = _TXT["ya"].get(idioma, _TXT["ya"]["es"]).format(nombres=", ".join(ya))
    return {"reply": reply, "agregados": nuevos, "lugares": elegidos}
