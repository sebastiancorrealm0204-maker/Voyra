"""Planes estructurados del viaje.

Antes `trip.planes` era una lista de strings sueltos sin fecha ni hora, así que
el calendario de Planes en el frontend nunca podía ubicarlos en un día. Ahora un
plan es un objeto:

    {
      "id":      "ab12cd34ef56",        # estable, para borrar/editar
      "fecha":   "2026-07-13",          # YYYY-MM-DD (hora local del destino)
      "hora":    "09:00" | None,        # HH:MM 24h, o None si es de día completo
      "titulo":  "Tour islas del Rosario",
      "tipo":    "actividad",           # vuelo|hotel|actividad|restaurante|transporte|otro
      "detalle": "Código XK29, salida muelle La Bodeguita",
      "lugar":   "Muelle La Bodeguita" | None,
      "origen":  "chat" | "documento" | "manual",
      "creado":  1718400000.0
    }

Este módulo centraliza: normalizar planes (incluyendo los viejos en formato
string para no romper datos existentes), agruparlos por día y ordenarlos por
hora. Todo el resto de la app habla con planes a través de aquí.
"""
import time
import uuid

TIPOS_VALIDOS = {"vuelo", "hotel", "actividad", "restaurante", "transporte", "otro"}


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def normalizar_plan(p, *, origen: str = "manual") -> dict:
    """Convierte cualquier entrada de plan a la forma canónica.

    Acepta:
    - str (formato viejo): se guarda como titulo, sin fecha/hora.
    - dict parcial (lo que devuelve el extractor): se rellenan campos faltantes.
    """
    if isinstance(p, str):
        return {
            "id": _new_id(),
            "fecha": None,
            "hora": None,
            "titulo": p.strip(),
            "tipo": "otro",
            "detalle": "",
            "lugar": None,
            "origen": origen,
            "creado": time.time(),
        }
    tipo = (p.get("tipo") or "otro").strip().lower()
    if tipo not in TIPOS_VALIDOS:
        tipo = "otro"
    return {
        "id": p.get("id") or _new_id(),
        "fecha": _norm_fecha(p.get("fecha")),
        "hora": _norm_hora(p.get("hora")),
        "titulo": (p.get("titulo") or p.get("title") or "").strip() or "Plan",
        "tipo": tipo,
        "detalle": (p.get("detalle") or "").strip(),
        "lugar": (p.get("lugar") or None),
        "origen": p.get("origen") or origen,
        "creado": p.get("creado") or time.time(),
    }


def _norm_fecha(f) -> str | None:
    """Acepta 'YYYY-MM-DD' y lo deja igual; cualquier otra cosa → None."""
    if not f or not isinstance(f, str):
        return None
    f = f.strip()
    # validación mínima: 2026-07-13
    parts = f.split("-")
    if len(parts) == 3 and len(parts[0]) == 4 and parts[0].isdigit():
        try:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            pass
    return None


def _norm_hora(h) -> str | None:
    """Acepta '9:00', '09:00', '21:30'; devuelve 'HH:MM' 24h o None."""
    if not h or not isinstance(h, str):
        return None
    h = h.strip().lower().replace(" ", "")
    ampm = None
    if h.endswith("am") or h.endswith("pm"):
        ampm = h[-2:]
        h = h[:-2]
    if ":" not in h:
        if h.isdigit():
            h = f"{h}:00"
        else:
            return None
    try:
        hh, mm = h.split(":")[:2]
        hh, mm = int(hh), int(mm)
    except (ValueError, IndexError):
        return None
    if ampm == "pm" and hh < 12:
        hh += 12
    if ampm == "am" and hh == 12:
        hh = 0
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None


def normalizar_lista(planes: list, *, origen: str = "manual") -> list[dict]:
    return [normalizar_plan(p, origen=origen) for p in (planes or [])]


def ordenar(planes: list[dict]) -> list[dict]:
    """Por fecha, luego por hora (los sin hora van primero en su día)."""
    def clave(p):
        return (
            p.get("fecha") or "9999-99-99",
            p.get("hora") or "00:00",
            p.get("creado") or 0,
        )
    return sorted(planes, key=clave)


def agrupar_por_dia(planes: list[dict]) -> dict[str, list[dict]]:
    """{ 'YYYY-MM-DD': [planes...] }. Los sin fecha van bajo la clave 'sin_fecha'."""
    out: dict[str, list[dict]] = {}
    for p in ordenar(planes):
        clave = p.get("fecha") or "sin_fecha"
        out.setdefault(clave, []).append(p)
    return out


def resumen_para_prompt(planes: list[dict]) -> str:
    """Texto compacto de los planes para inyectar en el system prompt del chat."""
    if not planes:
        return ""
    lineas = []
    for p in ordenar(planes):
        cuando = p.get("fecha") or "fecha por confirmar"
        if p.get("hora"):
            cuando += f" {p['hora']}"
        extra = f" — {p['detalle']}" if p.get("detalle") else ""
        lugar = f" @ {p['lugar']}" if p.get("lugar") else ""
        lineas.append(f"- [{cuando}] {p['titulo']} ({p['tipo']}){lugar}{extra}")
    return "\n".join(lineas)


# ── Red de seguridad: resolver fechas relativas en backend ──
_DIAS_ES = {
    "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6,
}


def resolver_fecha_relativa(texto: str, ahora) -> str | None:
    """Dado un texto y la fecha/hora local actual (datetime), intenta deducir
    una fecha YYYY-MM-DD a partir de 'hoy', 'mañana', 'pasado mañana' o un día
    de la semana ('el viernes'). Devuelve None si no hay pista clara.

    Es una RED DE SEGURIDAD para cuando el LLM no resuelve la fecha él mismo.
    """
    if not texto or ahora is None:
        return None
    from datetime import timedelta
    t = texto.lower()
    hoy = ahora.date()
    if "pasado mañana" in t or "pasado manana" in t:
        return (hoy + timedelta(days=2)).isoformat()
    if "mañana" in t or "manana" in t:
        return (hoy + timedelta(days=1)).isoformat()
    if "hoy" in t or "esta noche" in t or "más tarde" in t or "mas tarde" in t:
        return hoy.isoformat()
    # día de la semana: "el viernes", "este sábado" → el próximo que caiga
    for nombre, wd in _DIAS_ES.items():
        if nombre in t:
            delta = (wd - hoy.weekday()) % 7
            if delta == 0:
                delta = 7  # "el viernes" dicho un viernes = el siguiente
            return (hoy + timedelta(days=delta)).isoformat()
    return None


def rellenar_fechas(planes: list[dict], texto: str, ahora) -> list[dict]:
    """Para los planes sin fecha, intenta deducirla del texto de origen."""
    fecha = resolver_fecha_relativa(texto, ahora)
    if not fecha:
        return planes
    for p in planes:
        if not p.get("fecha"):
            p["fecha"] = fecha
    return planes


def limpiar_lugar_ciudad(planes: list[dict], city: str) -> list[dict]:
    """Anula el campo 'lugar' si es igual a la ciudad (o una zona genérica).

    El extractor a veces pone la ciudad ("Bogotá") como lugar; eso no es un
    sitio concreto y rompe el matching/maps. Lo dejamos en None para que el
    link use el título o caiga al fallback de ciudad correctamente.
    """
    import unicodedata
    def norm(s):
        return unicodedata.normalize("NFKD", (s or "").strip().lower()) \
            .encode("ascii", "ignore").decode()
    c = norm(city)
    genericos = {c, "centro", "centro historico", "zona t", "zona g",
                 "zona rosa", "chapinero", "la candelaria", "colombia"}
    for p in planes:
        if norm(p.get("lugar")) in genericos:
            p["lugar"] = None
    return planes


# Mapea la categoría curada (seed_data) al tipo de plan
_CAT_A_TIPO = {
    "restaurante": "restaurante", "bar": "restaurante", "cafe": "restaurante",
    "mercado": "actividad", "parque": "actividad", "mirador": "actividad",
    "atraccion": "actividad", "experiencia": "actividad", "excursion": "actividad",
    "compras": "actividad",
}


def enriquecer_con_curacion(planes: list[dict], places: list[dict],
                            best_match_fn) -> list[dict]:
    """Si un plan coincide con un lugar curado, corrige tipo y normaliza el
    nombre del lugar. Así 'cena en el cielo' (que el LLM marcó 'otro') queda
    como 'restaurante' con lugar canónico, y el ícono sale bien.

    best_match_fn(texto, places) -> dict|None  (inyectado para evitar import
    circular con db).
    """
    for p in planes:
        textos = [t for t in (p.get("lugar"), p.get("titulo")) if t and t.strip()]
        m = None
        for t in textos:
            m = best_match_fn(t, places)
            if m:
                break
        if not m:
            continue
        cat = (m.get("category") or "").lower()
        if p.get("tipo") in (None, "", "otro") and cat in _CAT_A_TIPO:
            p["tipo"] = _CAT_A_TIPO[cat]
        # Si el lugar quedó vacío (lo limpiamos por ser ciudad) pero hay match,
        # usamos un nombre corto y limpio del lugar curado.
        if not p.get("lugar"):
            nombre = m.get("name", "")
            # quitar sufijo de ciudad redundante ("El Cielo Bogotá" -> "El Cielo")
            for suf in (" Bogotá", " (BBC)"):
                if nombre.endswith(suf):
                    nombre = nombre[: -len(suf)]
            p["lugar"] = nombre.strip() or None
    return planes
