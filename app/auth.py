"""Autenticación de usuarios — el inicio de sesión del Companion.

Diseño minimal pero seguro para el MVP:
- Email + contraseña. Nada de OAuth todavía.
- Email VERIFICADO obligatorio antes de usar endpoints caros (LLM, Google Maps,
  Tavily). Esta es la barrera anti-abuso más barata: crear N cuentas exige N
  buzones reales. Sin esto, un script crea mil cuentas y multiplica las cuotas.
- Sesiones por token opaco (no JWT): un token aleatorio guardado en DB. Simple,
  revocable, sin dependencias de cripto extra.

Hashing de contraseña: PBKDF2-HMAC-SHA256 de la librería estándar (hashlib).
No metemos bcrypt/argon2 para no agregar dependencias nativas en Railway; PBKDF2
con 200k iteraciones es más que suficiente para el MVP y es 100% stdlib.

Todas las tablas viven en la MISMA SQLite del resto (app/db.py). Recuerda montar
un volumen persistente en Railway (DB_PATH=/data/voyra.db), si no los usuarios se
borran en cada deploy igual que los trips.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from . import db

# ── Parámetros ──
_PBKDF2_ITERATIONS = 200_000
_SESSION_TTL_DAYS = 30
_VERIFY_TTL_HOURS = 24
# Ventana y tope de creación de cuentas por IP (anti registro masivo).
_SIGNUP_WINDOW_SECONDS = 3600
_SIGNUP_MAX_PER_IP = int(os.environ.get("SIGNUP_MAX_PER_IP", "5"))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,          -- normalizado: minúsculas, sin espacios
  password_hash TEXT NOT NULL,         -- pbkdf2$iter$salt_hex$hash_hex
  email_verified INTEGER NOT NULL DEFAULT 0,
  plan TEXT NOT NULL DEFAULT 'free',   -- free | viajero | companion (futuro)
  created_at REAL NOT NULL,
  signup_ip TEXT,
  -- Datos globales del viajero (JSON de texto): nombre, contacto de emergencia,
  -- alergias, preferencias, nº de pasaporte… Sirven en TODOS sus viajes.
  profile TEXT,
  -- Datos del registro, visibles y filtrables en el Table Editor de Supabase.
  -- `telefono` va normalizado (+573001234567) y es ÚNICO: un celular, una cuenta.
  nombre TEXT,
  telefono TEXT,
  pais TEXT,
  -- Límites diarios POR USUARIO. NULL = usar el default del plan (env var).
  -- Un número aquí MANDA para ese usuario. Editable desde el Table Editor de
  -- Supabase: cambias el número, guardas, y aplica al instante sin redeploy.
  limit_maps INTEGER,
  limit_chat INTEGER,
  limit_document INTEGER,
  limit_scan INTEGER,
  limit_trips INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS email_verifications (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  expires_at REAL NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS signup_attempts (
  id TEXT PRIMARY KEY,
  ip TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_email_verif_user ON email_verifications(user_id);
CREATE INDEX IF NOT EXISTS idx_signup_ip ON signup_attempts(ip, created_at);
"""


def init_auth():
    with db.conn() as c:
        c.executescript(db._schema_for_postgres(SCHEMA) if db.IS_POSTGRES else SCHEMA)
        if db.IS_POSTGRES:
            # En Postgres, CREATE TABLE IF NOT EXISTS no modifica tablas existentes.
            # Agregamos las columnas nuevas con ALTER TABLE ... IF NOT EXISTS,
            # que es seguro de correr múltiples veces (no falla si ya existe).
            for col, decl in (("profile", "TEXT"), ("limit_maps", "INTEGER"),
                              ("limit_chat", "INTEGER"), ("limit_document", "INTEGER"),
                              ("limit_scan", "INTEGER"), ("limit_trips", "INTEGER"),
                              ("nombre", "TEXT"), ("telefono", "TEXT"), ("pais", "TEXT")):
                c.execute(
                    f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {decl}"
                )
        else:
            # Migración suave para SQLite local preexistente (PRAGMA + IF NOT EXISTS
            # no existe en SQLite, hay que consultar la tabla primero).
            trip_cols = {r["name"] for r in c.execute("PRAGMA table_info(trips)").fetchall()}
            if "user_id" not in trip_cols:
                c.execute("ALTER TABLE trips ADD COLUMN user_id TEXT")
            user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
            for col, decl in (("profile", "TEXT"), ("limit_maps", "INTEGER"),
                              ("limit_chat", "INTEGER"), ("limit_document", "INTEGER"),
                              ("limit_scan", "INTEGER"), ("limit_trips", "INTEGER"),
                              ("nombre", "TEXT"), ("telefono", "TEXT"), ("pais", "TEXT")):
                if col not in user_cols:
                    c.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        # Un celular, una cuenta. Los NULL no chocan entre sí (usuarios sin celular).
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telefono ON users(telefono)")


# ── Hashing de contraseña ──
def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ── Validación ──
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def valid_password(password: str) -> tuple[bool, str]:
    if not password or len(password) < 8:
        return False, "La contraseña debe tener al menos 8 caracteres."
    if len(password) > 200:
        return False, "Contraseña demasiado larga."
    return True, ""


# ── Anti registro masivo por IP ──
def signups_from_ip_recent(ip: str) -> int:
    cutoff = time.time() - _SIGNUP_WINDOW_SECONDS
    with db.conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n FROM signup_attempts WHERE ip=? AND created_at>?",
            (ip, cutoff),
        ).fetchone()
    return r["n"]


def record_signup_attempt(ip: str):
    db.insert("signup_attempts", {"ip": ip or "unknown"})


def ip_signup_blocked(ip: str) -> bool:
    return signups_from_ip_recent(ip) >= _SIGNUP_MAX_PER_IP


# ── Usuarios ──
def get_user_by_email(email: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (normalize_email(email),)).fetchone()
    return dict(r) if r else None


def get_user(user_id: str) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(r) if r else None


def normalize_phone(raw: str) -> str:
    """Deja solo dígitos y un '+' inicial. '' si no tiene al menos 7 dígitos."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7 or len(digits) > 15:
        return ""
    return ("+" if raw.startswith("+") else "") + digits


def get_user_by_phone(phone: str) -> dict | None:
    if not phone:
        return None
    with db.conn() as c:
        r = c.execute("SELECT * FROM users WHERE telefono=?", (phone,)).fetchone()
    return dict(r) if r else None


def create_user(email: str, password: str, ip: str = "", nombre: str = "",
                telefono: str = "", pais: str = "") -> dict:
    """Crea el usuario (sin verificar). Lanza ValueError si el email o el celular ya existen.
    `telefono` debe venir ya normalizado (normalize_phone) o vacío."""
    email = normalize_email(email)
    if get_user_by_email(email):
        raise ValueError("Ese email ya está registrado.")
    if telefono and get_user_by_phone(telefono):
        raise ValueError("Ese celular ya está registrado.")
    uid = db.new_id()
    with db.conn() as c:
        c.execute(
            "INSERT INTO users (id, email, password_hash, email_verified, plan, created_at, signup_ip, "
            "nombre, telefono, pais) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, email, _hash_password(password), 0, "free", time.time(), ip or "",
             (nombre or "").strip() or None, telefono or None, (pais or "").strip() or None),
        )
    return get_user(uid)


def verification_required() -> bool:
    """¿Se exige confirmar el email? Apagado por defecto; se enciende con
    REQUIRE_EMAIL_VERIFICATION=1 (leído en cada llamada, sin reiniciar tests/config)."""
    return os.environ.get("REQUIRE_EMAIL_VERIFICATION", "0") == "1"


def mark_email_verified(user_id: str):
    with db.conn() as c:
        c.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))


# ── Perfil global del viajero (datos de texto reutilizables en todos los viajes) ──
# Campos permitidos. Cualquier otra clave que mande el cliente se ignora (evita
# que se inyecten campos arbitrarios en el JSON).
PROFILE_FIELDS = (
    "nombre", "telefono", "contacto_emergencia", "alergias",
    "condiciones_medicas", "preferencias", "pasaporte", "notas",
)


def get_profile(user_id: str) -> dict:
    u = get_user(user_id)
    if not u:
        return {}
    raw = u.get("profile")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return {k: data.get(k, "") for k in PROFILE_FIELDS}
    except Exception:
        return {}


def set_profile(user_id: str, data: dict) -> dict:
    """Guarda solo los campos permitidos, como JSON de texto. Devuelve el perfil."""
    limpio = {k: str(data.get(k, "") or "").strip() for k in PROFILE_FIELDS}
    with db.conn() as c:
        c.execute("UPDATE users SET profile=? WHERE id=?", (json.dumps(limpio), user_id))
    return limpio


# ── Verificación de email ──
def create_verification_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.conn() as c:
        # Invalida tokens previos del usuario para que solo el último valga.
        c.execute("DELETE FROM email_verifications WHERE user_id=?", (user_id,))
        c.execute(
            "INSERT INTO email_verifications (token, user_id, expires_at, created_at) VALUES (?,?,?,?)",
            (token, user_id, now + _VERIFY_TTL_HOURS * 3600, now),
        )
    return token


def consume_verification_token(token: str) -> dict | None:
    """Valida y consume el token. Devuelve el usuario verificado o None."""
    with db.conn() as c:
        r = c.execute("SELECT * FROM email_verifications WHERE token=?", (token,)).fetchone()
        if not r or r["expires_at"] < time.time():
            return None
        uid = r["user_id"]
        c.execute("DELETE FROM email_verifications WHERE token=?", (token,))
    mark_email_verified(uid)
    return get_user(uid)


# ── Sesiones ──
def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db.conn() as c:
        c.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + _SESSION_TTL_DAYS * 86400),
        )
    return token


def user_for_session(token: str) -> dict | None:
    if not token:
        return None
    # Una sola query (JOIN) en vez de dos: corre en CADA request autenticado.
    with db.conn() as c:
        r = c.execute(
            "SELECT u.*, s.expires_at AS session_expires_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token=?", (token,)).fetchone()
    if not r or r["session_expires_at"] < time.time():
        return None
    u = dict(r)
    u.pop("session_expires_at", None)
    return u


def cleanup_expired() -> None:
    """Borra sesiones y tokens de verificación vencidos, e intentos de registro
    de más de un día (la ventana anti-abuso es de 1h). Lo llama el scheduler."""
    ahora = time.time()
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (ahora,))
        c.execute("DELETE FROM email_verifications WHERE expires_at < ?", (ahora,))
        c.execute("DELETE FROM signup_attempts WHERE created_at < ?", (ahora - 86400,))


def destroy_session(token: str):
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ── Login ──
def authenticate(email: str, password: str) -> dict | None:
    u = get_user_by_email(email)
    if not u or not _verify_password(password, u["password_hash"]):
        return None
    return u
