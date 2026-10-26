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
import html
import io
import os
import re
import threading
import time
from collections import deque

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
        # En Postgres, REAL es float4 (≈7 dígitos): un epoch como created_at
        # perdía precisión de minutos. Se traduce igual que el resto del esquema.
        c.executescript(db._schema_for_postgres(SCHEMA) if db.IS_POSTGRES else SCHEMA)
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

    # Idempotente y sin carrera: ON CONFLICT (email) DO NOTHING + rowcount.
    # Antes era SELECT-y-luego-INSERT: dos envíos simultáneos del mismo correo
    # (doble clic en la landing) chocaban con el UNIQUE → 500.
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO waitlist (id, email, name, phone, source, city, user_agent, referer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (email) DO NOTHING",
            (db.new_id(), email, (name or "").strip()[:120], (phone or "").strip()[:40],
             (source or "").strip()[:60], (city or "").strip()[:80], (user_agent or "")[:300],
             (referer or "")[:300], time.time()),
        )
        nuevo = cur.rowcount == 1
    if not nuevo:
        return {"ok": True, "status": "already"}
    _count_cache["exp"] = 0.0  # el contador público refleja el alta al instante
    return {"ok": True, "status": "added"}


# Contador público cacheado unos segundos: la landing lo pide en cada visita.
_COUNT_TTL_S = float(os.environ.get("WAITLIST_COUNT_TTL_S", "30"))
_count_cache = {"exp": 0.0, "n": 0}


def count(fresh: bool = False) -> int:
    key = "pg" if db.IS_POSTGRES else db.DB_PATH
    if not fresh and _count_cache.get("key") == key and _count_cache["exp"] > time.monotonic():
        return _count_cache["n"]
    with db.conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM waitlist").fetchone()
    n = int(row["n"])
    _count_cache.update(key=key, n=n, exp=time.monotonic() + _COUNT_TTL_S)
    return n


def reset_count_cache() -> None:
    _count_cache["exp"] = 0.0


# ── Rate limit por IP (en memoria, por proceso) ──
# limits.py controla cuotas por USUARIO autenticado; la landing es anónima, así
# que aquí basta una ventana deslizante por IP. Es por proceso (no compartida
# entre réplicas): el objetivo es frenar un script que spamee el formulario, no
# una contabilidad exacta. La dedup por email ya hace inofensivos los repetidos.
_RL_MAX = int(os.environ.get("WAITLIST_MAX_PER_IP", "10"))
_RL_WINDOW_S = float(os.environ.get("WAITLIST_WINDOW_S", "600"))
_rl_hits: dict[str, deque] = {}
_rl_lock = threading.Lock()


def rate_limited(ip: str) -> bool:
    """True si la IP superó _RL_MAX altas en la ventana. Registra el intento."""
    ahora = time.monotonic()
    with _rl_lock:
        q = _rl_hits.setdefault(ip or "unknown", deque())
        while q and q[0] < ahora - _RL_WINDOW_S:
            q.popleft()
        if len(q) >= _RL_MAX:
            return True
        q.append(ahora)
        # Higiene: no dejar crecer el dict con IPs viejas.
        if len(_rl_hits) > 10_000:
            for k in [k for k, v in _rl_hits.items() if not v or v[-1] < ahora - _RL_WINDOW_S]:
                _rl_hits.pop(k, None)
        return False


def reset_rate_limit() -> None:
    with _rl_lock:
        _rl_hits.clear()


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


def admin_configured() -> bool:
    return bool(os.environ.get("ADMIN_TOKEN", "").strip())


def panel_html(rows: list[dict]) -> str:
    """Mini panel HTML (solo lectura) para revisar los leads desde el celular."""
    filas = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(r[k] or ''))}</td>"
                         for k in ("fecha", "name", "email", "phone", "source", "city")) + "</tr>"
        for r in rows
    )
    return (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Waitlist Companion</title><style>"
        "body{font-family:system-ui,sans-serif;margin:16px;color:#0f172a;background:#fff}"
        "table{border-collapse:collapse;width:100%;font-size:14px}"
        "th,td{border-bottom:1px solid #e2e8f0;padding:6px 8px;text-align:left}"
        "th{background:#f8fafc}.wrap{overflow-x:auto}</style></head><body>"
        f"<h2>Waitlist · {len(rows)} leads</h2><div class='wrap'><table>"
        "<tr><th>Fecha</th><th>Nombre</th><th>Email</th><th>Teléfono</th><th>Source</th><th>Ciudad</th></tr>"
        f"{filas}</table></div></body></html>"
    )
