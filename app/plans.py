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
