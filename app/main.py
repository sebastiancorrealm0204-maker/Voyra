"""API del Companion en destino — FastAPI.

Correr:  uvicorn app.main:app --reload
Docs:    http://localhost:8000/docs
"""
import json
import urllib.parse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import context, db, engine, geo, llm, push, scheduler, search, seed_data, watchers

app = FastAPI(title="Voyra Companion", version="0.1.0")

# CORS: permite que el frontend (corriendo en el navegador, ej. un Artifact de
# Claude) llame a este backend en localhost. En producción, reemplaza "*" por
# el dominio real del frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_db()
db.seed_destination_places(seed_data.all_seeds())


@app.on_event("startup")
def _arrancar_scheduler():
    """Arranca el scheduler proactivo (check-ins por hora local del destino).
    Si APScheduler no está instalado, la app sigue normal sin modo proactivo."""
    scheduler.start()


# ── Schemas ──
class TripIn(BaseModel):
    ciudad: str = ""
    hotel: str = ""
    inicio: str = ""
    fin: str = ""
    vuelo_ida: str = ""
    vuelo_regreso: str = ""
    pais: str = "Colombia"
    gustos: list[str] = Field(default_factory=list)


class TripPatchIn(BaseModel):
    """Para completar/corregir datos del viaje (ej. lo que faltó tras subir reservas)."""
    ciudad: str | None = None
    hotel: str | None = None
    inicio: str | None = None
    fin: str | None = None
    vuelo_ida: str | None = None
    vuelo_regreso: str | None = None
    pais: str | None = None
    gustos: list[str] | None = None


class SignalIn(BaseModel):
    source: str
    category: str
    operational: bool = False
    payload: str


class LocationIn(BaseModel):
    zona: str = "En el hotel"
    lat: float | None = None
    lng: float | None = None
    disparar_geofence: bool = False


class ChatIn(BaseModel):
    message: str


class DocIn(BaseModel):
    filename: str
    text_content: str = ""           # texto del documento (.txt o ya extraído)
    image_data_url: str = ""         # data:image/...;base64,... para fotos/capturas de reservas
    pdf_data_url: str = ""           # data:application/pdf;base64,... — se extrae texto en backend


class FeedbackIn(BaseModel):
    feedback: str  # tapped | dismissed | not_interested


class PlanIn(BaseModel):
    plan: str | None = None          # compat: texto suelto (formato viejo)
    fecha: str | None = None
    hora: str | None = None
    titulo: str | None = None
    tipo: str | None = None
    detalle: str | None = None
    lugar: str | None = None


class PlansConfirmIn(BaseModel):
    """Confirma (guarda) un conjunto de planes que el chat propuso."""
    planes: list[dict]


class PushSubIn(BaseModel):
    subscription: dict   # {endpoint, keys:{p256dh, auth}} que da el navegador


# ── Trips ──
@app.post("/trips")
def create_trip(t: TripIn):
    tid = db.create_trip(t.model_dump())
    if t.ciudad.strip():
        saludo = (f"¡Listo! Ya estoy vigilando tu vuelo, el clima y lo que se mueve en {t.ciudad}. "
                  "Para avisarte solo de lo que te sirve: ¿qué planes tienes para hoy y mañana?")
    else:
        saludo = ("¡Hola! Soy tu Companion. Sube tus reservas (vuelo, hotel, tours) y armo tu "
                  "viaje contigo. También puedes contarme a dónde vas y cuándo.")
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": saludo})
    return {"trip_id": tid, "greeting": saludo, "trip": db.get_trip(tid)}


@app.patch("/trips/{tid}")
def patch_trip(tid: str, p: TripPatchIn):
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    patch = {k: v for k, v in p.model_dump().items() if v is not None}
    if patch:
        db.update_trip(tid, patch)
    return db.get_trip(tid)


@app.get("/trips/{tid}")
def get_trip(tid: str):
    t = db.get_trip(tid)
    if not t:
        raise HTTPException(404, "trip no existe")
    return t


@app.get("/trips/{tid}/plans")
def list_plans(tid: str):
    """Planes del viaje: lista plana ordenada + agrupados por día (para el calendario)."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    from . import plans as plans_mod
    planes = db.get_plans(tid)
    return {"planes": plans_mod.ordenar(planes),
            "por_dia": plans_mod.agrupar_por_dia(planes)}


@app.post("/trips/{tid}/plans")
def add_plan(tid: str, p: PlanIn):
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    # Compat: si llega 'plan' como texto suelto, se guarda como antes.
    if p.plan and not (p.titulo or p.fecha):
        nuevo = {"titulo": p.plan}
    else:
        nuevo = {"fecha": p.fecha, "hora": p.hora, "titulo": p.titulo or p.plan or "Plan",
                 "tipo": p.tipo, "detalle": p.detalle, "lugar": p.lugar}
    return {"planes": db.add_plans(tid, [nuevo], origen="manual")}


@app.post("/trips/{tid}/plans/confirm")
def confirm_plans(tid: str, body: PlansConfirmIn):
    """Guarda los planes que el chat o un documento propuso (tras confirmación del usuario)."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return {"planes": db.add_plans(tid, body.planes, origen="chat")}


@app.delete("/trips/{tid}/plans/{plan_id}")
def remove_plan(tid: str, plan_id: str):
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return {"planes": db.delete_plan(tid, plan_id)}


@app.get("/trips/{tid}/plans/{plan_id}/maps")
def plan_maps_link(tid: str, plan_id: str,
                   orig_lat: float | None = None, orig_lng: float | None = None):
    """Devuelve el deep link de Google Maps con ruta desde la ubicación actual
    del usuario hasta el lugar del plan. Si el lugar está en la curación,
    usa sus coordenadas exactas; si no, usa el nombre para que Maps lo geocodifique."""
    trip = db.get_trip(tid)
    if not trip:
        raise HTTPException(404, "trip no existe")
    planes = db.get_plans(tid)
    plan = next((p for p in planes if p.get("id") == plan_id), None)
    if not plan:
        raise HTTPException(404, "plan no existe")

    # Datos del destino desde la curación.
    # Probamos con el lugar Y con el título: a veces el extractor guarda un
    # lugar genérico ("Bogotá") pero el título trae el nombre real ("Cena en
    # El Cielo"). Nos quedamos con el match de mayor puntaje entre ambos.
    city = trip.get("ciudad", "")
    dest_lat, dest_lng = None, None
    dest_dir, dest_query = None, None
    candidatos_texto = [t for t in (plan.get("lugar"), plan.get("titulo")) if t and t.strip()]
    places = db.places_for_city(city)
    match = None
    for texto in candidatos_texto:
        m = db.best_place_match(texto, places)
        if m:
            match = m
            break
    if match:
        dest_lat, dest_lng = match["lat"], match["lng"]
        dest_dir = match.get("dir")
        dest_query = match.get("maps_query")
        dest_place_id = match.get("place_id")
    else:
        dest_place_id = None
    lugar_nombre = (plan.get("lugar") or plan.get("titulo") or "").strip()

    # Origen: parámetros de query > GPS guardado en trip > None
    olat = orig_lat or trip.get("lat_actual")
    olng = orig_lng or trip.get("lng_actual")

    dist_km = round(geo.haversine_km(olat, olng, dest_lat, dest_lng), 2) \
        if (olat and dest_lat) else None
    mode = geo.suggest_mode(dist_km)

    # Destino para el LINK: preferimos la query curada (nombre del negocio +
    # dirección), que Google resuelve al POI exacto. La dirección sola es el
    # respaldo. Las coordenadas guardadas NO se usan como destino porque pueden
    # estar mal; solo sirven para estimar distancia/tiempo.
    if dest_query or dest_dir or dest_place_id:
        destino_texto = dest_query or dest_dir
        link = geo.maps_link_from_to(olat, olng, dest_lat, dest_lng, mode=mode,
                                     maps_query=destino_texto, place_id=dest_place_id)
    else:
        # Lugar no curado: armamos la mejor query posible.
        # Elegimos entre lugar y título el texto con más "sustancia" (que tenga
        # tokens distintivos, no solo "Bogotá" o "cena"). Si ninguno la tiene,
        # caemos a búsqueda de la ciudad.
        mejor_texto = ""
        for texto in candidatos_texto:
            if db.place_tokens(texto):  # tiene al menos un token distintivo
                mejor_texto = texto
                break
        if mejor_texto:
            # Evitar duplicar la ciudad si ya está en el texto
            base = mejor_texto.strip()
            query = base if city.lower() in base.lower() else f"{base}, {city}"
            link = geo.maps_link_from_to(olat, olng, None, None, mode=mode,
                                         maps_query=query)
        else:
            link = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(city)}"

    minutos = geo.travel_minutes(dist_km, city) if dist_km else None

    return {"maps_link": link, "mode": mode,
            "dist_km": dist_km, "travel_min": minutos,
            "lugar": plan.get("lugar"), "titulo": plan.get("titulo")}


# ── Señales / watchers ──
@app.post("/trips/{tid}/signals")
def ingest_signal(tid: str, s: SignalIn):
    try:
        return engine.ingest(tid, s.source, s.category, s.operational, s.payload)
    except KeyError:
        raise HTTPException(404, "trip no existe")


@app.post("/webhooks/duffel/{tid}")
def duffel(tid: str, payload: dict):
    return watchers.duffel_webhook(tid, payload)


@app.post("/trips/{tid}/location")
def location(tid: str, loc: LocationIn):
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return watchers.update_location(tid, loc.zona, loc.disparar_geofence, lat=loc.lat, lng=loc.lng)


@app.post("/trips/{tid}/checkin")
def checkin(tid: str):
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return watchers.check_in(tid)


@app.post("/trips/{tid}/checkin-matutino")
def checkin_matutino(tid: str):
    """Check-in matutino. En producción lo dispara el scheduler ~8am hora local."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return watchers.check_in_matutino(tid)


@app.post("/scheduler/tick")
def scheduler_tick():
    """Dispara un ciclo del scheduler manualmente (dev/debug). En producción
    corre solo cada pocos minutos. Respeta el dedup: no repite lo ya enviado hoy."""
    return scheduler.tick()


# ── Web Push (VAPID) ──
@app.get("/push/public-key")
def push_public_key():
    """Clave pública VAPID que el frontend necesita para suscribirse.
    Si no hay claves configuradas, enabled=False y el front no ofrece push."""
    return {"enabled": push.enabled(), "public_key": push.public_key()}


@app.post("/trips/{tid}/push/subscribe")
def push_subscribe(tid: str, body: PushSubIn):
    """Guarda la suscripción push del navegador para este viaje."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    push.save_subscription(tid, body.subscription)
    return {"ok": True}


@app.post("/trips/{tid}/push/unsubscribe")
def push_unsubscribe(tid: str, body: PushSubIn):
    """Elimina una suscripción (cuando el usuario desactiva las notificaciones)."""
    endpoint = body.subscription.get("endpoint")
    if endpoint:
        push.delete_subscription(endpoint)
    return {"ok": True}


@app.post("/trips/{tid}/push/test")
def push_test(tid: str):
    """Manda una notificación de prueba a los dispositivos suscritos del viaje."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return push.send_to_trip(tid, "Voyra 🛟", "¡Las notificaciones funcionan! Te avisaré de lo que importe.",
                             data={"kind": "test"})


@app.post("/trips/{tid}/scan")
def scan(tid: str):
    """Dispara el News watcher + Destination Scanner reales (Tavily) para este viaje."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return watchers.scan_destination(tid)


@app.post("/trips/{tid}/nearby")
def nearby(tid: str):
    """Recomendaciones desde la base curada, rankeadas por distancia real a zona_actual."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return watchers.nearby_recommendations(tid)


@app.get("/trips/{tid}/nearby-chain")
def nearby_chain(tid: str, q: str, orig_lat: float | None = None,
                 orig_lng: float | None = None, radio_m: int = 2500):
    """Búsqueda EN VIVO de cadenas/servicios cerca del usuario (Carulla, cajero,
    farmacia, etc.). Consulta Google Places al momento — NO usa la curación.

    Pasa orig_lat/orig_lng (GPS del usuario) o usa la ubicación guardada del trip.
    """
    from . import places_live
    trip = db.get_trip(tid)
    if not trip:
        raise HTTPException(404, "trip no existe")
    olat = orig_lat or trip.get("lat_actual")
    olng = orig_lng or trip.get("lng_actual")
    if olat is None or olng is None:
        # sin GPS, intenta con coords de la zona conocida
        coords = geo.zone_coords(trip.get("ciudad", ""), trip.get("zona_actual", ""))
        if coords:
            olat, olng = coords
    if olat is None or olng is None:
        return {"disponible": places_live.disponible(), "resultados": [],
                "nota": "No tengo tu ubicación. Comparte tu GPS para buscar cerca."}
    resultados = places_live.buscar_cerca(q, olat, olng, radio_m=radio_m)
    return {"disponible": places_live.disponible(), "query": q,
            "resultados": resultados,
            "nota": None if resultados else (
                "No encontré nada cerca." if places_live.disponible()
                else "Búsqueda en vivo no configurada (falta GOOGLE_MAPS_API_KEY).")}


@app.get("/trips/{tid}/airport")
def airport_arrival(tid: str):
    """Modo aeropuerto: timeline de llegada (migración → equipaje → salida) +
    opciones de transporte con tips anti-estafa, curado por aeropuerto y con
    tiempos en vivo si hay búsqueda configurada."""
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    return watchers.airport_arrival(tid)


@app.get("/destinations/{city}/places")
def destination_places(city: str):
    """Inspecciona la base curada para una ciudad (debug / verificación)."""
    return db.places_for_city(city)


@app.get("/_diag")
def diagnostico():
    """Diagnóstico rápido del estado de la DB en producción.

    Úsalo para verificar tras un deploy si la curación quedó correcta. P. ej.
    en Railway abre  https://TU-APP.up.railway.app/_diag  y revisa que
    'el_cielo' apunte a Calle 70 / Zona G, no a coordenadas viejas.
    """
    import os
    places = db.places_for_city("Bogotá")
    cielo = next((p for p in places if "cielo" in p["name"].lower()), None)
    return {
        "db_path": db.DB_PATH,
        "db_path_es_volumen": bool(os.path.dirname(db.DB_PATH)),
        "total_lugares_bogota": len(places),
        "el_cielo": {
            "name": cielo["name"],
            "lat": cielo["lat"],
            "lng": cielo["lng"],
            "zona": cielo["zona"],
            "dir": cielo.get("dir"),
            "maps_query": cielo.get("maps_query"),
        } if cielo else None,
        "ok": bool(cielo and "70" in (cielo.get("dir") or "") and 4.64 < cielo["lat"] < 4.66),
    }


# ── Chat ──
@app.post("/trips/{tid}/chat")
def chat(tid: str, c: ChatIn):
    trip = db.get_trip(tid)
    if not trip:
        raise HTTPException(404, "trip no existe")
    db.insert("messages", {"trip_id": tid, "role": "user", "content": c.message})
    history = [{"role": m["role"], "content": m["content"]} for m in db.rows("messages", tid)]
    reply = llm.chat(context.build(trip), history)
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": reply})
    # Detectar planes en el mensaje del usuario SIN guardarlos: el frontend
    # preguntará "¿lo anoto en tu calendario?" antes de persistir.
    propuestas = []
    # Los mensajes entre corchetes son taps de notificaciones, no planes.
    if not c.message.strip().startswith("["):
        from . import plans as plans_mod, timeutil
        propuestas = plans_mod.normalizar_lista(
            llm.extract_plans_from_chat(c.message, trip), origen="chat")
        # No dejar la ciudad como "lugar" (rompe el match y manda a sitios random)
        propuestas = plans_mod.limpiar_lugar_ciudad(propuestas, trip.get("ciudad", ""))
        # Si el plan coincide con un lugar curado, corrige tipo (ícono) y lugar
        propuestas = plans_mod.enriquecer_con_curacion(
            propuestas, db.places_for_city(trip.get("ciudad", "")), db.best_place_match)
        # Red de seguridad: si el LLM no puso fecha pero el mensaje dice
        # "mañana", "el viernes", etc., la deducimos aquí.
        try:
            ahora = timeutil.now_local(trip)
            propuestas = plans_mod.rellenar_fechas(propuestas, c.message, ahora)
        except Exception:
            pass
    # Búsqueda en vivo de cadenas: detectar si el usuario pide algo cerca.
    chain_results = None
    from . import places_live
    cadena_q = places_live.detectar_busqueda_cadena(c.message)
    if cadena_q and places_live.disponible():
        olat = trip.get("lat_actual")
        olng = trip.get("lng_actual")
        if olat is None or olng is None:
            coords = geo.zone_coords(trip.get("ciudad", ""), trip.get("zona_actual", ""))
            if coords:
                olat, olng = coords
        if olat and olng:
            resultados = places_live.buscar_cerca(cadena_q, olat, olng)
            if resultados:
                chain_results = {"query": cadena_q, "items": resultados}

    return {"reply": reply, "planes_propuestos": propuestas, "chain_results": chain_results}


# ── Documentos ──
@app.post("/trips/{tid}/documents")
def upload_doc(tid: str, d: DocIn):
    trip = db.get_trip(tid)
    if not trip:
        raise HTTPException(404, "trip no existe")
    if d.pdf_data_url:
        from . import docparse
        texto = docparse.pdf_b64_to_text(d.pdf_data_url)
        if texto:
            r = llm.extract_document(texto, d.filename, trip)
        else:
            # PDF escaneado (sin texto): no hay capa de texto que leer.
            r = {"tipo": "otro",
                 "resumen": f"PDF {d.filename} recibido, pero no tiene texto legible (parece escaneado).",
                 "confirmacion": f"Guardé {d.filename}. Si es una foto/escaneo, súbela como imagen para leerla mejor.",
                 "planes": []}
    elif d.image_data_url:
        r = llm.extract_document_image(d.image_data_url, d.filename, trip)
    else:
        r = llm.extract_document(d.text_content, d.filename, trip)
    db.insert("documents", {"trip_id": tid, "filename": d.filename,
                            "doc_type": r["tipo"], "summary": r["resumen"]})
    # Si el documento reveló datos del viaje (ciudad, hotel, fechas, vuelos),
    # rellenamos SOLO los campos que el usuario aún no tiene puestos. Nunca
    # sobrescribimos lo que ya existe.
    ti = r.get("trip_info") or {}
    patch = {}
    for campo, clave in (("ciudad", "ciudad"), ("hotel", "hotel"),
                         ("inicio", "inicio"), ("fin", "fin"),
                         ("vuelo_ida", "vuelo_ida"), ("vuelo_regreso", "vuelo_regreso")):
        val = (ti.get(clave) or "").strip() if isinstance(ti.get(clave), str) else ti.get(clave)
        if val and not (trip.get(campo) or "").strip():
            patch[campo] = val
    if patch:
        db.update_trip(tid, patch)
    r["trip_info_aplicado"] = patch
    r["trip"] = db.get_trip(tid)
    # Normaliza los planes detectados pero NO los guarda aún: el frontend
    # muestra "encontré estos planes, ¿los agrego a tu calendario?".
    from . import plans as plans_mod
    propuestas = plans_mod.normalizar_lista(r.get("planes", []), origen="documento")
    propuestas = plans_mod.limpiar_lugar_ciudad(propuestas, trip.get("ciudad", ""))
    propuestas = plans_mod.enriquecer_con_curacion(
        propuestas, db.places_for_city(trip.get("ciudad", "")), db.best_place_match)
    r["planes_propuestos"] = propuestas
    return r


# ── Notificaciones y feedback ──
@app.get("/trips/{tid}/notifications")
def notifications(tid: str):
    rows = db.rows("notifications", tid)
    for r in rows:
        if r.get("extra"):
            r.update(json.loads(r.pop("extra")))
        else:
            r.pop("extra", None)
    return rows


@app.post("/notifications/{nid}/feedback")
def feedback(nid: str, f: FeedbackIn):
    try:
        return engine.feedback(nid, f.feedback)
    except KeyError:
        raise HTTPException(404, "notificación no existe")


# ── Dataset (el log de decisiones que acordamos guardar desde el día uno) ──
@app.get("/trips/{tid}/decisions")
def decisions(tid: str):
    return db.rows("decisions", tid)


@app.get("/health")
def health():
    from . import places_live
    return {"status": "ok", "llm_mode": llm.MODE, "search_mode": search.MODE,
            "push_enabled": push.enabled(), "places_live": places_live.MODE}


# ── Frontend / PWA ──
@app.get("/")
def frontend():
    """Sirve companion-agente.html desde la raíz del repo (mismo dominio que
    la API). Necesario para que navigator.geolocation funcione en el celular
    — el navegador exige contexto seguro (HTTPS o localhost); abrir el HTML
    como archivo local o desde otro dominio no cumple eso."""
    return FileResponse("companion-agente.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
def pwa_manifest():
    """Manifest de la PWA. Los íconos van embebidos como data-URI dentro del
    archivo, así que no se necesita una carpeta de imágenes ni rutas extra."""
    return FileResponse("manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Service worker servido desde la raíz para controlar todo el scope "/".
    'Cache-Control: no-cache' hace que el navegador detecte nuevas versiones
    del SW al recargar; 'Service-Worker-Allowed: /' fija el scope explícito."""
    return FileResponse(
        "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )
