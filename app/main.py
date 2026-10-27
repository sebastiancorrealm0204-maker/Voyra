"""API del Companion en destino — FastAPI.

Correr:  uvicorn app.main:app --reload
Docs:    http://localhost:8000/docs
"""
import json
import logging
import os
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (transporte as transporte_mod, ciudades, city_knowledge, context, db, engine, geo, llm, memoria, net, places_live, push, scheduler,
               search, seed_data, verificacion, watchers, auth, limits, email_send, social_enrich,
               waitlist, accesos, admin_panel, feedback_limite as feedback_limite_mod, traducir, visual, resumen as resumen_mod, itinerario_chat, horarios, timeutil)

# Logging estructurado (clave=valor) en un solo formato para toda la app. Railway
# captura stdout; LOG_LEVEL=DEBUG para depurar.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
log = logging.getLogger("companion.api")

# Raíz del repo: los archivos del frontend se sirven por ruta absoluta (antes
# dependían del directorio desde el que se lanzara uvicorn).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Threads del threadpool de FastAPI para endpoints sync. El default de anyio es
# 40: con 40 chats esperando al LLM (hasta ~50 s c/u) se bloqueaban TODOS los
# endpoints, incluido /health. Configurable por env.
_THREADPOOL_SIZE = int(os.environ.get("THREADPOOL_SIZE", "100"))

# Pool para el fan-out del chat (respuesta LLM ∥ router ∥ extracción ∥ Places).
_fanout = ThreadPoolExecutor(max_workers=int(os.environ.get("CHAT_FANOUT_WORKERS", "32")),
                             thread_name_prefix="companion-chat")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Arranque/apagado ordenado (reemplaza los on_event deprecados).
    Arranque: amplía el threadpool y prende el scheduler proactivo.
    Apagado: scheduler → pushes en vuelo → cliente HTTP → pool de Postgres."""
    try:
        import anyio.to_thread
        anyio.to_thread.current_default_thread_limiter().total_tokens = _THREADPOOL_SIZE
    except Exception:
        log.exception("threadpool_config_error")
    # Si APScheduler no está instalado, la app sigue normal sin modo proactivo.
    scheduler.start()
    log.info("app_start llm=%s db=%s", llm.MODE, "postgres" if db.IS_POSTGRES else "sqlite")
    try:
        yield
    finally:
        scheduler.shutdown()
        push.shutdown()
        _fanout.shutdown(wait=False, cancel_futures=True)
        net.close()
        db.close_pool()
        log.info("app_stop")


app = FastAPI(title="Companion", version="0.2.0", lifespan=lifespan)

# CORS: permite que el frontend (corriendo en el navegador, ej. un Artifact de
# Claude) llame a este backend en localhost. En producción, reemplaza "*" por
# el dominio real del frontend.
# (Esto incluye la landing en Vercel, que llama a POST /waitlist sin credenciales.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# GZip: el HTML del frontend (~cientos de KB), /destinations/{city}/places y los
# historiales JSON se comprimen 5-10x. Respuestas pequeñas (<1 KB) no se tocan.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def _log_requests(request: Request, call_next):
    """Log por request (método, ruta, status, duración) + X-Request-ID para
    poder cruzar un error del front con la línea del log."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    traducir.fijar_ui_lang(request.headers.get("x-ui-lang"))  # idioma de la interfaz de ESTE request
    t0 = time.perf_counter()
    response = await call_next(request)
    dur_ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Request-ID"] = rid
    nivel = logging.WARNING if response.status_code >= 500 or dur_ms > 15000 else logging.INFO
    log.log(nivel, "req rid=%s method=%s path=%s status=%s dur_ms=%.0f",
            rid, request.method, request.url.path, response.status_code, dur_ms)
    return response


def _es_db_saturada(exc: Exception) -> bool:
    """Pool de Postgres agotado o SQLite bloqueado: falla transitoria → 503."""
    nombre = type(exc).__name__
    if nombre in ("PoolTimeout", "TooManyRequests"):
        return True
    import sqlite3
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


@app.exception_handler(StarletteHTTPException)
async def _http_traducido(request: Request, exc: StarletteHTTPException):
    """Errores HTTP con el mensaje en el idioma de la interfaz (los textos están en español en todo el backend).
    Los códigos (`error`) y el status no cambian; solo se traduce el texto que ve el usuario. Fail-safe: si la
    traducción falla, sale el original."""
    detail = exc.detail
    lang = traducir.ui_lang()
    if lang and lang != "es":
        try:
            if isinstance(detail, str):
                detail = (await run_in_threadpool(traducir.traducir, [detail], lang))[0]
            elif isinstance(detail, dict) and isinstance(detail.get("mensaje"), str):
                detail = {**detail, "mensaje": (await run_in_threadpool(traducir.traducir, [detail["mensaje"]], lang))[0]}
        except Exception:
            log.warning("http_traduccion_fallo path=%s", request.url.path)
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def _error_no_manejado(request: Request, exc: Exception):
    """Ningún error interno sale como texto plano ni filtra detalles: JSON con
    un mensaje genérico (y 503 + Retry-After si es saturación transitoria de la
    DB, para que el cliente reintente)."""
    if _es_db_saturada(exc):
        log.warning("db_saturada path=%s error=%s", request.url.path, type(exc).__name__)
        return JSONResponse({"detail": "Servicio ocupado, intenta de nuevo en unos segundos."},
                            status_code=503, headers={"Retry-After": "2"})
    log.exception("error_no_manejado path=%s", request.url.path)
    return JSONResponse({"detail": "Error interno. Intenta de nuevo."}, status_code=500)


db.init_pool()       # abre el ConnectionPool de Postgres (no-op en modo SQLite)
db.init_db()
auth.init_auth()
limits.init_limits()
waitlist.init_waitlist()
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
    if user.get("suspendido"):
        raise HTTPException(403, {"error": "cuenta_suspendida", "mensaje": "Tu cuenta está suspendida. Escríbenos si crees que es un error."})
    return user


def verified_user(user: dict = Depends(current_user)) -> dict:
    """Usuario autenticado Y con email verificado (si la verificación está activa).
    Exigido para endpoints caros."""
    if auth.verification_required() and not user.get("email_verified"):
        raise HTTPException(403, "Verifica tu email para usar esta función. "
                                 "Revisa tu correo o pide un nuevo enlace.")
    return user


def admin_guard(x_admin_token: str | None = Header(default=None)) -> None:
    """Protege la cola de revisión de enriquecimientos. Como aún no hay rol de
    admin en el modelo de usuario, gateamos con un token de entorno
    (COMPANION_ADMIN_TOKEN, o el antiguo VOYRA_ADMIN_TOKEN). Si no está configurado, los endpoints quedan cerrados."""
    esperado = os.environ.get("COMPANION_ADMIN_TOKEN") or os.environ.get("VOYRA_ADMIN_TOKEN", "")
    if not esperado or (x_admin_token or "").strip() != esperado:
        raise HTTPException(403, "Acceso de administrador requerido.")


def admin_any(x_admin_token: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> None:
    """Acceso de administrador: el token de entorno (X-Admin-Token) o una sesión de un usuario cuyo correo está en ADMIN_EMAILS."""
    import hmac
    esperado = os.environ.get("COMPANION_ADMIN_TOKEN") or os.environ.get("VOYRA_ADMIN_TOKEN", "")
    if esperado and x_admin_token and hmac.compare_digest(x_admin_token.strip(), esperado):
        return
    user = auth.user_for_session(_token_from_header(authorization)) if authorization else None
    if user and auth.es_admin(user) and not user.get("suspendido"):
        return
    raise HTTPException(403, "Acceso de administrador requerido.")


def admin_quien(authorization: str | None = Header(default=None)) -> dict:
    """Quién es el admin que hace la petición (vacío si entra con el token de entorno)."""
    return (auth.user_for_session(_token_from_header(authorization)) if authorization else None) or {}


def own_trip(tid: str, user: dict) -> dict:
    """Carga el trip y verifica que sea del usuario. 404 si no existe, 403 si no es suyo.
    Una sola query (trip + dueño)."""
    trip, owner = db.get_trip_with_owner(tid)
    if not trip:
        raise HTTPException(404, "trip no existe")
    # Trips legacy sin dueño: se permiten (compat con datos previos a auth).
    if owner is not None and owner != user["id"]:
        raise HTTPException(403, "Este viaje no es tuyo.")
    # Idioma único: si la interfaz está en otro idioma que el viaje, el viaje se alinea (Companion, notificaciones,
    # voz y pantallas hablan lo mismo). No depende de que el cliente haya alcanzado a hacer un PATCH aparte.
    ui = traducir.ui_lang()
    if ui and ui != (trip.get("idioma") or "es"):
        try:
            db.update_trip(tid, {"idioma": ui})
            trip = {**trip, "idioma": ui}
        except Exception:
            log.warning("sync_idioma_fallo trip=%s", tid)
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
        if e.total_block:
            raise HTTPException(429, {
                "error": "quota_total",
                "resource": resource,
                "limite": e.limit,
                "usado": e.used,
                "mensaje": "Llegaste al máximo gratis de la etapa de pruebas. ¡Gracias por ayudarnos a mejorar!"})
        raise HTTPException(429, {
            "error": "quota",
            "resource": resource,
            "limite": e.limit,
            "usado": e.used,
            "mensaje": f"Alcanzaste tu límite diario de {e.limit} para esta función. "
                       "Se renueva mañana."})


# ── Schemas ──
class HotelConfirmadoIn(BaseModel):
    place_id: str = Field("", max_length=200)
    nombre: str = Field("", max_length=200)
    direccion: str = Field("", max_length=300)
    lat: float
    lng: float


class TripIn(BaseModel):
    ciudad: str = ""
    hotel: str = ""
    inicio: str = ""
    fin: str = ""
    vuelo_ida: str = ""
    vuelo_regreso: str = ""
    pais: str = "Colombia"
    gustos: list[str] = Field(default_factory=list)
    idioma: str = "es"            # idioma del usuario (es | en | pt | fr | de | it) — en qué le habla el Companion
    # Hotel que el usuario CONFIRMÓ entre los candidatos (ver /hotels/search): evita adivinar cuál de varios homónimos es.
    hotel_confirmado: HotelConfirmadoIn | None = None


class EncuestaIn(BaseModel):
    nota_destino: int | None = None
    nota_app: int | None = None
    gusto_destino: list[str] = []
    gusto_app: list[str] = []
    mejora_destino: str = ""
    mejora_app: str = ""
    feature: str = ""


class TripPatchIn(BaseModel):
    """Para completar/corregir datos del viaje (ej. lo que faltó tras subir reservas)."""
    ciudad: str | None = None
    hotel: str | None = None
    hotel_confirmado: HotelConfirmadoIn | None = None
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
    zona: str = Field("En el hotel", max_length=200)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    disparar_geofence: bool = False


class ChatIn(BaseModel):
    # Tope defensivo: un mensaje gigante solo quema tokens (y cuota del proveedor).
    message: str = Field(..., max_length=10_000)


class DocIn(BaseModel):
    # Topes defensivos (~15 MB de archivo en base64): evitan que un upload
    # descomunal tumbe el proceso por memoria. Holgados para fotos de celular.
    filename: str = Field(..., max_length=300)
    text_content: str = Field("", max_length=500_000)       # texto del documento (.txt o ya extraído)
    image_data_url: str = Field("", max_length=20_000_000)  # data:image/...;base64,... fotos/capturas
    pdf_data_url: str = Field("", max_length=20_000_000)    # data:application/pdf;base64,...


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
    nombre: str = ""
    telefono: str = ""   # con código de país, ej. "+57 300 123 4567"; único por cuenta
    pais: str = ""
    codigo: str = ""    # código de acceso del hotel


class LoginIn(BaseModel):
    email: str
    password: str


class ResendIn(BaseModel):
    email: str


# ── Auth endpoints ──
@app.post("/auth/signup")
def signup(body: SignupIn, request: Request):
    """Registro. Con REQUIRE_EMAIL_VERIFICATION=1 crea el usuario SIN verificar y envía el
    correo de verificación (no inicia sesión hasta confirmarlo). Por defecto la verificación
    está apagada: la cuenta queda lista y el cliente puede iniciar sesión de inmediato."""
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
        raise HTTPException(409, {"error": "email_exists",
                                  "mensaje": "Ese email ya está registrado. Inicia sesión."})
    nombre = body.nombre.strip()
    if body.nombre and len(nombre) < 2:
        raise HTTPException(400, "Escribe tu nombre.")
    telefono = ""
    if body.telefono.strip():
        telefono = auth.normalize_phone(body.telefono)
        if not telefono:
            raise HTTPException(400, "Celular inválido. Incluye el código de país.")
        if auth.get_user_by_phone(telefono):
            raise HTTPException(409, {"error": "phone_exists",
                                      "mensaje": "Ese celular ya tiene una cuenta. Inicia sesión."})

    # Código de acceso del hotel: obligatorio con REQUIRE_ACCESS_CODE=1; si no, opcional (si lo traen válido, se anota el hotel).
    acceso = None
    if body.codigo.strip() or accesos.requerido():
        acceso = accesos.consumir(body.codigo)
        if not acceso and accesos.requerido():
            raise HTTPException(403, {"error": "codigo_invalido",
                                      "mensaje": "Ese código no es válido o ya no tiene cupos. Pídelo en la recepción de tu hotel."})

    auth.record_signup_attempt(ip)
    try:
        user = auth.create_user(email, body.password, ip=ip, nombre=nombre,
                                telefono=telefono, pais=body.pais)
    except ValueError as e:  # carrera: otro registro idéntico justo antes
        if acceso:
            accesos.devolver(acceso["codigo"])
        code = "phone_exists" if "celular" in str(e) else "email_exists"
        raise HTTPException(409, {"error": code, "mensaje": str(e)})
    if acceso:
        with db.conn() as c:
            c.execute("UPDATE users SET codigo_acceso=?, hotel_origen=? WHERE id=?", (acceso["codigo"], acceso["hotel"], user["id"]))
    if nombre or telefono:
        auth.set_profile(user["id"], {"nombre": nombre, "telefono": telefono})
    if not auth.verification_required():
        auth.mark_email_verified(user["id"])
        return {"ok": True, "email": email, "verificado": True, "mensaje": "Cuenta creada."}
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
        "<h2>¡Email confirmado!</h2>"
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
    """Inicia sesión. Exige email verificado solo si la verificación está activa."""
    user = auth.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(401, "Email o contraseña incorrectos.")
    if user.get("suspendido"):
        raise HTTPException(403, {"error": "cuenta_suspendida", "mensaje": "Tu cuenta está suspendida. Escríbenos si crees que es un error."})
    if auth.verification_required() and not user["email_verified"]:
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
            "es_admin": auth.es_admin(user),
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
            # El trip sigue funcionando sin coordenadas del hotel. (Antes este
            # except se tragaba un NameError: `places_live` no estaba importado
            # en este módulo, así que el geocoding NUNCA corría.)
            log.warning("geocode_hotel_fallo trip=%s", tid, exc_info=True)

    if not places_live.disponible():
        return  # sin key no hay nada que geocodificar: ni gastamos un thread
    threading.Thread(target=_run, daemon=True).start()


def _campos_hotel(h: HotelConfirmadoIn) -> dict:
    """Lo que se guarda en el viaje cuando el usuario confirmó su hotel (igual que lo que dejaba el geocoding automático)."""
    return {"hotel": h.nombre or None, "lat_hotel": h.lat, "lng_hotel": h.lng, "place_id_hotel": h.place_id,
            "dir_hotel": h.direccion, "google_name_hotel": h.nombre}


@app.get("/hotels/search")
def hoteles_buscar(q: str, ciudad: str = "Bogotá", user: dict = Depends(current_user)):
    """Candidatos de hotel con foto REAL y dirección para que el usuario confirme cuál es (en onboarding)."""
    q = (q or "").strip()[:120]
    if len(q) < 3:
        return {"hoteles": []}
    cand = places_live.buscar_hoteles(q, ciudad, n=4)
    fotos = [visual._pool.submit(visual.fotos, h["place_id"], 1, 480) for h in cand]
    out = []
    for h, f in zip(cand, fotos):
        try:
            ph = f.result(timeout=8)
        except Exception:
            ph = []
        out.append({**{k: v for k, v in h.items() if k != "foto_name"}, "foto": ph[0] if ph else None})
    return {"hoteles": out}


@app.post("/trips")
def create_trip(t: TripIn, user: dict = Depends(verified_user)):
    if not limits.can_create_trip(user["id"]):
        tope = limits.effective_trip_limit(user["id"])
        raise HTTPException(429, {
            "error": "trip_limit",
            "limite": tope,
            "mensaje": f"Ya usaste los {tope} viajes de tu plan gratuito. "
                       "Borrar un viaje no libera cupo."})
    _exigir_ciudad_activa(t.ciudad)
    usados = limits.trips_count(user["id"])
    tid = db.create_trip(t.model_dump(exclude={"hotel_confirmado"}), user_id=user["id"])
    limits.registrar_viaje_creado(user["id"], usados)
    if t.hotel_confirmado:
        db.update_trip(tid, {k: v for k, v in _campos_hotel(t.hotel_confirmado).items() if v is not None})
    _idi = (t.idioma or "es").lower()
    _saludos = {
        "es": (f"¡Listo! Soy tu amigo local en {t.ciudad}. Pregúntame dónde comer, qué hacer o cómo "
               "moverte. Para empezar: ¿qué planes tienes para hoy y mañana?",
               "¡Hola! Soy tu Companion. Sube tus reservas (vuelo, hotel, tours) y armo tu "
               "viaje contigo. También puedes contarme a dónde vas y cuándo."),
        "en": (f"All set! I'm your local friend in {t.ciudad}. Ask me where to eat, what to do or how to "
               "get around. To start: what are your plans for today and tomorrow?",
               "Hi! I'm your Companion. Upload your bookings (flight, hotel, tours) and I'll build your "
               "trip with you. You can also just tell me where you're going and when."),
        "pt": (f"Pronto! Sou seu amigo local em {t.ciudad}. Me pergunte onde comer, o que fazer ou como "
               "se locomover. Para começar: quais são seus planos para hoje e amanhã?",
               "Oi! Sou o seu Companion. Suba suas reservas (voo, hotel, passeios) e eu monto sua "
               "viagem com você. Você também pode só me dizer para onde vai e quando."),
        "fr": (f"C'est prêt ! Je suis ton ami local à {t.ciudad}. Demande-moi où manger, quoi faire ou "
               "comment te déplacer. Pour commencer : quels sont tes plans pour aujourd'hui et demain ?",
               "Salut ! Je suis ton Companion. Ajoute tes réservations (vol, hôtel, activités) et je "
               "construis ton voyage avec toi. Tu peux aussi simplement me dire où tu vas et quand."),
        "de": (f"Fertig! Ich bin dein Local-Freund in {t.ciudad}. Frag mich, wo du essen kannst, was du "
               "unternehmen sollst oder wie du dich fortbewegst. Zum Start: Was hast du heute und morgen vor?",
               "Hallo! Ich bin dein Companion. Lade deine Buchungen hoch (Flug, Hotel, Touren) und ich "
               "plane deine Reise mit dir. Du kannst mir auch einfach sagen, wohin du reist und wann."),
        "it": (f"Pronto! Sono il tuo amico locale a {t.ciudad}. Chiedimi dove mangiare, cosa fare o come "
               "muoverti. Per iniziare: che programmi hai per oggi e domani?",
               "Ciao! Sono il tuo Companion. Carica le tue prenotazioni (volo, hotel, attività) e costruisco "
               "il tuo viaggio con te. Puoi anche dirmi semplicemente dove vai e quando."),
    }
    con_ciudad, sin_ciudad = _saludos.get(_idi, _saludos["es"])
    saludo = con_ciudad if t.ciudad.strip() else sin_ciudad
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": saludo})

    # Geocodificar el hotel en background para no demorar la respuesta del
    # onboarding. Cuando termine, guarda lat_hotel/lng_hotel/place_id_hotel en
    # el trip — el Companion los usa para detectar si el usuario está en su hotel.
    if t.hotel.strip() and t.ciudad.strip() and not t.hotel_confirmado:
        _geocodificar_hotel_bg(tid, t.hotel, t.ciudad, t.pais)

    return {"trip_id": tid, "greeting": saludo, "trip": db.get_trip(tid)}


def _exigir_ciudad_activa(ciudad: str | None) -> None:
    """422 si se intenta abrir un viaje en una ciudad donde aún no estamos.
    Vacío se permite: el onboarding crea un viaje en blanco para subir reservas."""
    if ciudad and ciudad.strip() and not ciudades.activa(ciudad):
        raise HTTPException(422, {"error": "ciudad_no_disponible", "ciudades": ciudades.ACTIVAS,
                                  "mensaje": ciudades.mensaje_no_disponible(ciudad)})


@app.get("/trips")
def list_my_trips(user: dict = Depends(current_user)):
    """Lista los viajes del usuario autenticado."""
    return {"trips": db.list_trips_for_user(user["id"])}


@app.patch("/trips/{tid}")
def patch_trip(tid: str, p: TripPatchIn, user: dict = Depends(current_user)):
    own_trip(tid, user)
    patch = {k: v for k, v in p.model_dump(exclude={"hotel_confirmado"}).items() if v is not None}
    if p.hotel_confirmado:
        patch.update({k: v for k, v in _campos_hotel(p.hotel_confirmado).items() if v is not None})
    if p.ciudad is not None:
        _exigir_ciudad_activa(p.ciudad)
    if patch:
        db.update_trip(tid, patch)
    # Si se actualizó el hotel o la ciudad, re-geocodificar el hotel en background.
    trip = db.get_trip(tid)
    if (p.hotel is not None or p.ciudad is not None) and not p.hotel_confirmado and trip.get("hotel") and trip.get("ciudad"):
        _geocodificar_hotel_bg(tid, trip["hotel"], trip["ciudad"], trip.get("pais", ""))
    return trip


@app.get("/trips/{tid}")
def get_trip(tid: str, user: dict = Depends(current_user)):
    return own_trip(tid, user)


@app.delete("/trips/{tid}")
def delete_trip(tid: str, user: dict = Depends(current_user)):
    """Borra un viaje del usuario. NO devuelve el cupo de viajes: el tope cuenta los viajes creados en total."""
    own_trip(tid, user)
    with db.conn() as c:
        c.execute("DELETE FROM trips WHERE id=?", (tid,))
    return {"ok": True}


admin_panel.registrar(app, current_user, admin_any, admin_quien)


@app.get("/auth/config")
def auth_config():
    """Lo que la pantalla de registro necesita saber: si exige código de acceso."""
    return {"codigo_requerido": accesos.requerido()}


@app.get("/auth/codigo/{codigo}")
def auth_codigo(codigo: str):
    """¿Es válido este código? Devuelve el hotel para saludar al huésped. No gasta ningún uso."""
    v = accesos.validar(codigo)
    return {"valido": bool(v), "hotel": v["hotel"] if v else ""}


class CodigoIn(BaseModel):
    hotel: str = Field(..., max_length=80)
    max_usos: int = Field(0, ge=0, le=100000)   # 0 = sin tope
    codigo: str | None = Field(None, max_length=40)


@app.post("/admin/codigos", dependencies=[Depends(admin_any)])
def admin_crear_codigo(b: CodigoIn):
    try:
        return accesos.crear(b.hotel, b.max_usos, b.codigo)
    except ValueError as e:
        raise HTTPException(422, {"error": str(e)})


class LoteIn(BaseModel):
    hotel: str = Field(..., max_length=80)
    cantidad: int = Field(..., ge=1, le=500)
    max_usos: int = Field(1, ge=0, le=100000)   # 1 = un código por huésped


@app.post("/admin/codigos/lote", dependencies=[Depends(admin_any)])
def admin_lote_codigos(b: LoteIn):
    try:
        return {"codigos": accesos.crear_lote(b.hotel, b.cantidad, b.max_usos)}
    except ValueError as e:
        raise HTTPException(422, {"error": str(e)})


@app.get("/admin/codigos", dependencies=[Depends(admin_any)])
def admin_listar_codigos():
    return {"codigos": accesos.listar()}


@app.post("/admin/codigos/{codigo}/desactivar", dependencies=[Depends(admin_any)])
def admin_desactivar_codigo(codigo: str):
    if not accesos.desactivar(codigo):
        raise HTTPException(404, "Código no existe.")
    return {"ok": True}


class FeedbackLimiteIn(BaseModel):
    recurso: str = Field(..., max_length=20)       # chat | maps | document | scan | trips
    gusta: bool
    motivo: str = Field("", max_length=600)
    trip_id: str | None = Field(None, max_length=64)


@app.post("/feedback/limite")
def feedback_limite(body: FeedbackLimiteIn, user: dict = Depends(current_user)):
    """Al llegar a un límite, el usuario cuenta si le gusta o no y por qué, y a cambio se amplía el límite (etapa de pruebas)."""
    r = feedback_limite_mod.registrar(user["id"], recurso=body.recurso, gusta=body.gusta, motivo=body.motivo, trip_id=body.trip_id)
    if not r["ok"]:
        raise HTTPException(422, {"error": r["razon"], "mensaje": "Cuéntanos un poco más: escribe por qué."
                                  if r["razon"] == "motivo_corto" else "Recurso no válido."})
    return {**r, "cuotas": limits.usage_summary(user["id"]),
            "trips": {"usados": limits.trips_count(user["id"]), "limite": limits.effective_trip_limit(user["id"])}}


@app.get("/places/{place_id}/fotos")
def fotos_del_lugar(place_id: str, n: int = 4, user: dict = Depends(current_user)):
    """«Ver más fotos» de una tarjeta: las tarjetas del chat traen solo 1 foto (cada foto cuesta); el resto se pide aquí al tocar."""
    return {"fotos": visual.fotos(place_id, max(1, min(n, 5)))}


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


@app.get("/trips/{tid}/plans/fotos")
def plans_fotos(tid: str, user: dict = Depends(current_user)):
    """Tarjeta con foto real (Google Places) de cada plan del itinerario que es un lugar de nuestra curación."""
    trip = own_trip(tid, user)
    try:
        fotos = resumen_mod.fotos_de_planes(db.get_plans(tid), db.places_for_city(trip.get("ciudad", "")),
                                            trip.get("idioma") or "es")
    except Exception:
        log.warning("fotos_planes_fallo trip=%s", tid, exc_info=True)
        fotos = {}
    return {"fotos": fotos}


@app.post("/trips/{tid}/encuesta")
def trip_encuesta(tid: str, e: EncuestaIn, user: dict = Depends(current_user)):
    """Encuesta de cierre (obligatoria para ver el resumen): nota y feedback del destino, y de la app."""
    trip = own_trip(tid, user)
    if not resumen_mod.guardar_encuesta(
            trip, user["id"], nota_destino=e.nota_destino, nota_app=e.nota_app, gusto_destino=e.gusto_destino,
            gusto_app=e.gusto_app, mejora_destino=e.mejora_destino, mejora_app=e.mejora_app, feature=e.feature):
        raise HTTPException(422, "Calificanos el destino y la app (de 1 a 5).")
    return {"ok": True}


@app.get("/trips/{tid}/encuesta")
def trip_encuesta_estado(tid: str, user: dict = Depends(current_user)):
    """¿Ya respondió la encuesta de cierre? Es rápido (sin armar el resumen) para decidir qué pantalla mostrar."""
    own_trip(tid, user)
    return {"respondida": resumen_mod.encuesta_respondida(tid)}


@app.get("/trips/{tid}/resumen")
def trip_resumen(tid: str, user: dict = Depends(current_user)):
    """Resumen del viaje: lo que hizo (con fotos reales) y lo que le faltó conocer (ganchos para volver)."""
    trip = own_trip(tid, user)
    return resumen_mod.resumen(trip, db.get_plans(tid), db.places_for_city(trip.get("ciudad", "")))


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
    # Idempotente por payload["id"] (ver watchers.duffel_webhook).
    try:
        return watchers.duffel_webhook(tid, payload)
    except KeyError:
        raise HTTPException(404, "trip no existe")


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
    return push.send_to_trip(tid, "Notificaciones activadas", "¡Funcionan! Te avisaré cuándo salir, qué hay abierto y consejos para moverte seguro.",
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
    trip = own_trip(tid, user)
    out = watchers.nearby_recommendations(tid)
    try:  # cada recomendación con su tarjeta visual (foto, zona, calificación, dato local)
        por_nombre = {p["name"]: p for p in db.places_for_city(trip.get("ciudad", ""))}
        res = out.get("resultados") or []
        elegidos = [por_nombre[r["lugar"]] for r in res if r.get("lugar") in por_nombre]
        dist = {r["lugar"]: r.get("distancia_km") for r in res}
        cards = {c["name"]: c for c in visual.tarjetas(elegidos, trip.get("idioma") or "es", distancias=dist)}
        out = {**out, "resultados": [{**r, "tarjeta": cards.get(r.get("lugar"))} for r in res]}
    except Exception:
        log.warning("tarjetas_nearby_fallo trip=%s", tid, exc_info=True)
    if out.get("nota"):
        out = {**out, "nota": traducir.traducir([out["nota"]], trip.get("idioma") or "es")[0]}
    return out


@app.get("/trips/{tid}/nearby-chain")
def nearby_chain(tid: str, q: str, orig_lat: float | None = None,
                 orig_lng: float | None = None, radio_m: int = 2500,
                 user: dict = Depends(verified_user)):
    """Búsqueda EN VIVO de cadenas/servicios cerca del usuario (Carulla, cajero,
    farmacia, etc.). Consulta Google Places al momento — NO usa la curación.

    Pasa orig_lat/orig_lng (GPS del usuario) o usa la ubicación guardada del trip.
    """
    trip = own_trip(tid, user)
    radio_m = max(100, min(int(radio_m), 50_000))  # Google rechaza radios fuera de rango
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


def _origen_del_viaje(trip: dict, lat: float | None = None, lng: float | None = None):
    """GPS del teléfono > ubicación guardada del viaje > hotel/zona conocida. (None, None) si no hay nada."""
    if lat is None or lng is None:
        lat, lng = trip.get("lat_actual"), trip.get("lng_actual")
    if lat is None or lng is None:
        coords = geo.resolve_origin(trip)
        if coords:
            lat, lng = coords
    return lat, lng


def _bloque_rutas(trip: dict, user: dict, mensaje: str) -> tuple[str, dict | None]:
    """Si el mensaje pide cómo llegar a un lugar CURADO (o al hotel / aeropuerto), trae la ruta REAL.
    Devuelve (bloque para el prompt, ruta para pintar la tarjeta «Cómo llegar» en el chat). Cualquier fallo
    (sin Routes, sin cuota, sin ubicación) degrada a lo que se pueda y el chat sigue normal."""
    if (trip.get("ciudad") or "Bogotá") != "Bogotá":
        return "", None
    alerta = ""
    try:
        if transporte_mod._PREGUNTA_MOVILIDAD.search(mensaje):
            alerta = transporte_mod.bloque_alertas(transporte_mod.alertas())
    except Exception as e:
        log.warning("alertas_error error=%s", type(e).__name__)
    if not transporte_mod.disponible():
        return alerta, None
    try:
        d = transporte_mod.detectar_destino(mensaje, db.places_for_city(trip.get("ciudad") or "Bogotá"))
        if not d:
            libre = transporte_mod.destino_libre(mensaje)
            if libre:
                # Solo damos ruta a lugares de nuestra curación (más hotel y aeropuerto): ese es el valor de Companion.
                return ("RUTAS: el usuario pregunta cómo llegar a «%s», que NO está en nuestra curación. No calcules ni "
                        "inventes la ruta ni tiempos. Dile con naturalidad que ahí no te metes, y ofrécele 1-2 lugares "
                        "CURADOS parecidos o cercanos a su zona (de los que tienes en LUGARES) con los que sí puedes ayudarle "
                        "a llegar." % libre), None
        if not d:
            return alerta, None
        if d["tipo"] == "aeropuerto":
            dest = transporte_mod.AEROPUERTO
        elif d["tipo"] == "hotel":
            if trip.get("lat_hotel") is None or trip.get("lng_hotel") is None:
                return "", None
            dest = (trip["lat_hotel"], trip["lng_hotel"], trip.get("hotel") or "Hotel")
        else:
            dest = (d["lat"], d["lng"], d["nombre"])
        olat, olng = _origen_del_viaje(trip)
        if olat is None or olng is None:
            sin_origen = ("RUTAS: el usuario pide cómo llegar pero NO tengo su ubicación. Pregúntale en qué zona o "
                          "dirección está (o que comparta su ubicación) antes de dar tiempos; no inventes la ruta.")
            return "\n\n".join(x for x in (sin_origen, alerta) if x), None
        limits.check_and_consume(user["id"], "maps")
        ruta = transporte_mod.rutas((float(olat), float(olng), trip.get("zona_actual") or ""), dest, 0)
        ruta["restante_maps"] = limits.remaining(user["id"], "maps")
        bloque = transporte_mod.bloque_chat(ruta) if ruta["fuente"] == "google" else ""
        return "\n\n".join(x for x in (bloque, alerta) if x), ruta
    except limits.QuotaError:
        return alerta, None
    except Exception as e:  # una ruta fallida nunca rompe el chat
        log.warning("bloque_rutas_error error=%s", type(e).__name__)
        return alerta, None


@app.get("/transporte/trafico")
def transporte_trafico(user: dict = Depends(current_user)):
    """Termómetro de tráfico de Bogotá (4 corredores). Medición compartida y cacheada (20 min)."""
    return transporte_mod.trafico()


class TraducirIn(BaseModel):
    textos: list[str] = Field(..., max_length=60)
    idioma: str | None = None


@app.post("/i18n/traducir")
def i18n_traducir(body: TraducirIn, user: dict = Depends(current_user)):
    """Traduce textos dinámicos (en español) al idioma de la interfaz. Caché global por frase: una vez traducida,
    es gratis para todos. Si no hay LLM, devuelve el original."""
    lang = (body.idioma or traducir.ui_lang() or "es").lower()
    return {"idioma": lang, "textos": traducir.traducir([t[:1200] for t in body.textos], lang)}


@app.get("/transporte/alertas")
def transporte_alertas(user: dict = Depends(current_user)):
    """Paros, marchas y cierres que afectan la movilidad en Bogotá (noticias de 2 días, cacheado 30 min)."""
    return traducir.profundo(transporte_mod.alertas(), traducir.ui_lang(), omitir={"actualizado"})


class TransporteIn(BaseModel):
    destino: str | None = None        # "hotel" | "aeropuerto" | nombre de un lugar curado
    nombre: str | None = None         # nombre a mostrar si se manda lat/lng
    lat: float | None = None
    lng: float | None = None
    origen_lat: float | None = None   # GPS del teléfono; si falta, se usa la ubicación guardada del viaje
    origen_lng: float | None = None
    salida_min: int = 0               # en cuántos minutos sale (0 = ahora)


@app.post("/trips/{tid}/transporte")
def transporte(tid: str, body: TransporteIn, user: dict = Depends(verified_user)):
    """Cómo llegar: taxi (con el trancón), TransMilenio/SITP y a pie, con una recomendación.
    Sin Routes API responde en modo demo (`fuente: "demo"`); con ella, usa tráfico real."""
    trip = own_trip(tid, user)
    # ── destino
    clave = (body.destino or "").strip().lower()
    if body.lat is not None and body.lng is not None:
        dest = (body.lat, body.lng, body.nombre or body.destino or "")
    elif clave == "hotel":
        if trip.get("lat_hotel") is None or trip.get("lng_hotel") is None:
            raise HTTPException(404, {"error": "hotel_sin_ubicacion",
                                      "mensaje": "Aún no ubico tu hotel en el mapa."})
        dest = (trip["lat_hotel"], trip["lng_hotel"], trip.get("hotel") or "Hotel")
    elif clave == "aeropuerto":
        dest = transporte_mod.AEROPUERTO
    else:
        lugar = transporte_mod.buscar_lugar(body.destino or "", db.places_for_city(trip.get("ciudad") or "Bogotá"))
        if not lugar:
            raise HTTPException(404, {"error": "destino_no_encontrado",
                                      "mensaje": "No encontré ese destino. Elige uno de la lista."})
        dest = (float(lugar["lat"]), float(lugar["lng"]), lugar["name"])
    # ── origen: GPS del teléfono > ubicación guardada > hotel/zona conocida
    olat, olng = _origen_del_viaje(trip, body.origen_lat, body.origen_lng)
    if olat is None or olng is None:
        raise HTTPException(422, {"error": "sin_origen",
                                  "mensaje": "No tengo tu ubicación. Comparte tu GPS o elige tu zona."})
    # Solo gastamos cuota si de verdad vamos a pegarle a Google (hay key).
    if transporte_mod.disponible():
        _consume_or_429(user, "maps")
    out = transporte_mod.rutas((float(olat), float(olng), trip.get("zona_actual") or ""), dest, body.salida_min)
    out["restante_maps"] = limits.remaining(user["id"], "maps")
    return out


@app.get("/trips/{tid}/airport")
def airport_arrival(tid: str, user: dict = Depends(current_user)):
    """Modo aeropuerto: timeline de llegada (migración → equipaje → salida) +
    opciones de transporte con tips anti-estafa, curado por aeropuerto y con
    tiempos en vivo si hay búsqueda configurada."""
    trip = own_trip(tid, user)
    return traducir.profundo(watchers.airport_arrival(tid), trip.get("idioma") or "es")


@app.get("/destinations/portada")
def destinos_portada():
    """Fotos reales de lugares emblemáticos de las ciudades activas, para la bienvenida y el inicio (público, cacheado)."""
    return visual.portada(list(ciudades.ACTIVAS))


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


@app.get("/_diag_fotos")
def diag_fotos():
    """Por qué (no) salen las fotos: key presente, cobertura de place_id y la respuesta cruda de Google."""
    places = db.places_for_city("Bogotá")
    vis = [p for p in places if p.get("category") in visual.CATEGORIAS_VISUALES]
    muestra = next((p for p in vis if p.get("place_id")), None)
    out = {"api_key": bool(places_live.API_KEY), "visuales": len(vis),
           "con_place_id": sum(1 for p in vis if p.get("place_id")), "muestra": muestra and muestra["name"]}
    if muestra and places_live.API_KEY:
        try:
            r = net.request("GET", f"https://places.googleapis.com/v1/places/{muestra['place_id']}", retries=0, timeout=6,
                            headers={"X-Goog-Api-Key": places_live.API_KEY, "X-Goog-FieldMask": "photos.name"})
            body = r.json()
            out["details_status"] = r.status_code
            out["fotos_en_google"] = len(body.get("photos") or [])
            if r.status_code != 200:
                out["details_error"] = (body.get("error") or {}).get("message", "")[:300]
            elif body.get("photos"):
                m = net.request("GET", f"https://places.googleapis.com/v1/{body['photos'][0]['name']}/media", retries=0,
                                timeout=6, headers={"X-Goog-Api-Key": places_live.API_KEY},
                                params={"maxWidthPx": 640, "skipHttpRedirect": "true"})
                out["media_status"] = m.status_code
                out["media_uri"] = (m.json().get("photoUri") or "")[:80] if m.status_code == 200 else                     (m.json().get("error") or {}).get("message", "")[:300]
        except Exception as e:
            out["excepcion"] = type(e).__name__
    return out


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


_TXT_CHAT = {
    "demanda": {
        "es": "Ahora mismo tengo mucha demanda y no alcancé a responderte. Dame unos segundos y reenvíame el mensaje.",
        "en": "I'm getting a lot of requests right now and couldn't answer in time. Give me a few seconds and send your message again.",
        "pt": "Estou com muita demanda agora e não consegui te responder a tempo. Me dê alguns segundos e reenvie a mensagem.",
        "fr": "J'ai beaucoup de demandes en ce moment et je n'ai pas pu te répondre à temps. Laisse-moi quelques secondes et renvoie ton message.",
        "de": "Gerade ist bei mir viel los und ich konnte nicht rechtzeitig antworten. Gib mir ein paar Sekunden und sende die Nachricht noch einmal.",
        "it": "Ho molte richieste in questo momento e non sono riuscito a risponderti in tempo. Dammi qualche secondo e rimanda il messaggio.",
    },
    "conexion": {
        "es": "Se me trabó la conexión un momento y no alcancé a procesar eso. Mándamelo de nuevo y lo retomo al instante.",
        "en": "My connection hiccuped for a moment and I couldn't process that. Send it again and I'll pick it right back up.",
        "pt": "Minha conexão travou por um instante e não consegui processar isso. Mande de novo e eu retomo na hora.",
        "fr": "Ma connexion a eu un petit raté et je n'ai pas pu traiter ça. Renvoie-le-moi et je reprends tout de suite.",
        "de": "Meine Verbindung hat kurz gehakt und ich konnte das nicht verarbeiten. Schick es noch einmal, dann mache ich sofort weiter.",
        "it": "La connessione ha avuto un intoppo e non sono riuscito a elaborare. Rimandalo e riprendo subito.",
    },
}


@app.post("/trips/{tid}/chat")
def chat(tid: str, c: ChatIn, user: dict = Depends(verified_user)):
    trip = own_trip(tid, user)
    if resumen_mod.terminado(trip):
        raise HTTPException(409, {"codigo": "viaje_cerrado",
                                  "mensaje": "Este viaje ya terminó. Mira tu resumen o crea uno nuevo."})
    # Los taps de notificaciones (mensajes entre corchetes) no gastan cuota de
    # chat: son acciones de UI, no conversación real con el LLM. Igual cuentan
    # poco, pero para no penalizar al usuario por interactuar, los eximimos.
    es_tap = c.message.strip().startswith("[")
    if not es_tap:
        _consume_or_429(user, "chat")
    db.insert("messages", {"trip_id": tid, "role": "user", "content": c.message})
    # Solo los últimos CHAT_HISTORY_MAX mensajes (antes: los 200 MÁS VIEJOS).
    history = context.historial(tid)
    _idi = (trip.get("idioma") or "es").lower()
    if _idi != "es" and history and history[-1]["role"] == "user":
        # Recordatorio de idioma pegado al último mensaje (solo para el LLM; no se guarda): con el
        # prompt largo en español y un historial en español, el modelo suele contestar en español.
        history[-1] = {**history[-1],
                       "content": history[-1]["content"] + f"\n\n[Reply in {city_knowledge.lang_name(_idi)}.]"}

    # «Agrégalo a mi itinerario»: se agrega SIEMPRE, sin pasar por el modelo ni por una confirmación aparte.
    if not es_tap:
        try:
            ultimo = next((m["content"] for m in reversed(history[:-1]) if m["role"] != "user"), "")
            agregado = itinerario_chat.agregar_desde_chat(trip, c.message, ultimo)
        except Exception:
            log.warning("agregar_itinerario_fallo trip=%s", tid, exc_info=True)
            agregado = None
        if agregado:
            db.insert("messages", {"trip_id": tid, "role": "companion", "content": agregado["reply"]})
            return {"reply": agregado["reply"], "planes_propuestos": [], "chain_results": None,
                    "intencion": "agregar_itinerario", "restante_chat": limits.remaining(user["id"], "chat"),
                    "verificacion": None, "ruta": None, "planes_agregados": agregado["agregados"],
                    "lugares": visual.tarjetas(agregado["lugares"], _idi, n_fotos=1)}

    # ── PARALELISMO ──
    # La respuesta del chat NO depende de la intención: arranca YA en otro
    # thread mientras el router clasifica y, si toca, se extraen planes o se
    # busca la cadena en Google. Antes era router → chat → extracción en serie
    # (hasta 3 llamadas LLM una tras otra); ahora el turno dura ~lo que la más
    # lenta de ellas.
    rutas, ruta_card = ("", None) if es_tap else _bloque_rutas(trip, user, c.message)
    system = context.build(trip, mensaje=c.message, memoria=memoria.bloque(user["id"], tid), rutas=rutas)
    fut_reply = _fanout.submit(llm.chat, system, history)

    # ── ROUTER DE INTENCIÓN ──
    # Una sola llamada BARATA (sin el system prompt grande) clasifica el mensaje
    # (y casi siempre ni eso: las reglas rápidas deciden sin LLM). Con eso
    # decidimos qué subtareas extra correr. Cada subtarea corre solo cuando la
    # intención la pide (evita los 429 de tokens/min de Groq).
    from . import router as router_mod
    intent = router_mod.clasificar(c.message, trip) if not es_tap else {"intencion": "charla"}
    intencion = intent["intencion"]

    fut_planes = None
    if not es_tap and intencion == "agendar_plan":
        fut_planes = _fanout.submit(_propuestas_de_chat, c.message, trip)
    fut_cadena = None
    if not es_tap and intencion == "buscar_cadena":
        fut_cadena = _fanout.submit(_buscar_cadena_chat, c.message, trip, user["id"])

    # La conversación es el producto: una falla transitoria del proveedor LLM
    # (timeout, 429 tras failover, corte de red) NO debe tumbar el endpoint ni
    # mostrar "se me cruzaron los cables". Si falla, degradamos con un mensaje
    # honesto en vez de devolver un 500.
    try:
        reply = fut_reply.result()
    except llm.RateLimited:
        reply = _TXT_CHAT["demanda"].get(_idi, _TXT_CHAT["demanda"]["es"])
    except Exception as e:
        log.warning("chat_fallback trip=%s error=%s", tid, type(e).__name__)
        reply = _TXT_CHAT["conexion"].get(_idi, _TXT_CHAT["conexion"]["es"])
    # Red de seguridad de horarios: si la respuesta ofrece un lugar CERRADO a la hora del plan (el modelo a veces lo hace aunque la lista
    # se lo marque), se le pide UNA vez que la reescriba sin ofrecerlo para ese momento.
    if not es_tap and reply and intencion != "emergencia":
        try:
            ref, _ = horarios.referencia(trip, c.message, timeutil.now_local(trip))
            cerrados = horarios.cerrados_mencionados(reply, db.places_for_city(trip.get("ciudad", "")), ref)
            if cerrados:
                detalle = "; ".join(f"{p['name']} ({horarios.desc_dia(p.get('place_id'), ref) or 'cerrado'})" for p in cerrados)
                correccion = (f"\n\nCORRECCIÓN OBLIGATORIA: tu borrador ofreció lugares que están CERRADOS el {ref.strftime('%d/%m')} a las {ref.strftime('%H:%M')}: {detalle}. "
                              "Respóndele de nuevo al usuario SIN ofrecer esos lugares para ese momento: usa solo lugares de OPCIONES QUE CUMPLEN EL PEDIDO "
                              "(abiertos a esa hora y del tipo que pidió). Si hay pocos, dilo y propón otro momento. Solo puedes nombrarlos como alternativa "
                              "para OTRO día u hora, con su horario real.")
                reply = llm.chat(system + correccion, history)
        except Exception:
            log.warning("red_horarios_fallo trip=%s", tid, exc_info=True)
    # Verificación que corrige: si la respuesta atribuye a un lugar curado algo
    # que su ficha no respalda (caso real: "Crepes & Waffles" + "bandeja paisa"),
    # se reescribe o recorta ANTES de guardarla y mostrarla. Se guarda la versión
    # corregida para que el próximo turno no arrastre el error del historial.
    reply, verificacion_info = verificacion.verificar_y_corregir(
        reply, db.places_for_city(trip.get("ciudad", "")))
    db.insert("messages", {"trip_id": tid, "role": "companion", "content": reply})

    propuestas = _resultado_o(fut_planes, [], "planes")
    chain_results = _resultado_o(fut_cadena, None, "cadena")

    # Tarjetas visuales (foto, zona, calificación, dato local) de los lugares CURADOS que la respuesta nombra.
    lugares_card: list[dict] = []
    if not es_tap:
        try:
            citados = visual.mencionados(reply, db.places_for_city(trip.get("ciudad", "")), maximo=6)
            if context.pide_comida(c.message):
                # Un pedido de comida solo muestra tarjetas de sitios para comer o beber: «Parque de la 93» o «Zona T» nombrados como
                # zona del restaurante no deben ocupar una tarjeta (y dejar fuera al tercer restaurante).
                citados = [p for p in citados if p.get("category") in context.CATEGORIAS_COMIDA]
            lugares_card = visual.tarjetas(citados[:3], _idi)
        except Exception:
            log.warning("tarjetas_lugares_fallo trip=%s", tid, exc_info=True)

    return {"reply": reply, "planes_propuestos": propuestas, "chain_results": chain_results,
            "intencion": intencion, "restante_chat": limits.remaining(user["id"], "chat"),
            "verificacion": verificacion_info, "ruta": ruta_card, "lugares": lugares_card}


def _resultado_o(fut, default, nombre: str):
    """Resultado de una subtarea del chat; si falló, `default` (nunca un 500)."""
    if fut is None:
        return default
    try:
        return fut.result()
    except Exception:
        log.warning("chat_subtarea_fallo subtarea=%s", nombre, exc_info=True)
        return default


def _propuestas_de_chat(mensaje: str, trip: dict) -> list[dict]:
    """Extracción de planes: SOLO cuando el router dice que el usuario está
    agendando. Antes corría en CADA mensaje (una llamada LLM extra siempre); se
    salta en navegación, búsqueda, recomendación y charla → menos tokens y, de
    paso, mata el bug de "quiero volver al hotel" → "¿lo anoto?"."""
    from . import plans as plans_mod, timeutil
    propuestas = plans_mod.normalizar_lista(
        llm.extract_plans_from_chat(mensaje, trip), origen="chat")
    propuestas = plans_mod.limpiar_lugar_ciudad(propuestas, trip.get("ciudad", ""))
    propuestas = plans_mod.enriquecer_con_curacion(
        propuestas, db.places_for_city(trip.get("ciudad", "")), db.best_place_match)
    try:
        ahora = timeutil.now_local(trip)
        propuestas = plans_mod.rellenar_fechas(propuestas, mensaje, ahora)
    except Exception:
        pass
    return propuestas


def _buscar_cadena_chat(mensaje: str, trip: dict, user_id: str) -> dict | None:
    """Búsqueda en vivo de cadenas: SOLO si el router la pidió (no en cada mensaje)."""
    cadena_q = places_live.detectar_busqueda_cadena(mensaje)
    if not (cadena_q and places_live.disponible()):
        return None
    olat = trip.get("lat_actual")
    olng = trip.get("lng_actual")
    if olat is None or olng is None:
        coords = geo.resolve_origin(trip)
        if coords:
            olat, olng = coords
    if not (olat and olng):
        return None
    try:
        limits.check_and_consume(user_id, "maps")
    except limits.QuotaError:
        return {"query": cadena_q, "items": [],
                "nota": "Llegaste a tu límite de búsquedas en vivo por hoy. "
                        "Se renueva mañana."}
    resultados = places_live.buscar_cerca(cadena_q, olat, olng)
    return {"query": cadena_q, "items": resultados} if resultados else None


# ── Documentos ──
@app.post("/trips/{tid}/documents")
def upload_doc(tid: str, d: DocIn, user: dict = Depends(verified_user)):
    trip = own_trip(tid, user)
    _consume_or_429(user, "document")  # extracción/visión = LLM caro
    try:
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
    except Exception as e:
        # Proveedor caído/saturado: 503 claro (el front puede reintentar) en vez
        # de un 500 opaco. No guardamos un documento vacío en el contexto.
        log.warning("documento_llm_fallo trip=%s error=%s", tid, type(e).__name__)
        raise HTTPException(503, {"error": "llm_no_disponible",
                                  "mensaje": "No pude leer el documento ahora mismo. "
                                             "Intenta de nuevo en unos segundos."})
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
    if patch.get("ciudad") and not ciudades.activa(patch["ciudad"]):
        r["ciudad_no_disponible"] = {"ciudad": patch.pop("ciudad"),
                                     "mensaje": ciudades.mensaje_no_disponible(ti["ciudad"])}
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
    """Estado REAL: hace ping a la DB (503 si no responde, para que el balanceador
    / Railway lo detecte) y reporta scheduler y circuit breakers de los LLM.
    Mantiene todas las claves de antes."""
    db_estado = db.ping()
    breakers = llm.breaker_state()
    degradado = not db_estado["ok"] or any(v == "open" for v in breakers.values())
    cuerpo = {"status": "ok" if not degradado else "degraded",
            "db": db_estado, "scheduler": scheduler.running(), "llm_breakers": breakers,
            "llm_mode": llm.MODE, "search_mode": search.MODE,
            "transporte": "google" if transporte_mod.disponible() else "demo",
            "push_enabled": push.enabled(), "places_live": places_live.MODE,
            "email_mode": email_send.mode(),
            "limits": {"free": limits.FREE_LIMITS,
                       "max_trips": limits.MAX_TRIPS_PER_USER,
                       "global_maps_per_day": limits.GLOBAL_MAPS_PER_DAY,
                       "maps_used_today": (limits.global_used_today("maps")
                                           if db_estado["ok"] else None)}}
    if not db_estado["ok"]:
        return JSONResponse(cuerpo, status_code=503)
    return cuerpo


# ── Waitlist (landing) ──
class WaitlistIn(BaseModel):
    email: str = Field(..., max_length=254)
    name: str = Field("", max_length=200)
    phone: str = Field("", max_length=60)
    source: str = Field("", max_length=100)
    city: str = Field("", max_length=120)


def _client_ip(request: Request) -> str:
    """IP del cliente para rate limit anónimo. Detrás del proxy de Railway,
    request.client.host es la IP del PROXY (igual para todos), así que se usa el
    primer salto de X-Forwarded-For si viene. Es falsificable, pero aquí solo
    sirve para frenar spam del formulario (la dedup por email ya protege)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"


@app.post("/waitlist")
def waitlist_add(body: WaitlistIn, request: Request):
    """Alta pública desde la landing. Idempotente por email:
    {"ok": true, "status": "added" | "already"}."""
    if waitlist.rate_limited(_client_ip(request)):
        raise HTTPException(429, "Demasiados intentos desde esta red. Intenta más tarde.")
    try:
        return waitlist.add(
            body.email, name=body.name, phone=body.phone, source=body.source, city=body.city,
            user_agent=request.headers.get("user-agent", ""),
            referer=request.headers.get("referer", "") or request.headers.get("origin", ""),
        )
    except ValueError:
        raise HTTPException(400, "Email inválido.")


class WaitlistMeIn(BaseModel):
    momento: str = Field("", max_length=40)  # primer_viaje | dia2 | cierre | mas


MOMENTOS_LISTA = {"primer_viaje", "dia2", "cierre", "mas"}


@app.get("/waitlist/me")
def waitlist_me(user: dict = Depends(current_user)):
    """¿Este usuario ya está en la lista de espera? (La tarjeta de la app no se muestra si ya se unió.)"""
    return {"unido": waitlist.esta(user["email"])}


@app.post("/waitlist/me")
def waitlist_me_add(body: WaitlistMeIn, user: dict = Depends(current_user)):
    """Un toque desde la app: inscribe al usuario con los datos que ya dio al registrarse. Misma lista que la landing;
    `source` queda como app-<momento> para saber en qué momento convierte mejor."""
    momento = body.momento if body.momento in MOMENTOS_LISTA else "app"
    return waitlist.add(user["email"], name=user.get("nombre") or "", phone=user.get("telefono") or "",
                        source=(f"app-{momento}" + (f"-{user['hotel_origen']}" if user.get("hotel_origen") else ""))[:60],
                        city=user.get("pais") or "")


@app.get("/waitlist/count")
def waitlist_count():
    """Contador público para la landing (cacheado unos segundos)."""
    return {"count": waitlist.count()}


def _waitlist_admin(x_admin_token: str | None, token: str | None) -> None:
    """Admin de la waitlist: header X-Admin-Token o ?token= contra ADMIN_TOKEN.
    Sin ADMIN_TOKEN configurado → 503 (nunca quedan abiertos por error)."""
    if not waitlist.admin_configured():
        raise HTTPException(503, "Panel de waitlist no configurado (falta ADMIN_TOKEN).")
    if not waitlist.admin_ok(x_admin_token or token):
        raise HTTPException(403, "Acceso de administrador requerido.")


@app.get("/admin/encuestas")
def encuestas_admin(token: str | None = None, x_admin_token: str | None = Header(default=None)):
    _waitlist_admin(x_admin_token, token)
    with db.conn() as c:
        rs = [dict(r) for r in c.execute("SELECT * FROM encuesta_cierre ORDER BY created_at DESC").fetchall()]
    return {"count": len(rs), "encuestas": rs}


@app.get("/admin/waitlist")
def waitlist_admin_json(token: str | None = None,
                        x_admin_token: str | None = Header(default=None)):
    _waitlist_admin(x_admin_token, token)
    rows = waitlist.all_rows()
    return {"count": len(rows), "leads": rows}


@app.get("/admin/waitlist.csv")
def waitlist_admin_csv(token: str | None = None,
                       x_admin_token: str | None = Header(default=None)):
    _waitlist_admin(x_admin_token, token)
    return PlainTextResponse(waitlist.to_csv(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=waitlist.csv"})


@app.get("/admin/waitlist/panel")
def waitlist_admin_panel(token: str | None = None,
                         x_admin_token: str | None = Header(default=None)):
    _waitlist_admin(x_admin_token, token)
    return HTMLResponse(waitlist.panel_html(waitlist.all_rows()))


# ── Frontend / PWA ──
@app.get("/")
def frontend():
    """Sirve companion-agente.html desde la raíz del repo (mismo dominio que
    la API). Necesario para que navigator.geolocation funcione en el celular
    — el navegador exige contexto seguro (HTTPS o localhost); abrir el HTML
    como archivo local o desde otro dominio no cumple eso."""
    return FileResponse(os.path.join(_ROOT, "companion-agente.html"))


@app.get("/manifest.webmanifest", include_in_schema=False)
def pwa_manifest():
    """Manifest de la PWA. Los íconos van embebidos como data-URI dentro del
    archivo, así que no se necesita una carpeta de imágenes ni rutas extra."""
    return FileResponse(os.path.join(_ROOT, "manifest.webmanifest"),
                        media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Service worker servido desde la raíz para controlar todo el scope "/".
    'Cache-Control: no-cache' hace que el navegador detecte nuevas versiones
    del SW al recargar; 'Service-Worker-Allowed: /' fija el scope explícito."""
    return FileResponse(
        os.path.join(_ROOT, "sw.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )
