"""Cliente LLM multi-proveedor con routing por tarea (la arquitectura de la spec).

Proveedores soportados:
- anthropic : Claude (API nativa). Recomendado para CONVERSACIÓN — la voz del Companion.
- deepseek  : API compatible OpenAI. Recomendado para SCORING y EXTRACCIÓN (~$0.14/M).
- groq      : API compatible OpenAI, modelos abiertos muy rápidos. Alternativa para scoring.
- mock      : sin keys, respuestas determinísticas — todo el pipeline corre gratis.

Routing por variables de entorno (cada tarea puede ir a un proveedor distinto):
  SCORING_PROVIDER  = deepseek | groq | anthropic   (default: el más barato disponible)
  CHAT_PROVIDER     = anthropic | deepseek | groq   (default: anthropic si hay key)
  EXTRACT_PROVIDER  = deepseek | groq | anthropic   (default: el más barato disponible)

Keys:
  ANTHROPIC_API_KEY  (+ ANTHROPIC_MODEL, default claude-haiku-4-5)
  DEEPSEEK_API_KEY   (+ DEEPSEEK_MODEL,  default deepseek-chat)
  GROQ_API_KEY       (+ GROQ_MODEL,      default llama-3.3-70b-versatile)
"""
import json
import os

import httpx

PROVIDERS = {
    "anthropic": {
        "key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        "url": "https://api.anthropic.com/v1/messages",
        "style": "anthropic",
    },
    "deepseek": {
        "key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "url": "https://api.deepseek.com/chat/completions",
        "style": "openai",
    },
    "groq": {
        "key": os.environ.get("GROQ_API_KEY", ""),
        "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "style": "openai",
    },
}


def _available() -> list[str]:
    return [p for p, c in PROVIDERS.items() if c["key"]]


def _default_cheap() -> str:
    """Para scoring/extracción: el más barato disponible."""
    for p in ("deepseek", "groq", "anthropic"):
        if PROVIDERS[p]["key"]:
            return p
    return "mock"


def _default_chat() -> str:
    """Para conversación: Claude primero — la voz del Companion es el producto."""
    for p in ("anthropic", "deepseek", "groq"):
        if PROVIDERS[p]["key"]:
            return p
    return "mock"


SCORING_PROVIDER = os.environ.get("SCORING_PROVIDER") or _default_cheap()
CHAT_PROVIDER = os.environ.get("CHAT_PROVIDER") or _default_chat()
EXTRACT_PROVIDER = os.environ.get("EXTRACT_PROVIDER") or _default_cheap()

# Modelo de visión por proveedor (para leer imágenes de reservas). Configurable
# por env. Groq ofrece un modelo de visión en su free tier; por eso, por defecto,
# la extracción de imágenes usa Groq aunque el resto vaya a otro proveedor.
VISION_MODELS = {
    "groq": os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
    "anthropic": os.environ.get("ANTHROPIC_VISION_MODEL", "claude-haiku-4-5"),
    "deepseek": os.environ.get("DEEPSEEK_VISION_MODEL", ""),  # DeepSeek no tiene visión estable
}


def _vision_provider() -> str:
    """Proveedor para leer imágenes: el primero disponible que tenga visión.
    Groq primero (gratis), luego Anthropic. DeepSeek no aplica."""
    for p in ("groq", "anthropic"):
        if PROVIDERS[p]["key"] and VISION_MODELS.get(p):
            return p
    return "mock"


MODE = "mock" if not _available() else f"scoring={SCORING_PROVIDER}, chat={CHAT_PROVIDER}, extract={EXTRACT_PROVIDER}"


# Timeouts cortos + reintentos que RESPETAN el rate limit del proveedor.
#
# El problema real detrás de "se me cruzaron los cables" resultó ser doble:
#  1) Cold-start / timeout sin reintento (lo que arreglamos antes).
#  2) MÁS IMPORTANTE: 429 de Groq por exceder tokens-por-minuto (TPM). El free
#     tier de Groq da ~12K TPM, y el system prompt del Companion es grande, así
#     que un par de llamadas por turno revientan el minuto. El dashboard de Groq
#     lo confirma: picos de 17K tokens contra una línea roja de 12K.
#
# Por eso un backoff fijo de 0.6s no basta para un 429 de TPM: hay que esperar a
# que el proveedor diga cuándo (header 'retry-after'), o saltar a otro proveedor.
_TIMEOUT = httpx.Timeout(connect=8.0, read=45.0, write=10.0, pool=8.0)
_REINTENTOS = 2  # 3 intentos en total
_STATUS_REINTENTABLES = {408, 409, 429, 500, 502, 503, 504, 529}
# Tope de espera por reintento: nunca bloqueamos al usuario más de esto. Si el
# proveedor pide esperar más, mejor fallar/saltar de proveedor que congelar el chat.
_MAX_ESPERA_429 = 6.0


def _espera_tras_429(resp: httpx.Response, intento: int) -> float:
    """Cuánto esperar antes de reintentar. Prioriza el header 'retry-after' del
    proveedor (Groq lo manda en sus 429); si no está, backoff exponencial. Topado
    a _MAX_ESPERA_429 para no congelar el chat."""
    ra = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset-tokens")
    if ra:
        try:
            return min(float(ra), _MAX_ESPERA_429)
        except ValueError:
            pass
    return min(0.6 * (2 ** intento), _MAX_ESPERA_429)


class RateLimited(Exception):
    """El proveedor devolvió 429 y se agotaron los reintentos. La capa de arriba
    puede capturarla para intentar OTRO proveedor (failover)."""


def _post_con_reintentos(url: str, headers: dict, payload: dict) -> httpx.Response:
    """POST con reintentos ante errores transitorios. Lanza la última excepción
    si se agotan los intentos. En 429 persistente lanza RateLimited para permitir
    failover de proveedor arriba."""
    import time as _time
    ultima_exc: Exception | None = None
    for intento in range(_REINTENTOS + 1):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 429:
                if intento < _REINTENTOS:
                    _time.sleep(_espera_tras_429(resp, intento))
                    continue
                raise RateLimited("429 tras reintentos")
            if resp.status_code in _STATUS_REINTENTABLES and intento < _REINTENTOS:
                _time.sleep(min(0.6 * (2 ** intento), _MAX_ESPERA_429))
                continue
            resp.raise_for_status()
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as e:
            ultima_exc = e
            if intento < _REINTENTOS:
                _time.sleep(0.6 * (intento + 1))
                continue
            raise
        except httpx.HTTPStatusError as e:
            # 4xx no reintentables (400, 401, 403): fallan de inmediato.
            ultima_exc = e
            raise
    if ultima_exc:
        raise ultima_exc
    raise RuntimeError("post sin respuesta")  # inalcanzable


def _call_one(provider: str, system: str, messages: list[dict], max_tokens: int = 800) -> str:
    cfg = PROVIDERS[provider]
    if cfg["style"] == "anthropic":
        resp = _post_con_reintentos(
            cfg["url"],
            {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {"model": cfg["model"], "max_tokens": max_tokens, "system": system,
             "messages": messages},
        )
        return "".join(b.get("text", "") for b in resp.json()["content"] if b.get("type") == "text")
    # estilo OpenAI (DeepSeek, Groq): el system va como primer mensaje
    resp = _post_con_reintentos(
        cfg["url"],
        {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
        {"model": cfg["model"], "max_tokens": max_tokens,
         "messages": [{"role": "system", "content": system}] + messages},
    )
    return resp.json()["choices"][0]["message"]["content"]


def _orden_failover(primario: str) -> list[str]:
    """Proveedor primario primero, luego el resto de los disponibles como respaldo.
    Así, si Groq está rate-limited (429 de TPM), el chat salta a DeepSeek/Anthropic
    en vez de mostrarle un error al usuario."""
    resto = [p for p in _available() if p != primario]
    return ([primario] if primario in PROVIDERS and PROVIDERS[primario]["key"] else []) + resto


def _call(provider: str, system: str, messages: list[dict], max_tokens: int = 800) -> str:
    """Llama al proveedor con FAILOVER: si el primario está rate-limited, intenta
    con el siguiente disponible. Solo hacemos failover ante RateLimited (429), no
    ante errores de credenciales (no tendría sentido reintentar un 401 en otro)."""
    cadena = _orden_failover(provider)
    if not cadena:
        # sin keys: caemos al comportamiento mock de cada función de arriba
        return _call_one(provider, system, messages, max_tokens)
    ultima: Exception | None = None
    for p in cadena:
        try:
            return _call_one(p, system, messages, max_tokens)
        except RateLimited as e:
            ultima = e
            continue  # proveedor saturado → probar el siguiente
    # todos saturados
    if ultima:
        raise ultima
    raise RuntimeError("sin proveedores")


def _call_vision(provider: str, system: str, prompt: str, image_data_url: str,
                 max_tokens: int = 800) -> str:
    """Llamada multimodal: una imagen + texto. Solo proveedores estilo OpenAI con
    modelo de visión (Groq llama-3.2/4 vision). El modelo de visión se toma de
    {PROVIDER}_VISION_MODEL si está, si no se intenta con el modelo de chat."""
    cfg = PROVIDERS[provider]
    if cfg["style"] == "anthropic":
        # data URL → bloque image base64 de Anthropic
        media, b64 = _split_data_url(image_data_url)
        resp = httpx.post(
            cfg["url"],
            headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": VISION_MODELS.get(provider, cfg["model"]), "max_tokens": max_tokens,
                  "system": system,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                      {"type": "text", "text": prompt}]}]},
            timeout=120,
        )
        resp.raise_for_status()
        return "".join(b.get("text", "") for b in resp.json()["content"] if b.get("type") == "text")
    resp = httpx.post(
        cfg["url"],
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
        json={"model": VISION_MODELS.get(provider, cfg["model"]), "max_tokens": max_tokens,
              "messages": [
                  {"role": "system", "content": system},
                  {"role": "user", "content": [
                      {"type": "text", "text": prompt},
                      {"type": "image_url", "image_url": {"url": image_data_url}}]}]},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _split_data_url(data_url: str) -> tuple[str, str]:
    """'data:image/png;base64,XXXX' → ('image/png', 'XXXX')."""
    try:
        head, b64 = data_url.split(",", 1)
        media = head.split(":", 1)[1].split(";", 1)[0]
        return media, b64
    except (ValueError, IndexError):
        return "image/jpeg", data_url


def _parse_json(text: str) -> dict:
    return json.loads(text.replace("```json", "").replace("```", "").strip())


# ── Scoring del motor de relevancia (tarea barata, alto volumen → DeepSeek/Groq) ──
_MOCK_SCORES = {
    "vuelo": (95, "Evento operativo de vuelo: prioridad absoluta."),
    "seguridad": (92, "Alerta de seguridad: prioridad absoluta."),
    "itinerario": (80, "Actividad reservada del usuario: alta relevancia."),
    "clima": (74, "Afecta el plan de mañana del usuario."),
    "zona": (72, "El usuario está explorando una zona con opciones afines a sus gustos."),
    "recomendacion": (76, "Cruza con los gustos declarados y la zona actual."),
}


def score_event(system: str, source: str, payload: str, category: str,
                extra: dict | None = None) -> dict:
    if SCORING_PROVIDER == "mock" or not _available():
        score, reason = _MOCK_SCORES.get(category, (35, "Sin cruce claro con el contexto del viaje."))
        return {"score": score, "razon": reason, "titulo": f"[demo] señal de {category}",
                "mensaje": payload[:140], "accion": "Ver opciones" if score >= 70 else None,
                "_mode": "mock"}

    # Si la señal viene con un lugar curado (place_name + distancia_km calculados por
    # Haversine desde destination_places), se lo damos explícitamente al LLM para que
    # lo use en "titulo" y "mensaje" SIN inventar nombres, distancias ni URLs.
    lugar_block = ""
    if extra and extra.get("place_name"):
        dist = extra.get("distancia_km", "")
        dist_str = f" · {dist} km" if dist else ""
        lugar_block = (
            f"\nLUGAR CURADO (usa ESTE nombre y distancia — NO inventes otros): "
            f"{extra['place_name']}{dist_str}\n"
        )
    else:
        # Sin lugar curado: el LLM NO debe inventar nombres de sitios específicos.
        # El mensaje debe ser genérico y orientar al usuario a preguntar al Companion.
        lugar_block = (
            "\nIMPORTANTE: esta señal NO viene con un lugar verificado. "
            "NO menciones nombres de restaurantes, cafés, bares u otros sitios específicos en 'titulo' ni 'mensaje'. "
            "Habla en términos generales ('hay opciones cerca', 'la zona tiene buen ambiente') "
            "y anima al usuario a preguntarle al Companion para recomendaciones concretas.\n"
        )

    prompt = (
        f"Llegó esta señal de los watchers del servidor:\nFUENTE: {source}\nSEÑAL: {payload}\n"
        f"{lugar_block}\n"
        "Evalúa su relevancia para ESTE usuario en ESTE momento, cruzando con itinerario, gustos, "
        "ubicación y planes contados. Responde SOLO JSON válido sin markdown: "
        '{"score": <0-100>, "razon": "<1 frase>", "titulo": "<máx 6 palabras>", '
        '"mensaje": "<máx 2 frases, específico, con datos del contexto>", '
        '"accion": "<botón 1 tap máx 4 palabras o null>"}\n'
        "Criterios: vuelo/seguridad = score alto siempre. Recomendaciones = alto solo si cruzan "
        "con gustos y ubicación. ≥70 push, 40-69 feed, <40 silencio.\n"
        "REGLA DE HONESTIDAD (CRÍTICA): en 'mensaje' describe el lugar SOLO con lo que dice la "
        "SEÑAL/descripción curada. NO afirmes que el lugar encaja con un gusto del usuario salvo "
        "que la descripción lo respalde de verdad. Por ejemplo, NO digas 'perfecto para tu gusto "
        "de gastronomía local' sobre un restaurante español, italiano o internacional — eso NO es "
        "comida local. Si el lugar coincide con un gusto, nómbralo con precisión ('cocina española', "
        "'historia y cultura'); si no coincide claramente con ningún gusto declarado, simplemente "
        "describe el lugar por lo que es, sin inventar un match. Inventar afinidades destruye la confianza."
    )
    out = _parse_json(_call(SCORING_PROVIDER, system, [{"role": "user", "content": prompt}]))
    out["_mode"] = SCORING_PROVIDER
    return out


# ── Chat conversacional (la voz del Companion → Claude por defecto) ──
def chat(system: str, history: list[dict]) -> str:
    if CHAT_PROVIDER == "mock" or not _available():
        last = history[-1]["content"].lower() if history else ""
        if "perdido" in last:
            return ("[demo] Tranquilo, estás en tu zona actual. Opciones para volver al hotel: a pie (~20 min), "
                    "taxi oficial (~$10-15.000), o app (Uber/inDrive). Con esta hora te recomiendo el taxi. "
                    "¿Vas con prisa?")
        if "emergencia" in last or "duele" in last:
            return "[demo] Llama YA al número de emergencias local (123 en Colombia). La clínica con urgencias más cercana está a 5 min en taxi. ¿Puedes moverte?"
        return "[demo] ¡Anotado! ¿Algo en especial que tengas en mente, o te sorprendo con algo de la zona?"
    msgs = [{"role": ("user" if m["role"] == "user" else "assistant"), "content": m["content"]} for m in history]
    return _call(CHAT_PROVIDER, system, msgs, max_tokens=600)


# ── Extracción de documentos y planes (tarea barata → DeepSeek/Groq) ──
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _trip_ctx_block(trip: dict | None) -> str:
    """Da al extractor referencias de fecha YA RESUELTAS (hoy, mañana, y los
    próximos días de la semana con su fecha) para que solo tenga que copiar la
    fecha correcta en vez de calcularla. Resolver fechas relativas es donde más
    fallan los modelos pequeños, así que se lo damos hecho."""
    if not trip:
        return ""
    from datetime import timedelta
    from . import timeutil
    try:
        ahora = timeutil.now_local(trip)
    except Exception:
        ahora = None
    if ahora is None:
        return (
            f"\nCONTEXTO DEL VIAJE:\n- Ciudad: {trip.get('ciudad', '')}\n"
            f"- El viaje va del {trip.get('inicio', '?')} al {trip.get('fin', '?')}\n"
            "Si una fecha es relativa y no puedes resolverla, deja fecha en null.\n"
        )
    hoy = ahora.date()
    manana = hoy + timedelta(days=1)
    # mapa de los próximos 7 días: nombre del día → fecha
    proximos = []
    for i in range(0, 8):
        d = hoy + timedelta(days=i)
        nombre = _DIAS_ES[d.weekday()]
        etiqueta = "HOY" if i == 0 else ("MAÑANA" if i == 1 else f"próximo {nombre}")
        proximos.append(f"  · {etiqueta} = {d.isoformat()} ({nombre})")
    tabla = "\n".join(proximos)
    return (
        f"\nCONTEXTO DEL VIAJE Y FECHAS (úsalas para resolver referencias relativas):\n"
        f"- Ciudad destino: {trip.get('ciudad', '')}\n"
        f"- El viaje va del {trip.get('inicio', '?')} al {trip.get('fin', '?')}\n"
        f"- Fecha y hora local AHORA: {ahora.strftime('%Y-%m-%d %H:%M')}\n"
        f"- Tabla de equivalencias (NOMBRE = FECHA):\n{tabla}\n"
        "REGLA OBLIGATORIA: si el texto dice 'hoy', 'mañana', 'el viernes', 'este sábado', etc., "
        "USA la fecha exacta de la tabla de arriba. 'mañana' SIEMPRE es " + manana.isoformat() + ". "
        "Solo deja fecha en null si NO hay ninguna pista temporal en el texto.\n"
    )


_EXTRACT_SCHEMA = (
    'Responde SOLO JSON válido sin markdown con esta forma EXACTA:\n'
    '{\n'
    '  "tipo": "<hotel|vuelo|actividad|restaurante|transporte|otro>",\n'
    '  "resumen": "<2-4 frases con todos los datos concretos: nombres, códigos, direcciones, precios>",\n'
    '  "confirmacion": "<máx 2 frases cálidas confirmando qué entendiste>",\n'
    '  "trip_info": {\n'
    '    "ciudad": "<ciudad DESTINO del viaje, o null>",\n'
    '    "hotel": "<nombre del alojamiento, o null>",\n'
    '    "inicio": "<YYYY-MM-DD fecha de llegada/inicio del viaje, o null>",\n'
    '    "fin": "<YYYY-MM-DD fecha de regreso/fin, o null>",\n'
    '    "vuelo_ida": "<código del vuelo de ida, ej AV245, o null>",\n'
    '    "vuelo_regreso": "<código del vuelo de regreso, o null>"\n'
    '  },\n'
    '  "planes": [\n'
    '    {"fecha": "YYYY-MM-DD o null", "hora": "HH:MM o null", "titulo": "<corto>", '
    '"tipo": "<vuelo|hotel|actividad|restaurante|transporte|otro>", '
    '"detalle": "<datos útiles: código, dirección, terminal, etc.>", "lugar": "<nombre del lugar o null>"}\n'
    '  ]\n'
    '}\n'
    'En "trip_info" deduce los datos GENERALES del viaje SOLO si el documento los revela claramente '
    '(la ciudad destino, el hotel, las fechas del viaje, los vuelos). Si un dato no aparece, déjalo null. '
    'En "planes" pon UNA entrada por cada cosa con fecha/hora reservada (un vuelo = un plan con su fecha y hora; '
    'una estadía de hotel = un plan el día del check-in; un tour = un plan; una cena = un plan). '
    'Si el documento no contiene nada agendable, deja "planes" como lista vacía [].'
)


def _safe_extract(raw: str, filename: str) -> dict:
    try:
        out = _parse_json(raw)
    except Exception:
        return {"tipo": "otro", "resumen": f"Documento {filename} (no pude estructurarlo del todo).",
                "confirmacion": f"Guardé {filename} en tu viaje ✓", "planes": [], "trip_info": {}}
    out.setdefault("tipo", "otro")
    out.setdefault("resumen", f"Extraído de {filename}")
    out.setdefault("confirmacion", f"Leí {filename} y lo anoté ✓")
    out.setdefault("planes", [])
    out.setdefault("trip_info", {})
    if not isinstance(out["planes"], list):
        out["planes"] = []
    if not isinstance(out["trip_info"], dict):
        out["trip_info"] = {}
    return out


def extract_document(text_content: str, filename: str, trip: dict | None = None) -> dict:
    """Extrae datos + planes agendables de un documento de TEXTO (PDF→texto o .txt)."""
    if EXTRACT_PROVIDER == "mock" or not _available():
        return {"tipo": "actividad",
                "resumen": f"[demo] Extraído de {filename}: {text_content[:120]}",
                "confirmacion": f"Leí tu documento {filename} y lo anoté en tu viaje ✓",
                "trip_info": {},
                "planes": [{"fecha": None, "hora": None, "titulo": f"[demo] {filename}",
                            "tipo": "otro", "detalle": text_content[:80], "lugar": None}]}
    prompt = (
        f"Contenido de un documento de viaje del usuario ({filename}):\n---\n{text_content[:7000]}\n---\n"
        f"{_trip_ctx_block(trip)}"
        "Extrae TODA la info útil y los planes agendables. " + _EXTRACT_SCHEMA
    )
    raw = _call(EXTRACT_PROVIDER, "Eres el parser de documentos de viaje de Voyra.",
                [{"role": "user", "content": prompt}], max_tokens=1000)
    return _safe_extract(raw, filename)


def extract_document_image(image_data_url: str, filename: str, trip: dict | None = None) -> dict:
    """Igual que extract_document pero leyendo una IMAGEN (captura/foto de reserva)."""
    vp = _vision_provider()
    if vp == "mock":
        return {"tipo": "otro",
                "resumen": f"[demo] Imagen {filename} recibida (sin modelo de visión configurado).",
                "confirmacion": f"Recibí tu imagen {filename} ✓",
                "trip_info": {},
                "planes": []}
    prompt = (
        f"Esta imagen es una reserva/confirmación de viaje del usuario ({filename}). "
        "Léela con cuidado (puede ser una captura de pantalla o foto de un correo/PDF). "
        f"{_trip_ctx_block(trip)}"
        "Extrae TODA la info útil y los planes agendables. " + _EXTRACT_SCHEMA
    )
    raw = _call_vision(vp, "Eres el parser de documentos de viaje de Voyra.",
                       prompt, image_data_url, max_tokens=1000)
    return _safe_extract(raw, filename)


def extract_plans_from_chat(message: str, trip: dict | None = None) -> list[dict]:
    """Detecta planes agendables en UN mensaje de chat del usuario.

    Devuelve lista (posiblemente vacía) de planes SIN guardar. El endpoint decide
    si preguntar al usuario antes de persistir. Solo extrae cosas con intención de
    plan futuro ('el viernes tengo tour a las 9', 'reservé cena el sábado'),
    NO preguntas ('¿qué hago el viernes?') ni charla general."""
    if EXTRACT_PROVIDER == "mock" or not _available():
        # Heurística mínima para el modo demo/test: si menciona hora + actividad.
        low = message.lower()
        if any(k in low for k in ("reserv", "tour", "tengo", "cena", "vuelo")) and any(
                k in low for k in ("am", "pm", ":", "mañana", "hoy")):
            return [{"fecha": None, "hora": None, "titulo": message[:50],
                     "tipo": "otro", "detalle": "", "lugar": None}]
        return []
    prompt = (
        f"Mensaje del usuario en el chat:\n\"{message}\"\n"
        f"{_trip_ctx_block(trip)}"
        "¿El usuario está CONTANDO un PLAN o RESERVA futura concreta que YA decidió hacer "
        "(algo que va a hacer, idealmente con fecha/hora)? Si SÍ, extráelo. \n"
        "NO extraigas nada (devuelve lista vacía) si el mensaje es cualquiera de estos casos:\n"
        "- Una PREGUNTA o pedido de ayuda/indicaciones: '¿cómo llego a X?', '¿qué hay cerca?', "
        "'¿dónde queda Y?', 'llévame a', 'quiero ir al hotel/aeropuerto', 'quiero regresar al hotel', "
        "'estoy perdido', 'recomiéndame'. Pedir CÓMO llegar a un sitio NO es agendar un plan, es navegación.\n"
        "- Una necesidad inmediata o logística del momento (volver al hotel ahora, pedir un taxi, "
        "buscar un cajero/farmacia/supermercado). Eso se resuelve en el chat, NO se agenda.\n"
        "- Charla general, dudas, o comentarios sin un plan concreto.\n"
        "Solo cuenta como plan algo como: 'el viernes tengo tour a las 9', 'reservé cena en El Cielo el sábado', "
        "'mañana voy al Museo del Oro'. Es decir: una actividad/reserva con intención de agenda, no una pregunta.\n"
        "REGLAS para los campos:\n"
        "- \"lugar\": el NOMBRE ESPECÍFICO del sitio (ej. \"El Cielo\", \"Andrés Carne de Res\", "
        "\"Museo del Oro\"). NUNCA pongas la ciudad ni una zona genérica. Si el usuario no "
        "menciona un sitio concreto, usa null. JAMÁS pongas \"Bogotá\" como lugar. "
        "El hotel del usuario NO es un plan agendable: si dice que quiere ir/volver al hotel, NO lo agendes.\n"
        "- \"tipo\": clasifica por la actividad — una cena/almuerzo en un sitio = \"restaurante\"; "
        "un tour/museo/parque/paseo = \"actividad\"; un vuelo = \"vuelo\"; check-in de hotel = \"hotel\"; "
        "un traslado/taxi = \"transporte\". Solo usa \"otro\" si de verdad no encaja en ninguno.\n"
        "- \"titulo\": breve y claro (ej. \"Cena en El Cielo\").\n"
        'Responde SOLO JSON válido sin markdown: {"planes": [ '
        '{"fecha": "YYYY-MM-DD o null", "hora": "HH:MM o null", "titulo": "<corto>", '
        '"tipo": "<vuelo|hotel|actividad|restaurante|transporte|otro>", '
        '"detalle": "<datos útiles o vacío>", "lugar": "<nombre del sitio o null, NUNCA la ciudad>"} ] }'
    )
    try:
        out = _parse_json(_call(EXTRACT_PROVIDER, "Eres el detector de planes de viaje de Voyra.",
                                [{"role": "user", "content": prompt}], max_tokens=600))
        planes = out.get("planes", [])
        planes = planes if isinstance(planes, list) else []
        return _descartar_no_planes(planes, message)
    except Exception:
        return []


# Frases de navegación/ayuda que el extractor a veces marca como "plan" por error.
# Si el mensaje del usuario es una de estas intenciones, descartamos cualquier
# plan propuesto: pedir cómo llegar a un sitio NO es agendarlo. Esto evita el bug
# de "quiero volver al hotel" → "¿lo anoto en tu calendario?".
_INTENCIONES_NAVEGACION = (
    "como llego", "cómo llego", "como llegar", "cómo llegar", "como voy", "cómo voy",
    "como vuelvo", "cómo vuelvo", "como regreso", "cómo regreso", "llevame", "llévame",
    "quiero ir al hotel", "quiero volver al hotel", "quiero regresar al hotel",
    "volver al hotel", "regresar al hotel", "ir al hotel", "al aeropuerto",
    "estoy perdido", "donde queda", "dónde queda", "donde esta", "dónde está",
    "que hay cerca", "qué hay cerca", "recomiendame", "recomiéndame", "donde hay",
    "dónde hay", "pedir un taxi", "pedir taxi", "como pido", "cómo pido",
)


def _descartar_no_planes(planes: list[dict], message: str) -> list[dict]:
    """Red de seguridad determinística: si el mensaje del usuario es claramente
    una pregunta de navegación o ayuda inmediata, NO proponemos agendar nada,
    aunque el LLM lo haya intentado. Pedir indicaciones no es crear un plan."""
    import unicodedata

    def _norm(s: str) -> str:
        return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()

    m = _norm(message)
    if any(_norm(frase) in m for frase in _INTENCIONES_NAVEGACION):
        return []
    # También: si el único "plan" es básicamente ir al hotel, descártalo.
    filtrados = []
    for p in planes:
        titulo = _norm(p.get("titulo", ""))
        lugar = _norm(p.get("lugar", ""))
        if "hotel" in titulo and any(v in titulo for v in ("volver", "regresar", "ir al", "vuelta")):
            continue
        if "aeropuerto" in titulo and p.get("tipo") == "transporte" and not p.get("hora"):
            continue
        filtrados.append(p)
    return filtrados
