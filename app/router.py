"""Router de intención del Companion — la primera etapa de cada mensaje de chat.

POR QUÉ EXISTE
--------------
Antes, cada mensaje del usuario disparaba varias llamadas LLM pesadas en paralelo:
  1) chat() con el system prompt completo del Companion (~3-4K tokens de input),
  2) extract_plans_from_chat() (otra llamada con su propio prompt),
  3) a veces detección de cadenas.
Con el free tier de Groq (~12K tokens/min) eso revienta el límite con UN solo
turno y devuelve 429 → el front mostraba "se me cruzaron los cables".

El router invierte el orden: PRIMERO una sola llamada BARATA y PEQUEÑA (sin el
system prompt grande) que clasifica la intención y extrae lo mínimo. Con esa
clasificación, el endpoint decide qué hacer:

  - navegar / buscar_cadena / emergencia / info_simple  → muchas veces NO necesitan
    la llamada de chat cara; se resuelven con plantillas o con una sola llamada.
  - agendar_plan  → SOLO aquí corre la extracción de planes (antes corría siempre).
  - recomendar / charla  → llamada de chat normal, pero ya sabemos que NO hay que
    extraer planes ni buscar cadenas, así que es UNA sola llamada en vez de tres.

Resultado: de 2-3 llamadas LLM por turno bajamos a 1-2, y la llamada del router
es chica (prompt de pocos cientos de tokens), así que el consumo de tokens/min
cae drásticamente y los 429 desaparecen.

DISEÑO
------
- Es determinístico primero (palabras clave) y LLM como respaldo. Si las reglas
  rápidas ya clasifican con confianza, ni siquiera gastamos una llamada.
- Devuelve SIEMPRE una estructura estable, pase lo que pase (nunca rompe el chat).
- La llamada LLM usa EXTRACT_PROVIDER (el barato), no el de chat.
"""
from __future__ import annotations

import json
import unicodedata

from . import llm

# Intenciones que el resto de la app entiende. Una por mensaje (la dominante).
INTENCIONES = {
    "emergencia",      # médica, seguridad, robo de documentos → prioridad vital
    "navegar",         # cómo llego / volver al hotel / al aeropuerto / estoy perdido
    "buscar_cadena",   # supermercado / cajero / farmacia cerca
    "agendar_plan",    # "el viernes tengo tour a las 9" → sí extraer plan
    "recomendar",      # qué hago / dónde como / qué hay cerca
    "charla",          # todo lo demás → conversación normal
}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower().strip()) \
        .encode("ascii", "ignore").decode()


# ── Etapa 1: reglas determinísticas (costo cero) ──
# Si alguna calza con alta confianza, devolvemos sin tocar el LLM.
_EMERGENCIA = (
    "emergencia", "me duele", "dolor de pecho", "no puedo respirar", "sangra",
    "me robaron", "robo", "me asaltaron", "perdi mis documentos", "perdi el pasaporte",
    "perdi mi pasaporte", "me perdieron las maletas", "accidente", "ayuda urgente",
)
_NAVEGAR = (
    "como llego", "como llegar", "como voy", "como vuelvo", "como regreso",
    "volver al hotel", "regresar al hotel", "ir al hotel", "al aeropuerto",
    "estoy perdido", "llevame", "como tomo", "ruta a", "ruta al", "ruta hacia",
)
_BUSCAR_CADENA = (
    "supermercado", "cajero", "farmacia", "droguria", "drogueria", "comprar agua",
    "comprar comida", "donde compro", "gasolina", "carulla", "exito", "jumbo",
)
# Señales de que el usuario CUENTA un plan futuro (no que pregunta por uno).
_AGENDAR = ("tengo reservado", "reserve", "reserve una", "tengo un tour",
            "tengo cena", "tengo una cita", "voy a ir el", "anota", "agenda esto",
            "recuerdame que")


def _regla_rapida(mensaje: str) -> str | None:
    m = _norm(mensaje)
    if any(k in m for k in _EMERGENCIA):
        return "emergencia"
    if any(k in m for k in _NAVEGAR):
        return "navegar"
    # 'buscar_cadena' solo si además hay proximidad/compra (evita falsos positivos
    # tipo "¿qué venden en Carulla?"); reusa la lógica robusta de places_live.
    from . import places_live
    if places_live.detectar_busqueda_cadena(mensaje):
        return "buscar_cadena"
    if any(k in m for k in _AGENDAR):
        return "agendar_plan"
    return None


_ROUTER_SCHEMA = (
    'Clasifica el mensaje del usuario en UNA intención y responde SOLO JSON sin '
    'markdown con esta forma EXACTA:\n'
    '{"intencion": "<emergencia|navegar|buscar_cadena|agendar_plan|recomendar|charla>", '
    '"confianza": <0.0-1.0>, "destino": "<a dónde quiere ir, o null>", '
    '"que_busca": "<qué tipo de lugar/servicio busca, o null>"}\n'
    'Definiciones:\n'
    '- emergencia: salud, seguridad, robo, documentos perdidos. Prioridad vital.\n'
    '- navegar: pide CÓMO llegar/volver a un sitio, o está perdido. NO es agendar.\n'
    '- buscar_cadena: quiere encontrar un servicio/cadena cerca (super, cajero, farmacia).\n'
    '- agendar_plan: CUENTA un plan/reserva futura concreta que ya decidió (con o sin hora). '
    'Pedir indicaciones o preguntar NO es agendar.\n'
    '- recomendar: pide ideas de qué hacer/dónde comer/qué hay cerca.\n'
    '- charla: saludo, agradecimiento, o cualquier otra cosa.\n'
    'Si dudas entre agendar_plan y navegar/recomendar, elige navegar o recomendar: '
    'agendar solo cuando el usuario claramente está contando algo que hará.'
)


def clasificar(mensaje: str, trip: dict | None = None) -> dict:
    """Devuelve {intencion, confianza, destino, que_busca}. Nunca lanza: ante
    cualquier fallo cae a 'charla' para no romper el chat."""
    # Taps de notificación (mensajes entre corchetes) no se enrutan: son acciones
    # de UI con su propia regla en el system prompt del chat.
    if mensaje.strip().startswith("["):
        return {"intencion": "charla", "confianza": 1.0, "destino": None,
                "que_busca": None, "_via": "tap"}

    rapida = _regla_rapida(mensaje)
    if rapida:
        return {"intencion": rapida, "confianza": 0.95, "destino": None,
                "que_busca": None, "_via": "regla"}

    # Respaldo LLM (barato): solo si las reglas no clasificaron.
    if llm.EXTRACT_PROVIDER == "mock" or not llm._available():
        return {"intencion": "charla", "confianza": 0.4, "destino": None,
                "que_busca": None, "_via": "mock"}
    prompt = f'Mensaje del usuario:\n"{mensaje}"\n\n{_ROUTER_SCHEMA}'
    try:
        raw = llm._call(llm.EXTRACT_PROVIDER,
                        "Eres el router de intención de Voyra. Respondes solo JSON.",
                        [{"role": "user", "content": prompt}], max_tokens=150)
        out = json.loads(raw.replace("```json", "").replace("```", "").strip())
        intent = out.get("intencion")
        if intent not in INTENCIONES:
            intent = "charla"
        return {
            "intencion": intent,
            "confianza": float(out.get("confianza", 0.6) or 0.6),
            "destino": out.get("destino") or None,
            "que_busca": out.get("que_busca") or None,
            "_via": "llm",
        }
    except Exception:
        return {"intencion": "charla", "confianza": 0.3, "destino": None,
                "que_busca": None, "_via": "error"}
