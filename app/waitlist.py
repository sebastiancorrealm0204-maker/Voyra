"""Waitlist del Companion — captura de leads desde la landing page.

Sigue el mismo patrón que el resto del backend: usa db.conn() (bilingüe
SQLite/Postgres), placeholders estilo "?", filas indexables por nombre.

Tabla:
- waitlist: un lead por fila, deduplicado por email. Guarda nombre, teléfono
  (con código de país), de dónde vino (source) y un poco de contexto.

Endpoints (se montan en main.py):
- POST /waitlist             -> alta de un lead (público, lo llama la landing)
- GET  /admin/waitlist       -> lista en JSON              (protegido con token)
- GET  /admin/waitlist.csv   -> descarga CSV               (protegido con token)
- GET  /admin/waitlist/panel -> mini panel HTML para verlos (protegido con token)

Protección del panel: header  X-Admin-Token  o query  ?token=...  que debe
coincidir con la variable de entorno ADMIN_TOKEN. Si ADMIN_TOKEN no está
seteada, los endpoints /admin/* responden 503 (no quedan abiertos por error).
"""
import csv
import io
import os
import re
import time

from . import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS waitlist (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT,
  phone TEXT,
  source TEXT,
  city TEXT,
  user_agent TEXT,
  referer TEXT,
  created_at REAL NOT NULL
);
"""


def init_waitlist():
    """Crea la tabla waitlist. Idempotente. Llamar en el startup de FastAPI,
    junto a db.init_db(). Incluye migración suave para SQLite por si la tabla
    ya existía sin la columna phone."""
    with db.conn() as c:
        c.executescript(SCHEMA)
        # Migración suave SOLO para SQLite (Postgres arranca con el esquema
        # completo). Si una BD local ya tenía la tabla sin 'phone', la agrega.
        if not db.IS_POSTGRES:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(waitlist)").fetchall()}
            if "phone" not in cols:
                c.execute("ALTER TABLE waitlist ADD COLUMN phone TEXT")


def add(email: str, name: str = "", phone: str = "", source: str = "",
        city: str = "", user_agent: str = "", referer: str = "") -> dict:
    """Inserta un lead. Devuelve {ok, status} donde status es 'added' o
    'already' (ya estaba ese email). Lanza ValueError si el email es inválido."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("email_invalido")

    with db.conn() as c:
        row = c.execute("SELECT id FROM waitlist WHERE email = ?", (email,)).fetchone()
        if row:
            return {"ok": True, "status": "already"}
        c.execute(
            "INSERT INTO waitlist (id, email, name, phone, source, city, user_agent, referer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.new_id(), email, (name or "").strip()[:120], (phone or "").strip()[:40],
             (source or "")[:60], (city or "")[:80], (user_agent or "")[:300],
             (referer or "")[:300], time.time()),
        )
    return {"ok": True, "status": "added"}


def count() -> int:
    with db.conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM waitlist").fetchone()
        return int(row["n"])


def all_rows() -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT email, name, phone, source, city, referer, created_at "
            "FROM waitlist ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "email": r["email"],
            "name": r["name"] or "",
            "phone": r["phone"] or "",
            "source": r["source"] or "",
            "city": r["city"] or "",
            "referer": r["referer"] or "",
            "created_at": r["created_at"],
            "fecha": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
        })
    return out


def to_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "nombre", "telefono", "source", "ciudad", "referer", "fecha"])
    for r in all_rows():
        w.writerow([r["email"], r["name"], r["phone"], r["source"], r["city"], r["referer"], r["fecha"]])
    return buf.getvalue()


def admin_ok(token: str | None) -> bool:
    """True si el token coincide con ADMIN_TOKEN. Si ADMIN_TOKEN no está
    configurada, devuelve False (los endpoints admin quedan cerrados)."""
    expected = os.environ.get("ADMIN_TOKEN", "").strip()
    if not expected:
        return False
    return (token or "").strip() == expected
