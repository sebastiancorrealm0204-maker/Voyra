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

MODE = "mock" if not _available() else f"scoring={SCORING_PROVIDER}, chat={CHAT_PROVIDER}, extract={EXTRACT_PROVIDER}"


def _call(provider: str, system: str, messages: list[dict], max_tokens: int = 800) -> str:
    cfg = PROVIDERS[provider]
    if cfg["style"] == "anthropic":
        resp = httpx.post(
            cfg["url"],
            headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": cfg["model"], "max_tokens": max_tokens, "system": system,
                  "messages": messages},
            timeout=60,
        )
        resp.raise_for_status()
        return "".join(b.get("text", "") for b in resp.json()["content"] if b.get("type") == "text")
    # estilo OpenAI (DeepSeek, Groq): el system va como primer mensaje
    resp = httpx.post(
        cfg["url"],
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
        json={"model": cfg["model"], "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system}] + messages},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


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
        "con gustos y ubicación. ≥70 push, 40-69 feed, <40 silencio."
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


# ── Extracción de documentos (tarea barata → DeepSeek/Groq) ──
def extract_document(text_content: str, filename: str) -> dict:
    if EXTRACT_PROVIDER == "mock" or not _available():
        return {"tipo": "actividad", "resumen": f"[demo] Extraído de {filename}: {text_content[:120]}",
                "confirmacion": f"Leí tu documento {filename} y lo anoté en tu viaje ✓"}
    prompt = (
        f"Contenido de un documento de viaje del usuario ({filename}):\n---\n{text_content[:6000]}\n---\n"
        "Extrae TODA la info útil (qué es, nombres, fechas, horas, direcciones, códigos, precios). "
        'Responde SOLO JSON: {"tipo": "<hotel|vuelo|actividad|restaurante|otro>", '
        '"resumen": "<2-4 frases con todos los datos concretos>", '
        '"confirmacion": "<máx 2 frases cálidas confirmando qué entendiste>"}'
    )
    return _parse_json(_call(EXTRACT_PROVIDER, "Eres el parser de documentos de viaje de Voyra.",
                             [{"role": "user", "content": prompt}]))
