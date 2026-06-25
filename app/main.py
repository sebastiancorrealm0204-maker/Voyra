"""API del Companion en destino — FastAPI.

Correr:  uvicorn app.main:app --reload
Docs:    http://localhost:8000/docs
"""
import json
import os
import urllib.parse

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import (context, db, engine, geo, llm, push, scheduler, search, seed_data,
               watchers, auth, limits, email_send, social_enrich)

app = FastAPI(title="Voyra Companion", version="0.2.0")

# CORS: permite que el frontend (corriendo en el navegador, ej. un Artifact de
# Claude) llame a este backend en localhost. En producción, reemplaza "*" por
# el dominio real del frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db.init_pool()       # abre el ConnectionPool de Postgres (no-op en modo SQLite)
db.init_db()
auth.init_auth()
limits.init_limits()
db.seed_destination_places(seed_data.all_seeds())


# ── Dependencias de autenticación ──
def _token_from_header(authorization: str | None) -> str:
    """Extrae el token de 'Authorization: Bearer <token>'."""
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    """Usuario autenticado por su token de sesión. 401 si no hay sesión válida."""
    user = auth.user_for_session(_token_from_header(authorization))
    if not user:
        raise HTTPException(401, "No autenticado. Inicia sesión.")
    return user


def verified_user(user: dict = Depends(current_user)) -> dict:
    """Usuario autenticado Y con email verificado. Exigido para endpoints caros."""
    if not user.get("email_verified"):
        raise HTTPException(403, "Verifica tu email para usar esta función. "
                                 "Revisa tu correo o pide un nuevo enlace.")
    return user


def admin_guard(x_admin_token: str | None = Header(default=None)) -> None:
    """Protege la cola de revisión de enriquecimientos. Como aún no hay rol de
    admin en el modelo de usuario, gateamos con un token de entorno
    (VOYRA_ADMIN_TOKEN). Si no está configurado, los endpoints quedan cerrados."""
    esperado = os.environ.get("VOYRA_ADMIN_TOKEN", "")
    if not esperado or (x_admin_token or "").strip() != esperado:
        raise HTTPException(403, "Acceso de administrador requerido.")


def own_trip(tid: str, user: dict) -> dict:
    """Carga el trip y verifica que sea del usuario. 404 si no existe, 403 si no es suyo."""
    trip = db.get_trip(tid)
    if not trip:
        raise HTTPException(404, "trip no existe")
    owner = db.trip_owner(tid)
    # Trips legacy sin dueño: se permiten (compat con datos previos a auth).
    if owner is not None and owner != user["id"]:
        raise HTTPException(403, "Este viaje no es tuyo.")
    return trip


def _consume_or_429(user: dict, resource: str):
    """Aplica cuota; convierte QuotaError en HTTP 429 con detalle para el front."""
    try:
        limits.check_and_consume(user["id"], resource)
    except limits.QuotaError as e:
        if e.global_block:
            raise HTTPException(429, {
                "error": "global_limit",
                "resource": resource,
                "mensaje": "La búsqueda en vivo está temporalmente pausada por alta demanda. "
                           "Vuelve a intentar más tarde."})
        raise HTTPException(429, {
            "error": "quota",
            "resource": resource,
            "limite": e.limit,
            "usado": e.used,
            "mensaje": f"Alcanzaste tu límite diario de {e.limit} para esta función. "
                       "Se renueva mañana."})


@app.on_event("startup")
def _arrancar_scheduler():
    """Arranca el scheduler proactivo (check-ins por hora local del destino).
    Si APScheduler no está instalado, la app sigue normal sin modo proactivo."""
    scheduler.start()


@app.on_event("shutdown")
def _cerrar_pool():
    """Cierra el ConnectionPool de Postgres al apagar la app (no-op en SQLite)."""
    db.close_pool()


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
    idioma: str = "es"            # idioma del usuario (es | en | pt | fr) — en qué le habla el Companion


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
    idioma: str | None = None
    # True una vez que el usuario vio/cerró la MiniGuia de bienvenida de ESTE
    # viaje. Por viaje (no por usuario): cada viaje nuevo la muestra una vez,
    # pero reabrir el mismo viaje —en este dispositivo o en otro— no la repite.
    guia_vista: bool | None = None


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


# ── Auth schemas ──
class SignupIn(BaseModel):
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


class ResendIn(BaseModel):
    email: str


# ── Auth endpoints ──
@app.post("/auth/signup")
def signup(body: SignupIn, request: Request):
    """Registro. Crea el usuario SIN verificar y envía el correo de verificación.
    No inicia sesión hasta que el email esté verificado (anti-abuso)."""
    ip = request.client.host if request.client else "unknown"
    if auth.ip_signup_blocked(ip):
        raise HTTPException(429, "Demasiados registros desde esta red. Intenta más tarde.")

    email = auth.normalize_email(body.email)
    if not auth.valid_email(email):
        raise HTTPException(400, "Email inválido.")
    ok, msg = auth.valid_password(body.password)
    if not ok:
        raise HTTPException(400, msg)
    if auth.get_user_by_email(email):
        raise HTTPException(409, "Ese email ya está registrado. Inicia sesión.")

    auth.record_signup_attempt(ip)
    user = auth.create_user(email, body.password, ip=ip)
    token = auth.create_verification_token(user["id"])
    res = email_send.send_verification(email, token)

    out = {"ok": True, "email": email,
           "mensaje": "Te enviamos un correo para confirmar tu email.",
           "email_enviado": res.get("sent", False), "email_mode": res.get("mode")}
    # En modo dev (sin proveedor de correo), devolvemos el enlace para probar.
    if res.get("dev_link"):
        out["dev_verification_link"] = res["dev_link"]
    return out


@app.get("/auth/verify")
def verify_email(token: str):
    """Confirma el email desde el enlace del correo. Devuelve una página simple."""
    user = auth.consume_verification_token(token)
    if not user:
        return HTMLResponse(
            "<div style='font-family:system-ui;text-align:center;padding:48px'>"
            "<h2>Enlace inválido o vencido</h2>"
            "<p>Pide un nuevo enlace de verificación desde la app.</p></div>",
            status_code=400)
    return HTMLResponse(
        "<div style='font-family:system-ui;text-align:center;padding:48px'>"
        "<h2>✅ ¡Email confirmado!</h2>"
        "<p>Ya puedes volver a la app e iniciar sesión.</p></div>")


@app.post("/auth/resend-verification")
def resend_verification(body: ResendIn):
    """Reenvía el correo de verificación. Respuesta genérica para no filtrar
    qué emails existen."""
    email = auth.normalize_email(body.email)
    user = auth.get_user_by_email(email)
    out = {"ok": True, "mensaje": "Si ese email existe y no está verificado, te reenviamos el enlace."}
    if user and not user["email_verified"]:
        token = auth.create_verification_token(user["id"])
        res = email_send.send_verification(email, token)
        if res.get("dev_link"):
            out["dev_verification_link"] = res["dev_link"]
    return out


@app.post("/auth/login")
def login(body: LoginIn):
    """Inicia sesión. Exige email verificado. Devuelve un token de sesión."""
    user = auth.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(401, "Email o contraseña incorrectos.")
    if not user["email_verified"]:
        raise HTTPException(403, {"error": "email_no_verificado",
                                  "mensaje": "Confirma tu email antes de entrar. "
                                             "Revisa tu correo o pide otro enlace."})
    token = auth.create_session(user["id"])
    return {"token": token, "user": {"id": user["id"], "email": user["email"],
                                     "plan": user["plan"]}}


@app.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    auth.destroy_session(_token_from_header(authorization))
    return {"ok": True}


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    """Datos del usuario + perfil global + cuánto le queda de cada cuota hoy."""
    return {"id": user["id"], "email": user["email"], "plan": user["plan"],
            "email_verified": bool(user["email_verified"]),
            "perfil": auth.get_profile(user["id"]),
            "cuotas": limits.usage_summary(user["id"]),
            "trips": {"usados": limits.trips_count(user["id"]),
                      "limite": limits.effective_trip_limit(user["id"])}}


class ProfileIn(BaseModel):
    nombre: str = ""
    telefono: str = ""
    contacto_emergencia: str = ""
    alergias: str = ""
    condiciones_medicas: str = ""
    preferencias: str = ""
    pasaporte: str = ""
    notas: str = ""


@app.get("/profile")
def get_profile(user: dict = Depends(current_user)):
    """Perfil global del viajero (datos de texto reutilizables en todos los viajes)."""
    return {"perfil": auth.get_profile(user["id"])}


@app.put("/profile")
def put_profile(p: ProfileIn, user: dict = Depends(current_user)):
    """Guarda el perfil global. Solo el propio usuario edita su perfil."""
    return {"perfil": auth.set_profile(user["id"], p.model_dump())}


# ── Trips ──
def _geocodificar_hotel_bg(tid: str, nombre_hotel: str, ciudad: str, pais: str = "") -> None:
    """Lanza la geocodificación del hotel en un thread daemon para no bloquear
    la respuesta del onboarding. Cuando Google responde, guarda lat_hotel,
    lng_hotel y place_id_hotel en el trip. Si falla (sin key, hotel no
    encontrado, timeout), no hace nada — el trip sigue funcionando sin estos
    campos, solo pierde la detección 'estás en tu hotel'."""
    import threading

    def _run():
        try:
            coords = places_live.geocodificar_hotel(nombre_hotel, ciudad, pais)
            if coords and coords.get("lat") and coords.get("lng"):
                db.update_trip(tid, {
                    "lat_hotel": coords["lat"],
                    "lng_hotel": coords["lng"],
                    "place_id_hotel": coords.get("place_id", ""),
                    "dir_hotel": coords.get("dir", ""),
                    "google_name_hotel": coords.get("google_name", ""),
                })
        except Exception:
            pass  # silencioso — el trip sigue funcionando sin coordenadas del hotel

    threading.Thread(target=_run, daemon=True).start()


@app.post("/trips")
def create_trip(t: TripIn, user: dict = Depends(verified_user)):
    if not limits.can_create_trip(user["id"]):
        tope = limits.effective_trip_limit(user["id"])
        raise HTTPException(429, {
            "error": "trip_limit",
            "limite": tope,
            "mensaje": f"Tu plan permite {tope} viajes activos. "
                       "Borra uno para crear otro."})
    tid = db.create_trip(t.model_dump(), user_id=user["id"])
    _idi = (t.idioma or "es").lower()
    _saludos = {
        "es": (f"¡Listo! Ya estoy vigilando tu vuelo, el clima y lo que se mueve en {t.ciudad}. "
               "Para avisarte solo de lo que te sirve: ¿qué planes tienes para hoy y mañana?",
               "¡Hola! Soy tu Companion. Sube tus reservas (vuelo, hotel, tours) y armo tu "
               "viaje contigo. También puedes contarme a dónde vas y cuándo."),
        "en": (f"All set! I'm already watching your flight, the weather and what's happening in {t.ciudad}. "
               "So I only ping you with what's useful: what are your plans for today and tomorrow?",
               "Hi! I'm your Companion. Upload your bookings (flight, hotel, tours) and I'll build your "
               "trip with you. You can also just tell me where you're going and when."),
        "pt": (f"Pronto! Já estou de olho no seu voo, no clima e no que rola em {t.ciudad}. "
               "Para te avisar só do que importa: quais são seus planos para hoje e amanhã?",
               "Oi! Sou o seu Companion. Suba suas reservas (voo, hotel, passeios) e eu monto sua "
               "viagem com você. Você também pode só me dizer para onde vai e quando."),
        "fr": (f"C'est prêt ! Je surveille déjà ton vol, la météo et ce qui se passe à {t.ciudad}. "
               "Pour ne t'alerter que de l'utile : quels sont tes plans pour aujourd'hui et demain ?",
               "Salut ! Je suis ton Companion. Ajoute tes réservations (vol, hôtel, activités) et je "
               "construis ton voyage avec toi. Tu peux aussi simplement me dire où tu vas et quand."),
    }
    con_ciudad, sin_ciudad = _saludos.get(_idi, _saludos["es"])
    saludo = con_ciudad if t.ciudad.strip() else sin_ciudad
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": saludo})

    # Geocodificar el hotel en background para no demorar la respuesta del
    # onboarding. Cuando termine, guarda lat_hotel/lng_hotel/place_id_hotel en
    # el trip — el Companion los usa para detectar si el usuario está en su hotel.
    if t.hotel.strip() and t.ciudad.strip():
        _geocodificar_hotel_bg(tid, t.hotel, t.ciudad, t.pais)

    return {"trip_id": tid, "greeting": saludo, "trip": db.get_trip(tid)}


@app.get("/trips")
def list_my_trips(user: dict = Depends(current_user)):
    """Lista los viajes del usuario autenticado."""
    with db.conn() as c:
        rs = c.execute("SELECT id FROM trips WHERE user_id=? ORDER BY created_at DESC",
                       (user["id"],)).fetchall()
    return {"trips": [db.get_trip(r["id"]) for r in rs]}


@app.patch("/trips/{tid}")
def patch_trip(tid: str, p: TripPatchIn, user: dict = Depends(current_user)):
    own_trip(tid, user)
    patch = {k: v for k, v in p.model_dump().items() if v is not None}
    if patch:
        db.update_trip(tid, patch)
    # Si se actualizó el hotel o la ciudad, re-geocodificar el hotel en background.
    trip = db.get_trip(tid)
    if (p.hotel is not None or p.ciudad is not None) and trip.get("hotel") and trip.get("ciudad"):
        _geocodificar_hotel_bg(tid, trip["hotel"], trip["ciudad"], trip.get("pais", ""))
    return trip


@app.get("/trips/{tid}")
def get_trip(tid: str, user: dict = Depends(current_user)):
    return own_trip(tid, user)


@app.delete("/trips/{tid}")
def delete_trip(tid: str, user: dict = Depends(current_user)):
    """Borra un viaje del usuario (libera un cupo de trips activos)."""
    own_trip(tid, user)
    with db.conn() as c:
        c.execute("DELETE FROM trips WHERE id=?", (tid,))
    return {"ok": True}


@app.get("/trips/{tid}/plans")
def list_plans(tid: str, user: dict = Depends(current_user)):
    """Planes del viaje: lista plana ordenada + agrupados por día (para el calendario)."""
    own_trip(tid, user)
    from . import plans as plans_mod
    planes = db.get_plans(tid)
    return {"planes": plans_mod.ordenar(planes),
            "por_dia": plans_mod.agrupar_por_dia(planes)}


@app.post("/trips/{tid}/plans")
def add_plan(tid: str, p: PlanIn, user: dict = Depends(current_user)):
    own_trip(tid, user)
    # Compat: si llega 'plan' como texto suelto, se guarda como antes.
    if p.plan and not (p.titulo or p.fecha):
        nuevo = {"titulo": p.plan}
    else:
        nuevo = {"fecha": p.fecha, "hora": p.hora, "titulo": p.titulo or p.plan or "Plan",
                 "tipo": p.tipo, "detalle": p.detalle, "lugar": p.lugar}
    return {"planes": db.add_plans(tid, [nuevo], origen="manual")}


@app.post("/trips/{tid}/plans/confirm")
def confirm_plans(tid: str, body: PlansConfirmIn, user: dict = Depends(current_user)):
    """Guarda los planes que el chat o un documento propuso (tras confirmación del usuario)."""
    own_trip(tid, user)
    return {"planes": db.add_plans(tid, body.planes, origen="chat")}


@app.delete("/trips/{tid}/plans/{plan_id}")
def remove_plan(tid: str, plan_id: str, user: dict = Depends(current_user)):
    own_trip(tid, user)
    return {"planes": db.delete_plan(tid, plan_id)}


@app.get("/trips/{tid}/plans/{plan_id}/maps")
def plan_maps_link(tid: str, plan_id: str,
                   orig_lat: float | None = None, orig_lng: float | None = None,
                   user: dict = Depends(current_user)):
    """Devuelve el deep link de Google Maps con ruta desde la ubicación actual
    del usuario hasta el lugar del plan. Si el lugar está en la curación,
    usa sus coordenadas exactas; si no, usa el nombre para que Maps lo geocodifique."""
    trip = own_trip(tid, user)
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
def ingest_signal(tid: str, s: SignalIn, user: dict = Depends(current_user)):
    own_trip(tid, user)
    try:
        return engine.ingest(tid, s.source, s.category, s.operational, s.payload)
    except KeyError:
        raise HTTPException(404, "trip no existe")


@app.post("/webhooks/duffel/{tid}")
def duffel(tid: str, payload: dict):
    # Webhook de proveedor: no lleva sesión de usuario. Se deja sin auth de
    # usuario a propósito (en producción, validar firma del webhook de Duffel).
    return watchers.duffel_webhook(tid, payload)


@app.post("/trips/{tid}/location")
def location(tid: str, loc: LocationIn, user: dict = Depends(current_user)):
    own_trip(tid, user)
    return watchers.update_location(tid, loc.zona, loc.disparar_geofence, lat=loc.lat, lng=loc.lng)


@app.post("/trips/{tid}/checkin")
def checkin(tid: str, user: dict = Depends(current_user)):
    own_trip(tid, user)
    return watchers.check_in(tid)


@app.post("/trips/{tid}/checkin-matutino")
def checkin_matutino(tid: str, user: dict = Depends(current_user)):
    """Check-in matutino. En producción lo dispara el scheduler ~8am hora local."""
    own_trip(tid, user)
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
def push_subscribe(tid: str, body: PushSubIn, user: dict = Depends(current_user)):
    """Guarda la suscripción push del navegador para este viaje."""
    own_trip(tid, user)
    push.save_subscription(tid, body.subscription)
    return {"ok": True}


@app.post("/trips/{tid}/push/unsubscribe")
def push_unsubscribe(tid: str, body: PushSubIn, user: dict = Depends(current_user)):
    """Elimina una suscripción (cuando el usuario desactiva las notificaciones)."""
    own_trip(tid, user)
    endpoint = body.subscription.get("endpoint")
    if endpoint:
        push.delete_subscription(endpoint)
    return {"ok": True}


@app.post("/trips/{tid}/push/test")
def push_test(tid: str, user: dict = Depends(current_user)):
    """Manda una notificación de prueba a los dispositivos suscritos del viaje."""
    own_trip(tid, user)
    return push.send_to_trip(tid, "Voyra 🛟", "¡Las notificaciones funcionan! Te avisaré de lo que importe.",
                             data={"kind": "test"})


@app.post("/trips/{tid}/scan")
def scan(tid: str, user: dict = Depends(verified_user)):
    """Dispara el News watcher + Destination Scanner reales (Tavily) para este viaje."""
    own_trip(tid, user)
    _consume_or_429(user, "scan")  # Tavily se paga/limita
    return watchers.scan_destination(tid)


@app.post("/trips/{tid}/nearby")
def nearby(tid: str, user: dict = Depends(current_user)):
    """Recomendaciones desde la base curada, rankeadas por distancia real a zona_actual.
    Usa SOLO la curación local (sin costo de API), así que no consume cuota."""
    own_trip(tid, user)
    return watchers.nearby_recommendations(tid)


@app.get("/trips/{tid}/nearby-chain")
def nearby_chain(tid: str, q: str, orig_lat: float | None = None,
                 orig_lng: float | None = None, radio_m: int = 2500,
                 user: dict = Depends(verified_user)):
    """Búsqueda EN VIVO de cadenas/servicios cerca del usuario (Carulla, cajero,
    farmacia, etc.). Consulta Google Places al momento — NO usa la curación.

    Pasa orig_lat/orig_lng (GPS del usuario) o usa la ubicación guardada del trip.
    """
    from . import places_live
    trip = own_trip(tid, user)
    olat = orig_lat or trip.get("lat_actual")
    olng = orig_lng or trip.get("lng_actual")
    if olat is None or olng is None:
        # sin GPS, intenta con el hotel geocodificado o la zona conocida
        coords = geo.resolve_origin(trip)
        if coords:
            olat, olng = coords
    if olat is None or olng is None:
        return {"disponible": places_live.disponible(), "resultados": [],
                "nota": "No tengo tu ubicación. Comparte tu GPS para buscar cerca."}
    # Solo consumimos cuota si de verdad vamos a pegarle a Google (hay key y GPS).
    if places_live.disponible():
        _consume_or_429(user, "maps")
    resultados = places_live.buscar_cerca(q, olat, olng, radio_m=radio_m)
    return {"disponible": places_live.disponible(), "query": q,
            "resultados": resultados,
            "restante_maps": limits.remaining(user["id"], "maps"),
            "nota": None if resultados else (
                "No encontré nada cerca." if places_live.disponible()
                else "Búsqueda en vivo no configurada (falta GOOGLE_MAPS_API_KEY).")}


@app.get("/trips/{tid}/airport")
def airport_arrival(tid: str, user: dict = Depends(current_user)):
    """Modo aeropuerto: timeline de llegada (migración → equipaje → salida) +
    opciones de transporte con tips anti-estafa, curado por aeropuerto y con
    tiempos en vivo si hay búsqueda configurada."""
    own_trip(tid, user)
    return watchers.airport_arrival(tid)


@app.get("/destinations/{city}/places")
def destination_places(city: str):
    """Inspecciona la base curada para una ciudad (debug / verificación)."""
    return db.places_for_city(city)


# ════════════════════ Enriquecimiento social (cola de revisión, admin) ════════════════════
# Pipeline TikTok/IG → campo `dato`. Submit extrae un candidato y lo encola; el
# fundador revisa y aprueba. Nada se publica sin aprobación. Gateado por VOYRA_ADMIN_TOKEN.

class EnrichSubmitIn(BaseModel):
    city: str
    place_name: str           # debe coincidir EXACTO con destination_places.name
    source_text: str          # caption / transcripción de la pieza social
    source_type: str = "manual"   # tiktok | instagram | creator | manual
    source_url: str | None = None


@app.post("/admin/enrich/submit")
def enrich_submit(body: EnrichSubmitIn, _: None = Depends(admin_guard)):
    """Destila un dato local del texto social y, si pasa el umbral, lo encola
    como 'pending'. Devuelve el resultado de la extracción (incluido el motivo de
    descarte si no se encoló) para que el operador entienda la decisión."""
    return social_enrich.submit(
        body.place_name, body.city, body.source_text,
        source_type=body.source_type, source_url=body.source_url,
    )


@app.get("/admin/enrich/pending")
def enrich_pending(city: str | None = None, _: None = Depends(admin_guard)):
    """Cola de mejoras en revisión (opcionalmente por ciudad)."""
    return {"pending": db.list_enrichments(status="pending", city=city)}


@app.post("/admin/enrich/{eid}/approve")
def enrich_approve(eid: str, _: None = Depends(admin_guard)):
    """Aprueba una mejora: pasa a superponerse en las recomendaciones al instante."""
    if not db.set_enrichment_status(eid, "approved"):
        raise HTTPException(404, "Mejora no encontrada.")
    return {"ok": True, "status": "approved"}


@app.post("/admin/enrich/{eid}/reject")
def enrich_reject(eid: str, _: None = Depends(admin_guard)):
    """Rechaza una mejora: no se mostrará nunca."""
    if not db.set_enrichment_status(eid, "rejected"):
        raise HTTPException(404, "Mejora no encontrada.")
    return {"ok": True, "status": "rejected"}


@app.get("/_diag")
def diagnostico():
    """Diagnóstico rápido del estado de la DB en producción."""
    import os
    places = db.places_for_city("Bogotá")
    cielo = next((p for p in places if "cielo" in p["name"].lower()), None)
    tavola = next((p for p in places if "tavola" in p["name"].lower()), None)

    # Estado de la tabla place_enrichments
    try:
        enrichments = db.list_enrichments(city="Bogotá", limit=50)
        enrich_state = {
            "tabla_existe": True,
            "total": len(enrichments),
            "aprobados": [e for e in enrichments if e["status"] == "approved"],
            "pendientes": len([e for e in enrichments if e["status"] == "pending"]),
        }
    except Exception as ex:
        enrich_state = {"tabla_existe": False, "error": str(ex)}

    return {
        "db_path": db.DB_PATH,
        "total_lugares_bogota": len(places),
        "tavola": {
            "encontrado": bool(tavola),
            "name": tavola["name"] if tavola else None,
            "local_keys": list((tavola.get("local") or {}).keys()) if tavola else None,
            "dato_social": (tavola.get("local") or {}).get("dato_social") if tavola else None,
        } if tavola else {"encontrado": False},
        "enrichments_bogota": enrich_state,
        "el_cielo_ok": bool(cielo and "70" in (cielo.get("dir") or "") and 4.64 < cielo["lat"] < 4.66),
    }


@app.get("/_diag_prompt")
def diag_prompt(q: str = "cuéntame de La Tavola"):
    """Devuelve la LÍNEA EXACTA del prompt para el lugar mencionado en q.

    Es la fuente de verdad: muestra lo que el modelo realmente ve. Si aquí
    aparece 'DATO LOCAL (vía tiktok)', el dato SÍ llega al prompt y el problema
    sería del modelo; si NO aparece, el problema es de datos/código.
    """
    trip = {
        "id": "diag", "ciudad": "Bogotá", "hotel": "—",
        "inicio": "2026-07-01", "fin": "2026-07-05",
        "zona_actual": "La Candelaria", "gustos": [], "pais": "CO",
        "planes": [], "idioma": "es",
    }
    block = context._lugares_block(trip, q)
    lineas_tavola = [ln for ln in block.split("\n") if "tavola" in ln.lower()]
    return {
        "pregunta": q,
        "codigo_tiene_anclaje": "mensaje" in context.build.__code__.co_varnames,
        "lineas_con_tavola": lineas_tavola,
        "tiene_dato_social": any("DATO LOCAL" in ln for ln in lineas_tavola),
    }


# ── Chat ──
@app.get("/trips/{tid}/messages")
def get_messages(tid: str, user: dict = Depends(current_user)):
    """Historial del chat para este viaje, en orden cronológico.

    El frontend lo llama al abrir el chat para restaurar la conversación: el
    backend ya persistía cada mensaje en la tabla `messages`, pero no había
    forma de leerlos de vuelta, así que el chat se veía vacío al reabrir.
    Los mensajes-tap (que empiezan con '[') son señales internas de UI/sistema,
    no conversación real, así que los ocultamos del historial visible.
    """
    own_trip(tid, user)
    msgs = [
        {"role": m["role"], "content": m["content"], "ts": m.get("created_at")}
        for m in db.rows("messages", tid)
        if not (m["role"] == "user" and m["content"].strip().startswith("["))
    ]
    return {"messages": msgs}


@app.post("/trips/{tid}/chat")
def chat(tid: str, c: ChatIn, user: dict = Depends(verified_user)):
    trip = own_trip(tid, user)
    # Los taps de notificaciones (mensajes entre corchetes) no gastan cuota de
    # chat: son acciones de UI, no conversación real con el LLM. Igual cuentan
    # poco, pero para no penalizar al usuario por interactuar, los eximimos.
    es_tap = c.message.strip().startswith("[")
    if not es_tap:
        _consume_or_429(user, "chat")
    db.insert("messages", {"trip_id": tid, "role": "user", "content": c.message})
    history = [{"role": m["role"], "content": m["content"]} for m in db.rows("messages", tid)]

    # ── ROUTER DE INTENCIÓN ──
    # Una sola llamada BARATA (sin el system prompt grande) clasifica el mensaje.
    # Con eso decidimos qué llamadas caras hacer DESPUÉS. Antes corrían SIEMPRE
    # las tres (chat + extracción de planes + cadenas), lo que reventaba el límite
    # de tokens/min de Groq (los 429 del dashboard). Ahora cada subtarea corre
    # solo cuando la intención la pide.
    from . import router as router_mod
    intent = router_mod.clasificar(c.message, trip) if not es_tap else {"intencion": "charla"}
    intencion = intent["intencion"]

    # La conversación es el producto: una falla transitoria del proveedor LLM
    # (timeout, 429 tras failover, corte de red) NO debe tumbar el endpoint ni
    # mostrar "se me cruzaron los cables". Si falla, degradamos con un mensaje
    # honesto en vez de devolver un 500.
    try:
        reply = llm.chat(context.build(trip, mensaje=c.message), history)
    except llm.RateLimited:
        reply = ("Ahora mismo tengo mucha demanda y no alcancé a responderte. "
                 "Dame unos segundos y reenvíame el mensaje.")
    except Exception:
        reply = ("Se me trabó la conexión un momento y no alcancé a procesar eso. "
                 "Mándamelo de nuevo y lo retomo al instante.")
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": reply})

    # Red de seguridad: ¿el chat inventó un plato que no está en la descripción
    # curada del lugar que mencionó? (caso real: "Crepes & Waffles" + "bandeja
    # paisa"). No bloqueamos la respuesta — solo lo dejamos en logs para poder
    # ver si el prompt sigue fallando y reforzarlo o ajustar la curación.
    try:
        from . import food_guard
        food_guard.verificar_respuesta(reply, db.places_for_city(trip.get("ciudad", "")))
    except Exception:
        pass

    # Extracción de planes: SOLO si el router dice que el usuario está agendando.
    # Antes corría en CADA mensaje (una llamada LLM extra siempre). Ahora se salta
    # por completo en navegación, búsqueda, recomendación y charla → menos tokens
    # y, de paso, mata el bug de "quiero volver al hotel" → "¿lo anoto?".
    propuestas = []
    if not es_tap and intencion == "agendar_plan":
        from . import plans as plans_mod, timeutil
        propuestas = plans_mod.normalizar_lista(
            llm.extract_plans_from_chat(c.message, trip), origen="chat")
        propuestas = plans_mod.limpiar_lugar_ciudad(propuestas, trip.get("ciudad", ""))
        propuestas = plans_mod.enriquecer_con_curacion(
            propuestas, db.places_for_city(trip.get("ciudad", "")), db.best_place_match)
        try:
            ahora = timeutil.now_local(trip)
            propuestas = plans_mod.rellenar_fechas(propuestas, c.message, ahora)
        except Exception:
            pass

    # Búsqueda en vivo de cadenas: SOLO si el router la pidió (no en cada mensaje).
    chain_results = None
    if not es_tap and intencion == "buscar_cadena":
        from . import places_live
        cadena_q = places_live.detectar_busqueda_cadena(c.message)
        if cadena_q and places_live.disponible():
            olat = trip.get("lat_actual")
            olng = trip.get("lng_actual")
            if olat is None or olng is None:
                coords = geo.resolve_origin(trip)
                if coords:
                    olat, olng = coords
            if olat and olng:
                try:
                    limits.check_and_consume(user["id"], "maps")
                    resultados = places_live.buscar_cerca(cadena_q, olat, olng)
                    if resultados:
                        chain_results = {"query": cadena_q, "items": resultados}
                except limits.QuotaError:
                    chain_results = {"query": cadena_q, "items": [],
                                     "nota": "Llegaste a tu límite de búsquedas en vivo por hoy. "
                                             "Se renueva mañana."}

    return {"reply": reply, "planes_propuestos": propuestas, "chain_results": chain_results,
            "intencion": intencion, "restante_chat": limits.remaining(user["id"], "chat")}


# ── Documentos ──
@app.post("/trips/{tid}/documents")
def upload_doc(tid: str, d: DocIn, user: dict = Depends(verified_user)):
    trip = own_trip(tid, user)
    _consume_or_429(user, "document")  # extracción/visión = LLM caro
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
def notifications(tid: str, user: dict = Depends(current_user)):
    own_trip(tid, user)
    rows = db.rows("notifications", tid)
    for r in rows:
        if r.get("extra"):
            r.update(json.loads(r.pop("extra")))
        else:
            r.pop("extra", None)
    return rows


@app.post("/notifications/{nid}/feedback")
def feedback(nid: str, f: FeedbackIn, user: dict = Depends(current_user)):
    # Verifica que la notificación pertenezca a un trip del usuario.
    with db.conn() as c:
        r = c.execute("SELECT trip_id FROM notifications WHERE id=?", (nid,)).fetchone()
    if not r:
        raise HTTPException(404, "notificación no existe")
    own_trip(r["trip_id"], user)
    try:
        return engine.feedback(nid, f.feedback)
    except KeyError:
        raise HTTPException(404, "notificación no existe")


# ── Dataset (el log de decisiones que acordamos guardar desde el día uno) ──
@app.get("/trips/{tid}/decisions")
def decisions(tid: str, user: dict = Depends(current_user)):
    own_trip(tid, user)
    return db.rows("decisions", tid)


@app.get("/health")
def health():
    from . import places_live
    return {"status": "ok", "llm_mode": llm.MODE, "search_mode": search.MODE,
            "push_enabled": push.enabled(), "places_live": places_live.MODE,
            "email_mode": email_send.mode(),
            "limits": {"free": limits.FREE_LIMITS,
                       "max_trips": limits.MAX_TRIPS_PER_USER,
                       "global_maps_per_day": limits.GLOBAL_MAPS_PER_DAY,
                       "maps_used_today": limits.global_used_today("maps")}}


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
