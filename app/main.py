"""API del Companion en destino — FastAPI.

Correr:  uvicorn app.main:app --reload
Docs:    http://localhost:8000/docs
"""
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import context, db, engine, llm, search, seed_data, watchers

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


# ── Schemas ──
class TripIn(BaseModel):
    ciudad: str
    hotel: str
    inicio: str
    fin: str
    vuelo_ida: str = ""
    vuelo_regreso: str = ""
    pais: str = "Colombia"
    gustos: list[str] = Field(default_factory=list)


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
    text_content: str  # MVP: texto del documento (OCR/parse en el cliente). Multimodal directo: fase 2.


class FeedbackIn(BaseModel):
    feedback: str  # tapped | dismissed | not_interested


class PlanIn(BaseModel):
    plan: str


# ── Trips ──
@app.post("/trips")
def create_trip(t: TripIn):
    tid = db.create_trip(t.model_dump())
    saludo = (f"¡Listo! Ya estoy vigilando tu vuelo, el clima y lo que se mueve en {t.ciudad}. "
              "Para avisarte solo de lo que te sirve: ¿qué planes tienes para hoy y mañana?")
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": saludo})
    return {"trip_id": tid, "greeting": saludo}


@app.get("/trips/{tid}")
def get_trip(tid: str):
    t = db.get_trip(tid)
    if not t:
        raise HTTPException(404, "trip no existe")
    return t


@app.post("/trips/{tid}/plans")
def add_plan(tid: str, p: PlanIn):
    t = db.get_trip(tid)
    if not t:
        raise HTTPException(404, "trip no existe")
    db.update_trip(tid, {"planes": t.get("planes", []) + [p.plan]})
    return {"planes": db.get_trip(tid)["planes"]}


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
    return {"reply": reply}


# ── Documentos ──
@app.post("/trips/{tid}/documents")
def upload_doc(tid: str, d: DocIn):
    if not db.get_trip(tid):
        raise HTTPException(404, "trip no existe")
    r = llm.extract_document(d.text_content, d.filename)
    db.insert("documents", {"trip_id": tid, "filename": d.filename,
                            "doc_type": r["tipo"], "summary": r["resumen"]})
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
    return {"status": "ok", "llm_mode": llm.MODE, "search_mode": search.MODE}


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
