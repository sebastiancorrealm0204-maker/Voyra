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
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

# Ruta de la DB configurable por entorno. En Railway (y cualquier host con
# sistema de archivos efímero) hay que apuntar a un VOLUMEN PERSISTENTE, si no
# los trips/planes/documentos del usuario se borran en cada deploy o reinicio.
#
# Cómo configurarlo en Railway:
#   1. En el servicio, crea un Volume y móntalo (p. ej. en "/data").
#   2. Agrega la variable de entorno:  DB_PATH=/data/voyra.db
# Sin volumen, la DB vive en el contenedor efímero y se pierde al redeploy.
DB_PATH = os.environ.get("DB_PATH", "voyra.db")

# Si la ruta apunta a un directorio (volumen) que aún no existe, créalo.
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

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
  dir TEXT,                       -- dirección exacta (Calle/Carrera #...) para geocodificar el deep link
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
CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint TEXT PRIMARY KEY,    -- único por dispositivo/navegador
  trip_id TEXT NOT NULL,
  subscription TEXT NOT NULL,   -- JSON completo {endpoint, keys:{p256dh, auth}}
  created_at REAL NOT NULL
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
        if "dir" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN dir TEXT")

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


# ── Planes estructurados (ver plans.py para la forma canónica) ──
def get_plans(tid: str) -> list[dict]:
    """Devuelve los planes del viaje SIEMPRE normalizados. Migra de forma
    transparente los planes en formato viejo (lista de strings) la primera vez
    que se leen, persistiéndolos ya normalizados."""
    from . import plans  # import local para evitar ciclo
    t = get_trip(tid)
    if not t:
        raise KeyError(tid)
    crudos = t.get("planes", []) or []
    normalizados = plans.normalizar_lista(crudos)
    # Si había strings viejos, persistimos la versión normalizada una vez.
    if crudos and any(isinstance(p, str) for p in crudos):
        update_trip(tid, {"planes": normalizados})
    return normalizados


def add_plans(tid: str, nuevos: list[dict], *, origen: str = "manual") -> list[dict]:
    """Agrega uno o varios planes ya normalizados y devuelve la lista completa."""
    from . import plans
    actuales = get_plans(tid)
    actuales += plans.normalizar_lista(nuevos, origen=origen)
    update_trip(tid, {"planes": actuales})
    return actuales


def delete_plan(tid: str, plan_id: str) -> list[dict]:
    actuales = get_plans(tid)
    restantes = [p for p in actuales if p.get("id") != plan_id]
    update_trip(tid, {"planes": restantes})
    return restantes


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


def norm_place(name: str) -> str:
    """Normaliza un nombre de lugar para matching robusto.

    Quita tildes, pasa a minúsculas y elimina todo lo que no sea letra o
    número (espacios, puntuación, etc.). Así 'El Cielo', 'ElCielo',
    'El cielo' y 'el  cielo!' colapsan al mismo valor 'elcielo'.
    """
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum())


# Palabras vacías que no aportan a la identidad del lugar (no usar para matching).
# Incluye conectores, verbos de plan y categorías genéricas de lugar: dos sitios
# distintos pueden compartir 'museo' o 'parque' sin ser el mismo lugar.
_PLACE_STOPWORDS = {
    # conectores / artículos / preposiciones
    "el", "la", "los", "las", "de", "del", "en", "y", "a", "con", "para",
    "un", "una", "al", "por",
    # verbos / sustantivos de plan
    "cena", "almuerzo", "desayuno", "comida", "visita", "visitar", "tour",
    "paseo", "ir", "vamos", "plan", "reserva", "reservar", "entrada",
    # ubicación genérica
    "bogota", "bogotá", "colombia", "centro", "zona",
    # categorías genéricas de lugar (no distinguen un sitio de otro)
    "restaurante", "hotel", "bar", "cafe", "café", "coffee", "museo",
    "parque", "plaza", "mercado", "teatro", "club", "mina", "sal",
    "catedral", "iglesia", "cerro", "mirador", "tienda", "casa",
    "senderismo", "nocturna", "vida", "roasters",
}


def place_tokens(name: str) -> set[str]:
    """Tokens significativos de un nombre, normalizados y sin stopwords.

    'Cena en ElCielo' -> {'elcielo'} ; 'El Cielo Bogotá' -> {'cielo'}.
    Nota: 'ElCielo' (pegado) queda como un solo token, así que también se
    añade la versión sin espacios del nombre completo para cruzarlo.
    """
    raw = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    palabras = "".join(ch if ch.isalnum() else " " for ch in raw).split()
    toks = {w for w in palabras if w not in _PLACE_STOPWORDS and len(w) >= 3}
    return toks


def place_match_score(consulta: str, nombre_curado: str) -> float:
    """Puntúa qué tan probable es que 'consulta' se refiera a 'nombre_curado'.

    0.0 = sin relación. Valores mayores = match más fuerte. Pensado para
    elegir el MEJOR lugar entre varios candidatos (no el primero que pase),
    lo que evita falsos positivos por palabras compartidas genéricas.

    Robusto a tildes, mayúsculas, espacios y palabras de relleno:
    'el cielo', 'ElCielo', 'Cena en El Cielo' puntúan alto contra
    'El Cielo Bogotá'.
    """
    a, b = norm_place(consulta), norm_place(nombre_curado)
    if not a or not b:
        return 0.0
    # Igualdad exacta de la cadena completa normalizada -> match perfecto.
    if a == b:
        return 1.0

    ta, tb = place_tokens(consulta), place_tokens(nombre_curado)
    if not ta:
        # La consulta no tiene NINGÚN token distintivo (solo stopwords como
        # "Bogotá", "cena", "restaurante"). No es una referencia a un lugar
        # concreto: no debe coincidir con nada. Antes, "Bogotá" hacía match por
        # subcadena con "Bogotá Beer Company" y mandaba todo a BBC.
        return 0.0
    if not tb:
        # El nombre curado no tiene tokens distintivos (raro): apóyate en
        # contención de cadena completa, exigiendo solape sustancial.
        if len(a) >= 5 and (a in b or b in a):
            return 0.6
        return 0.0

    # Solape de tokens distintivos. Puntuamos por cobertura simétrica: cuánto
    # de la consulta Y del nombre curado cubren los tokens comunes. Así, ante
    # un token compartido ambiguo ('bolivar'), gana el candidato cuyo conjunto
    # de tokens queda más completamente cubierto por la consulta.
    comunes = ta & tb
    if comunes:
        cob_consulta = len(comunes) / len(ta)
        cob_curado = len(comunes) / len(tb)
        return 0.5 + 0.5 * (cob_consulta + cob_curado) / 2

    # Token pegado del usuario que contiene exactamente un token distintivo
    # del nombre curado: 'elcielo' contiene 'cielo'. Exige >=4 chars.
    for x in ta:
        for y in tb:
            if len(y) >= 4 and y in x:
                return 0.55
            if len(x) >= 4 and x in y:
                return 0.55
    return 0.0


def best_place_match(consulta: str, places: list[dict], minimo: float = 0.5) -> dict | None:
    """Devuelve el lugar curado que mejor coincide con 'consulta', o None.

    Recorre todos los candidatos y elige el de mayor puntuación, siempre que
    supere el umbral. Así 'Cena en El Cielo' resuelve a 'El Cielo Bogotá' y no
    al primer lugar que comparta una palabra genérica.
    """
    mejor, mejor_score = None, 0.0
    for p in places:
        s = place_match_score(consulta, p.get("name", ""))
        if s > mejor_score:
            mejor, mejor_score = p, s
    return mejor if mejor_score >= minimo else None


def place_matches(consulta: str, nombre_curado: str) -> bool:
    """Compat: ¿coinciden estos dos nombres? Umbral por defecto 0.5."""
    return place_match_score(consulta, nombre_curado) >= 0.5


def places_for_city(city: str) -> list[dict]:
    with conn() as c:
        rs = c.execute(
            "SELECT * FROM destination_places WHERE city=? ORDER BY confianza DESC, created_at ASC",
            (norm_city(city),),
        ).fetchall()
    return [dict(r) for r in rs]


def seed_destination_places(places: list[dict]):
    """Reemplaza la curación de la ciudad por la versión autoritativa del seed.

    destination_places es 100% propiedad del seed (nunca lo edita el usuario),
    así que la forma más robusta y simple es: borrar las filas curadas de la
    ciudad y reinsertarlas desde el código. Esto garantiza que:
      - no queden filas viejas con datos erróneos (p. ej. El Cielo con la
        dirección/coordenadas antiguas que apuntaban a otro lugar),
      - no se dupliquen lugares si el nombre cambió ligeramente entre versiones,
      - la curación en la DB siempre refleje exactamente el seed.

    Es seguro: solo toca destination_places. NUNCA borra trips, planes,
    documentos ni recomendaciones (place_recommendations referencia por nombre,
    y los nombres se conservan).
    """
    if not places:
        return
    city = norm_city(places[0]["city_display"])
    with conn() as c:
        c.execute("DELETE FROM destination_places WHERE city=?", (city,))
        for p in places:
            c.execute(
                "INSERT INTO destination_places (id, city, city_display, name, category, zona, lat, lng, descripcion, confianza, maps_query, dir, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), norm_city(p["city_display"]), p["city_display"], p["name"], p["category"],
                 p["zona"], p["lat"], p["lng"], p["descripcion"], p["confianza"],
                 p.get("maps_query"), p.get("dir"), now()),
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


# ── Suscripciones Web Push (VAPID) ──
def upsert_push_subscription(trip_id: str, endpoint: str, subscription_json: str) -> None:
    """Guarda o actualiza una suscripción push por endpoint (único por dispositivo)."""
    with conn() as c:
        c.execute(
            "INSERT INTO push_subscriptions (endpoint, trip_id, subscription, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET trip_id=excluded.trip_id, subscription=excluded.subscription",
            (endpoint, trip_id, subscription_json, now()),
        )


def list_push_subscriptions(trip_id: str) -> list[dict]:
    with conn() as c:
        rs = c.execute("SELECT endpoint, subscription FROM push_subscriptions WHERE trip_id=?", (trip_id,)).fetchall()
    return [{"endpoint": r["endpoint"], "subscription": r["subscription"]} for r in rs]


def delete_push_subscription(endpoint: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
