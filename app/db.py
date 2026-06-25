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

# ─────────────────────────────────────────────────────────────────────────────
# Capa de persistencia BILINGÜE: SQLite (local/tests) o Postgres/Supabase (prod).
#
# El motor se elige por entorno:
#   - Si existe DATABASE_URL  -> Postgres (Supabase). Producción.
#   - Si NO existe            -> SQLite en DB_PATH. Desarrollo y los 51 tests.
#
# TODO el resto del módulo (y auth.py, limits.py, main.py) sigue escribiendo SQL
# con placeholders estilo SQLite ("?") y leyendo filas como r["columna"]. Esta
# capa traduce eso al vuelo cuando el motor es Postgres:
#   - "?"  ->  "%s"           (en un wrapper de execute)
#   - filas indexables por nombre vía psycopg.rows.dict_row
# Así, cambiar de motor NO obliga a tocar las ~40 queries del código.
#
# Supabase: usa la connection string del POOLER (puerto 6543, transaction mode).
# El pool va con prepare_threshold=None porque ese pooler no soporta prepared
# statements en modo transaction.
# ─────────────────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = bool(DATABASE_URL)

# Ruta de la DB SQLite (solo aplica cuando NO hay DATABASE_URL).
DB_PATH = os.environ.get("DB_PATH", "voyra.db")
_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)

# El pool de Postgres se crea perezosamente la primera vez que se pide una
# conexión (o explícitamente con init_pool() en el startup de FastAPI).
_pg_pool = None


def init_pool():
    """Crea el ConnectionPool de Postgres. Idempotente. Llamar en el startup de
    FastAPI. En modo SQLite no hace nada."""
    global _pg_pool
    if not IS_POSTGRES or _pg_pool is not None:
        return _pg_pool
    from psycopg_pool import ConnectionPool
    from psycopg.rows import dict_row
    _pg_pool = ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=int(os.environ.get("PG_POOL_MAX", "10")),
        # El pooler de Supabase (transaction mode) no soporta prepared statements.
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
        open=True,
    )
    return _pg_pool


def close_pool():
    """Cierra el pool. Llamar en el shutdown de FastAPI."""
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.close()
        _pg_pool = None


class _PgCursor:
    """Envuelve un cursor de psycopg para que el resto del código siga usando
    placeholders estilo SQLite ('?'). Traduce '?' -> '%s' en cada execute.

    Ojo: solo toca los placeholders posicionales. El código de Voyra no usa '?'
    dentro de literales de cadena en su SQL, así que la traducción es segura.
    """

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        self._cur.execute(sql.replace("?", "%s"), params)
        return self

    def executescript(self, script):
        # psycopg ejecuta múltiples sentencias separadas por ';' en un execute.
        self._cur.execute(script)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount


class _PgConn:
    """Adapta una conexión psycopg a la mini-interfaz que usa el código
    (c.execute(...), c.executescript(...)), devolviendo cursores envueltos."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        cur = _PgCursor(self._raw.cursor())
        return cur.execute(sql, params)

    def executescript(self, script):
        cur = _PgCursor(self._raw.cursor())
        return cur.executescript(script)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,            -- JSON: ciudad, hotel, fechas, vuelos, pais, gustos, zona_actual, planes, categorias_silenciadas
  created_at REAL NOT NULL,
  user_id TEXT
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
  place_id TEXT,
  rating REAL,
  price_level TEXT,
  tags TEXT,                      -- JSON array de tags del vocabulario controlado (cruce directo con GUSTOS); NULL = sin tags
  local TEXT,                     -- JSON object con conocimiento local por lugar (clave, pedir/ver, momento, ojo, dato, practico); NULL = sin curación local
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


import re as _re


def _schema_for_postgres(sqlite_schema: str) -> str:
    """Adapta el SCHEMA de SQLite a tipos Postgres. Una sola fuente de verdad:
    el SCHEMA de arriba (SQLite); aquí solo se traducen los tipos que difieren.

    - REAL                 -> double precision
    Nota: 'operational' se mantiene como INTEGER (el código lo escribe y lee
    como 0/1 en engine.py); convertirlo a boolean obligaría a tocar engine.py y
    la query de pushes_today sin ganar nada. 'email_verified' igual: 0/1.
    Postgres entiende TEXT, INTEGER y PRIMARY KEY igual que SQLite; el UNIQUE(...)
    y el ON CONFLICT que usa el código son sintaxis común a ambos.
    """
    s = sqlite_schema
    # REAL -> double precision (created_at, lat, lng, rating).
    s = _re.sub(r"\bREAL\b", "double precision", s)
    return s


SCHEMA_PG = _schema_for_postgres(SCHEMA)


@contextmanager
def conn():
    """Context manager bilingüe. En Postgres toma una conexión del pool y la
    envuelve para mantener la interfaz c.execute('... ? ...', params) y filas
    indexables por nombre. En SQLite, comportamiento idéntico al original."""
    if IS_POSTGRES:
        pool = init_pool()
        with pool.connection() as raw:   # commit/rollback y devolución al pool automáticos
            yield _PgConn(raw)
    else:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init_db():
    """Crea el esquema. En Postgres usa los tipos adaptados y NO necesita las
    migraciones suaves por columna (PRAGMA): el esquema arranca completo. En
    SQLite mantiene las migraciones suaves para bases locales preexistentes."""
    if IS_POSTGRES:
        with conn() as c:
            c.executescript(SCHEMA_PG)
        # Migraciones suaves para Postgres: agrega columnas nuevas si la tabla
        # ya existía con un esquema anterior. Usa information_schema (SQL estándar).
        _pg_migrate_columns()
        return

    with conn() as c:
        c.executescript(SCHEMA)
        # Migración suave SOLO para SQLite: si una BD local ya existía con un
        # esquema anterior, agrega columnas nuevas sin borrar datos.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(notifications)").fetchall()}
        if "extra" not in cols:
            c.execute("ALTER TABLE notifications ADD COLUMN extra TEXT")
        dp_cols = {r["name"] for r in c.execute("PRAGMA table_info(destination_places)").fetchall()}
        if "maps_query" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN maps_query TEXT")
        if "dir" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN dir TEXT")
        if "place_id" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN place_id TEXT")
        if "rating" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN rating REAL")
        if "price_level" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN price_level TEXT")
        if "tags" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN tags TEXT")
        if "local" not in dp_cols:
            c.execute("ALTER TABLE destination_places ADD COLUMN local TEXT")


def _pg_migrate_columns():
    """Agrega columnas nuevas a tablas existentes en Postgres (idempotente).
    Usa information_schema para no depender de PRAGMA (SQLite-only).
    Agregar aquí cada columna nueva que se añada al SCHEMA."""
    # (tabla, columna, definición SQL)
    migrations = [
        ("destination_places", "maps_query",  "TEXT"),
        ("destination_places", "dir",         "TEXT"),
        ("destination_places", "place_id",    "TEXT"),
        ("destination_places", "rating",      "DOUBLE PRECISION"),
        ("destination_places", "price_level", "TEXT"),
        ("destination_places", "tags",        "TEXT"),
        ("destination_places", "local",       "TEXT"),
        ("notifications",      "extra",       "TEXT"),
    ]
    with conn() as c:
        for table, column, col_type in migrations:
            exists = c.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            if not exists:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


# ── Trips ──
def create_trip(data: dict, user_id: str | None = None) -> str:
    tid = new_id()
    defaults = {"zona_actual": "En el hotel", "planes": [], "categorias_silenciadas": [],
                "guia_vista": False}
    with conn() as c:
        c.execute("INSERT INTO trips (id, data, created_at, user_id) VALUES (?,?,?,?)",
                  (tid, json.dumps({**defaults, **data}), now(), user_id))
    return tid


def trip_owner(tid: str) -> str | None:
    """Devuelve el user_id dueño del trip, o None si no tiene dueño / no existe."""
    with conn() as c:
        r = c.execute("SELECT user_id FROM trips WHERE id=?", (tid,)).fetchone()
    return r["user_id"] if r else None


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
    """Normaliza el nombre de ciudad para matching robusto.

    Maneja todos los formatos que puede devolver el LLM al parsear una reserva:
      - 'Bogotá', 'bogota', 'BOGOTA'         → 'bogota'
      - 'Guadalajara, México'                 → 'guadalajara'
      - 'Guadalajara, Jalisco, México'        → 'guadalajara'
      - 'GDL' (código IATA)                  → 'guadalajara'
      - 'Ciudad de México' / 'CDMX'          → 'ciudad de mexico'
    """
    # Resolver códigos IATA y alias comunes ANTES de normalizar
    _ALIAS: dict[str, str] = {
        # México
        "gdl": "guadalajara", "mex": "ciudad de mexico", "cdmx": "ciudad de mexico",
        "cun": "cancun", "mty": "monterrey", "pbc": "puebla", "gro": "acapulco",
        "sjd": "los cabos", "zlo": "manzanillo", "pxm": "puerto escondido",
        # Colombia
        "bog": "bogota", "mde": "medellin", "clo": "cali", "ctg": "cartagena",
        "baq": "barranquilla", "pei": "pereira", "aoh": "armenia",
        # Argentina
        "eze": "buenos aires", "bue": "buenos aires",
        # Perú
        "lim": "lima",
        # Chile
        "scl": "santiago",
        # Aliases textuales comunes
        "ciudad de mexico": "ciudad de mexico",
        "mexico city": "ciudad de mexico",
        "cdmx": "ciudad de mexico",
    }
    raw = city.strip()
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().strip().lower()
    # Si viene con coma ("Guadalajara, México"), quedarse solo con la primera parte
    if "," in s:
        s = s.split(",")[0].strip()
    # Resolver alias/IATA después de quitar el sufijo de país
    if s in _ALIAS:
        return _ALIAS[s]
    return s


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
    out = []
    for r in rs:
        d = dict(r)
        # tags se guarda como JSON array; devolverlo ya parseado (o lista vacía)
        if d.get("tags"):
            try:
                d["tags"] = json.loads(d["tags"])
            except (ValueError, TypeError):
                d["tags"] = []
        else:
            d["tags"] = []
        # local se guarda como JSON object; devolverlo ya parseado (o None)
        if d.get("local"):
            try:
                d["local"] = json.loads(d["local"])
            except (ValueError, TypeError):
                d["local"] = None
        else:
            d["local"] = None
        out.append(d)
    return out


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
                "INSERT INTO destination_places (id, city, city_display, name, category, zona, lat, lng, descripcion, confianza, maps_query, dir, place_id, rating, price_level, tags, local, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), norm_city(p["city_display"]), p["city_display"], p["name"], p["category"],
                 p["zona"], p["lat"], p["lng"], p["descripcion"], p["confianza"],
                 p.get("maps_query"), p.get("dir"), p.get("place_id"),
                 p.get("rating"), p.get("price_level"),
                 json.dumps(p.get("tags"), ensure_ascii=False) if p.get("tags") else None,
                 json.dumps(p.get("local"), ensure_ascii=False) if p.get("local") else None,
                 now()),
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
    El UNIQUE(trip_id, kind, local_date) hace esto atómico aun con corridas solapadas.

    Usa INSERT ... ON CONFLICT DO NOTHING y mira rowcount (1=insertó, 0=ya existía).
    Esto funciona igual en SQLite y Postgres, y evita el viejo patrón de capturar
    IntegrityError: en Postgres ese error ABORTA la transacción (no así en SQLite),
    así que capturarlo dejaría la conexión envenenada. ON CONFLICT lo evita."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO proactive_log (id, trip_id, kind, local_date, created_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT (trip_id, kind, local_date) DO NOTHING",
            (new_id(), trip_id, kind, local_date, now()),
        )
        return cur.rowcount == 1


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
