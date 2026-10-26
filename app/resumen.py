"""Cierre del viaje y fotos del itinerario.

- `terminado`: el viaje se cierra cuando ya pasó su último día (en la hora del destino). Desde ahí el chat no
  recibe mensajes nuevos y la app muestra el resumen.
- `fotos_de_planes`: la tarjeta con foto REAL (Google Places) de cada plan del itinerario que corresponde a un
  lugar de nuestra curación. Los planes que no son un lugar curado (vuelos, transporte…) no llevan foto.
- `resumen`: lo que hizo el viajero (sus planes que son lugares curados) y los «ganchos»: lugares curados de la
  ciudad a los que no fue, para dejarle la puerta abierta a volver. Nada se inventa: todo sale de la curación.
"""
import logging
import os
import threading
from datetime import date, datetime, timezone

from . import net, timeutil, visual

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
    cards = {c["name"]: c for c in visual.tarjetas(unicos, idioma, n_fotos=1)}
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
        "narrativa": narrativa(trip, planes),
        "terminado": terminado(trip),
        "encuesta_respondida": encuesta_respondida(trip["id"]),
        "visitados": visual.tarjetas(list(visitados.values())[:8], idioma, n_fotos=1),
        "ganchos": visual.tarjetas(_ganchos(lugares, set(visitados)), idioma, n_fotos=2),
    }


# ── Encuesta de cierre (obligatoria antes de ver el resumen): el destino y la app ──
GUSTOS_DESTINO = ("comida", "cultura", "noche", "naturaleza", "gente", "cafe")
GUSTOS_APP = ("recomendaciones", "lugares", "tips", "rutas", "itinerario", "chat")
MAX_TEXTO = 500


def _nota(n) -> int | None:
    return n if isinstance(n, int) and 1 <= n <= 5 else None


def guardar_encuesta(trip: dict, user_id: str | None, *, nota_destino, nota_app, gusto_destino=(), gusto_app=(),
                     mejora_destino="", mejora_app="", feature="") -> bool:
    """Guarda (o reemplaza) la encuesta del viaje. False si faltan las dos notas (son obligatorias). Las claves se
    filtran a las conocidas y los textos se recortan: es entrada de usuario."""
    from . import db
    nd, na = _nota(nota_destino), _nota(nota_app)
    if nd is None or na is None:
        return False
    gd = ",".join(g for g in dict.fromkeys(gusto_destino) if g in GUSTOS_DESTINO)
    ga = ",".join(g for g in dict.fromkeys(gusto_app) if g in GUSTOS_APP)
    corto = lambda t: (t or "").strip()[:MAX_TEXTO]  # noqa: E731
    with db.conn() as c:
        c.execute("DELETE FROM encuesta_cierre WHERE trip_id=?", (trip["id"],))
    db.insert("encuesta_cierre", {
        "trip_id": trip["id"], "user_id": user_id, "ciudad": trip.get("ciudad", ""),
        "nota_destino": nd, "gusto_destino": gd, "mejora_destino": corto(mejora_destino),
        "nota_app": na, "gusto_app": ga, "mejora_app": corto(mejora_app), "feature": corto(feature)})
    if SUPABASE_URL and SUPABASE_KEY:
        fila = {"trip_id": trip["id"], "user_id": user_id, "ciudad": trip.get("ciudad", ""),
                "nota_destino": nd, "gusto_destino": gd, "mejora_destino": corto(mejora_destino),
                "nota_app": na, "gusto_app": ga, "mejora_app": corto(mejora_app), "feature": corto(feature),
                "created_at": datetime.now(timezone.utc).isoformat()}
        threading.Thread(target=_enviar_a_supabase, args=(fila,), daemon=True).start()
    return True


SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _enviar_a_supabase(fila: dict) -> None:
    """Copia la encuesta a la tabla `encuesta_cierre` de Supabase (upsert por trip_id). Nunca lanza: la copia local
    en nuestra base ya quedó guardada y es la que manda."""
    try:
        r = net.request(
            "POST", f"{SUPABASE_URL}/rest/v1/encuesta_cierre", params={"on_conflict": "trip_id"}, retries=1, timeout=8,
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=fila)
        if r.status_code >= 300:
            log.warning("supabase_encuesta_fallo status=%s body=%s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning("supabase_encuesta_error error=%s", type(e).__name__)


def encuesta_respondida(trip_id: str) -> bool:
    from . import db
    with db.conn() as c:
        return c.execute("SELECT 1 FROM encuesta_cierre WHERE trip_id=?", (trip_id,)).fetchone() is not None


# ── Resumen detallado del viaje terminado (usa TODO: itinerario, documentos y la conversación) ──
_SISTEMA_NARRATIVA = (
    "Eres el Companion de Voyra y escribes el RESUMEN FINAL de un viaje que ya terminó, para el viajero. "
    "Escribe en {idioma}, en segunda persona, con tono cálido y concreto. Usa SOLO lo que aparece en los datos "
    "(itinerario, documentos y conversación): no inventes lugares, platos, horarios, precios ni anécdotas. "
    "Si de un día no hay datos, no lo rellenes. Responde SOLO con JSON válido con esta forma:\n"
    '{{"titulo": "frase corta y bonita sobre el viaje", "intro": "2-3 frases", '
    '"dias": [{{"fecha": "YYYY-MM-DD", "titulo": "…", "texto": "3-5 frases detalladas de ese día", '
    '"lugares": ["nombres exactos de lugares de ese día"]}}], '
    '"comida": "qué comió/probó y qué recomendaron, o vacío", '
    '"descubrimientos": "tips locales y cosas que aprendió, o vacío", '
    '"datos_utiles": ["transporte, reservas, vuelos y datos prácticos que usó"], '
    '"momentos": ["3-5 momentos destacados"], "cierre": "1-2 frases para despedirlo"}}'
)


def _contexto_narrativa(trip: dict, planes: list[dict], mensajes: list[dict], docs: list[dict]) -> str:
    from . import plans as plans_mod
    partes = [f"VIAJE: {trip.get('ciudad', '')}, del {trip.get('inicio')} al {trip.get('fin')}. Hotel: {trip.get('hotel') or '—'}."]
    if planes:
        partes.append("ITINERARIO:")
        for p in plans_mod.ordenar(planes):
            partes.append(f"- {p.get('fecha') or 'sin fecha'} {p.get('hora') or ''} [{p.get('tipo')}] {p.get('titulo')}"
                          f"{' @ ' + p['lugar'] if p.get('lugar') else ''}{' — ' + p['detalle'] if p.get('detalle') else ''}")
    if docs:
        partes.append("DOCUMENTOS SUBIDOS:")
        partes += [f"- {d.get('filename')}: {(d.get('summary') or '')[:300]}" for d in docs[:10]]
    if mensajes:
        partes.append("CONVERSACIÓN (cronológica):")
        for m in mensajes[-150:]:
            quien = "Viajero" if m["role"] == "user" else "Companion"
            partes.append(f"{quien}: {(m['content'] or '')[:400]}")
    return "\n".join(partes)[:24000]


def _narrativa_basica(trip: dict, planes: list[dict]) -> dict:
    """Sin IA (o si falla): el resumen armado solo con el itinerario real, día por día."""
    from . import plans as plans_mod
    por_dia = plans_mod.agrupar_por_dia(planes)
    dias = []
    for fecha, ps in por_dia.items():
        if fecha == "sin_fecha":
            continue
        dias.append({"fecha": fecha, "titulo": "", "lugares": [p["lugar"] for p in ps if p.get("lugar")],
                     "texto": " · ".join(f"{(p.get('hora') + ' ') if p.get('hora') else ''}{p.get('titulo')}" for p in ps)})
    return {"titulo": "", "intro": "", "dias": dias, "comida": "", "descubrimientos": "",
            "datos_utiles": [], "momentos": [], "cierre": "", "ia": False}


def narrativa(trip: dict, planes: list[dict]) -> dict:
    """Resumen detallado escrito a partir de itinerario + documentos + chat. Se guarda en el viaje y solo se vuelve a
    generar si cambió lo que lo alimenta. Si la IA falla, devuelve el resumen básico del itinerario (no se guarda)."""
    import hashlib
    from . import city_knowledge, db, llm

    mensajes = [m for m in db.rows("messages", trip["id"], limit=400)
                if not (m["role"] == "user" and (m["content"] or "").strip().startswith("["))]
    docs = db.rows("documents", trip["id"], limit=20)
    contexto = _contexto_narrativa(trip, planes, mensajes, docs)
    idioma = trip.get("idioma") or "es"
    firma = hashlib.sha1(f"{idioma}|{contexto}".encode("utf-8")).hexdigest()
    guardado = trip.get("resumen_ia") or {}
    if guardado.get("firma") == firma and guardado.get("data"):
        return guardado["data"]
    if not (planes or mensajes or docs) or not llm._available():
        return _narrativa_basica(trip, planes)
    try:
        raw = llm._call(llm.CHAT_PROVIDER, _SISTEMA_NARRATIVA.format(idioma=city_knowledge.lang_name(idioma)),
                        [{"role": "user", "content": contexto}], max_tokens=2500)
        d = llm._parse_json(raw)
        data = {"titulo": str(d.get("titulo") or ""), "intro": str(d.get("intro") or ""),
                "dias": [{"fecha": str(x.get("fecha") or ""), "titulo": str(x.get("titulo") or ""),
                          "texto": str(x.get("texto") or ""), "lugares": [str(y) for y in (x.get("lugares") or [])][:8]}
                         for x in (d.get("dias") or []) if isinstance(x, dict)][:31],
                "comida": str(d.get("comida") or ""), "descubrimientos": str(d.get("descubrimientos") or ""),
                "datos_utiles": [str(x) for x in (d.get("datos_utiles") or [])][:10],
                "momentos": [str(x) for x in (d.get("momentos") or [])][:6],
                "cierre": str(d.get("cierre") or ""), "ia": True}
        db.update_trip(trip["id"], {"resumen_ia": {"firma": firma, "data": data}})
        return data
    except (ValueError, TypeError, KeyError, AttributeError, llm.ProviderError) as e:
        log.warning("narrativa_fallo trip=%s error=%s", trip["id"], type(e).__name__)
        return _narrativa_basica(trip, planes)
