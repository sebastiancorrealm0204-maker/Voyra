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
  signup_ip TEXT
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
"""


def init_auth():
    with db.conn() as c:
        c.executescript(SCHEMA)
        # Migración: si 'trips' ya existe sin user_id, agregar la columna.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(trips)").fetchall()}
        if "user_id" not in cols:
            c.execute("ALTER TABLE trips ADD COLUMN user_id TEXT")


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


def create_user(email: str, password: str, ip: str = "") -> dict:
    """Crea el usuario (sin verificar). Lanza ValueError si el email ya existe."""
    email = normalize_email(email)
    if get_user_by_email(email):
        raise ValueError("Ese email ya está registrado.")
    uid = db.new_id()
    with db.conn() as c:
        c.execute(
            "INSERT INTO users (id, email, password_hash, email_verified, plan, created_at, signup_ip) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, email, _hash_password(password), 0, "free", time.time(), ip or ""),
        )
    return get_user(uid)


def mark_email_verified(user_id: str):
    with db.conn() as c:
        c.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))


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
    with db.conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not r or r["expires_at"] < time.time():
            return None
    return get_user(r["user_id"])


def destroy_session(token: str):
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


# ── Login ──
def authenticate(email: str, password: str) -> dict | None:
    u = get_user_by_email(email)
    if not u or not _verify_password(password, u["password_hash"]):
        return None
    return u
