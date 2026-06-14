"""Persistencia del Companion: SQLite sin dependencias externas.

Tablas:
- trips: el Trip Context Store (un documento por viaje)
- events: toda señal que llega de los watchers
- decisions: log del motor de relevancia (filtro, score, decisión) — ES el dataset de entrenamiento futuro
- notifications: push/feed entregados al usuario, con feedback
- documents: fotos/PDFs subidos, ya extraídos a texto
- messages: historial de chat por viaje
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

DB_PATH = "voyra.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,            -- JSON: ciudad, hotel, fechas, vuelos, pais, gustos, zona_actual, planes, categorias_silenciadas
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  source TEXT NOT NULL,
  category TEXT NOT NULL,
  operational INTEGER NOT NULL,
  payload TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  filter_result TEXT NOT NULL,   -- 'pass' | razón de muerte en el filtro
  score INTEGER,
  reason TEXT,
  decision TEXT NOT NULL,        -- push | feed | silence
  llm_mode TEXT NOT NULL,        -- real | mock
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  kind TEXT NOT NULL,            -- push | feed
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  action TEXT,
  operational INTEGER NOT NULL,
  category TEXT NOT NULL,
  feedback TEXT,                 -- tapped | dismissed | not_interested
  extra TEXT,                    -- JSON: maps_link, place_name, distancia_km, etc.
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS destination_places (
  id TEXT PRIMARY KEY,
  city TEXT NOT NULL,            -- normalizado (sin acentos, minúsculas) para búsqueda
  city_display TEXT NOT NULL,    -- nombre bonito para mostrar
  name TEXT NOT NULL,
  category TEXT NOT NULL,        -- restaurante | atraccion
  zona TEXT NOT NULL,
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  descripcion TEXT NOT NULL,
  confianza TEXT NOT NULL,        -- muy_alta | alta | media_alta | media
  maps_query TEXT,                -- nombre buscable para landmarks (deep link a Maps); NULL = usar lat,lng
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS place_recommendations (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  place_name TEXT NOT NULL,       -- nombre tal cual en destination_places.name
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  filename TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  role TEXT NOT NULL,            -- user | companion
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS proactive_log (
  id TEXT PRIMARY KEY,
  trip_id TEXT NOT NULL,
  kind TEXT NOT NULL,           -- matutino | nocturno | vuelo_regreso | hora_salir:<plan>
  local_date TEXT NOT NULL,     -- YYYY-MM-DD en hora del destino (dedup por día local)
  created_at REAL NOT NULL,
  UNIQUE(trip_id, kind, local_date)
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        # Migración simple: si la BD ya existía con un esquema anterior,
        # agrega columnas nuevas sin borrar datos. CREATE TABLE IF NOT EXISTS
        # no modifica tablas que ya existen, así que esto cubre ese caso.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(notifications)").fetchall()}
        if "extra" not in cols:
            c.execute("ALTER TABLE notifications ADD COLUMN extra TEXT")
        dp_cols = {r["name"] for r in c.execute("PRAGMA table_info(destination_places)").fetchall()}
        if "maps_query" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN maps_query TEXT")

def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


# ── Trips ──
def create_trip(data: dict) -> str:
    tid = new_id()
    defaults = {"zona_actual": "En el hotel", "planes": [], "categorias_silenciadas": []}
    with conn() as c:
        c.execute("INSERT INTO trips VALUES (?,?,?)", (tid, json.dumps({**defaults, **data}), now()))
    return tid


def get_trip(tid: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM trips WHERE id=?", (tid,)).fetchone()
    return {"id": r["id"], **json.loads(r["data"])} if r else None


def list_trips() -> list[dict]:
    """Todos los viajes. Lo usa el scheduler para recorrer trips activos."""
    with conn() as c:
        rs = c.execute("SELECT * FROM trips ORDER BY created_at ASC").fetchall()
    return [{"id": r["id"], **json.loads(r["data"])} for r in rs]


def update_trip(tid: str, patch: dict):
    t = get_trip(tid)
    if not t:
        raise KeyError(tid)
    t.pop("id")
    t.update(patch)
    with conn() as c:
        c.execute("UPDATE trips SET data=? WHERE id=?", (json.dumps(t), tid))


# ── Genéricos ──
def insert(table: str, row: dict) -> str:
    rid = row.get("id") or new_id()
    row = {**row, "id": rid, "created_at": row.get("created_at", now())}
    cols = ",".join(row)
    qs = ",".join("?" * len(row))
    with conn() as c:
        c.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))
    return rid


def rows(table: str, trip_id: str, limit: int = 200) -> list[dict]:
    with conn() as c:
        rs = c.execute(
            f"SELECT * FROM {table} WHERE trip_id=? ORDER BY created_at ASC LIMIT ?", (trip_id, limit)
        ).fetchall()
    return [dict(r) for r in rs]


def pushes_today(trip_id: str) -> list[dict]:
    cutoff = now() - 86400
    with conn() as c:
        rs = c.execute(
            "SELECT * FROM notifications WHERE trip_id=? AND kind='push' AND operational=0 AND created_at>?",
            (trip_id, cutoff),
        ).fetchall()
    return [dict(r) for r in rs]


def set_feedback(notification_id: str, feedback: str) -> dict | None:
    with conn() as c:
        c.execute("UPDATE notifications SET feedback=? WHERE id=?", (feedback, notification_id))
        r = c.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
    return dict(r) if r else None


def not_interested_count(trip_id: str, category: str) -> int:
    with conn() as c:
        r = c.execute(
            "SELECT COUNT(*) n FROM notifications WHERE trip_id=? AND category=? AND feedback='not_interested'",
            (trip_id, category),
        ).fetchone()
    return r["n"]


# ── Destination places (curación, ver geo.py para distancias/links) ──
import unicodedata


def norm_city(city: str) -> str:
    """'Bogotá' / 'bogota' / 'BOGOTA' -> 'bogota'. Para que el matching no dependa de tildes/mayúsculas."""
    s = unicodedata.normalize("NFKD", city).encode("ascii", "ignore").decode()
    return s.strip().lower()


def places_for_city(city: str) -> list[dict]:
    with conn() as c:
        rs = c.execute(
            "SELECT * FROM destination_places WHERE city=? ORDER BY confianza DESC, created_at ASC",
            (norm_city(city),),
        ).fetchall()
    return [dict(r) for r in rs]


def seed_destination_places(places: list[dict]):
    """Idempotente: si ya hay lugares para esa ciudad, no vuelve a insertar."""
    if not places:
        return
    city = norm_city(places[0]["city_display"])
    with conn() as c:
        existing = c.execute("SELECT COUNT(*) n FROM destination_places WHERE city=?", (city,)).fetchone()["n"]
        if existing:
            return
        for p in places:
            c.execute(
                "INSERT INTO destination_places (id, city, city_display, name, category, zona, lat, lng, descripcion, confianza, maps_query, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), norm_city(p["city_display"]), p["city_display"], p["name"], p["category"],
                 p["zona"], p["lat"], p["lng"], p["descripcion"], p["confianza"], p.get("maps_query"), now()),
            )


# ── Dedup de /nearby: no repetir los mismos lugares dentro de la ventana ──
def recently_recommended(trip_id: str, hours: float = 24) -> set[str]:
    cutoff = now() - hours * 3600
    with conn() as c:
        rs = c.execute(
            "SELECT DISTINCT place_name FROM place_recommendations WHERE trip_id=? AND created_at>?",
            (trip_id, cutoff),
        ).fetchall()
    return {r["place_name"] for r in rs}


def mark_recommended(trip_id: str, place_name: str):
    insert("place_recommendations", {"trip_id": trip_id, "place_name": place_name})


# ── Dedup del scheduler proactivo: cada evento se dispara una vez por día local ──
def proactive_already_sent(trip_id: str, kind: str, local_date: str) -> bool:
    with conn() as c:
        r = c.execute(
            "SELECT 1 FROM proactive_log WHERE trip_id=? AND kind=? AND local_date=?",
            (trip_id, kind, local_date),
        ).fetchone()
    return r is not None


def mark_proactive_sent(trip_id: str, kind: str, local_date: str) -> bool:
    """Registra que se envió. Devuelve True si fue el primero (insertó), False si ya existía.
    El UNIQUE(trip_id, kind, local_date) hace esto atómico aun con corridas solapadas."""
    try:
        with conn() as c:
            c.execute(
                "INSERT INTO proactive_log (id, trip_id, kind, local_date, created_at) VALUES (?,?,?,?,?)",
                (new_id(), trip_id, kind, local_date, now()),
            )
        return True
    except sqlite3.IntegrityError:
        return False
