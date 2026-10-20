"""Verificación que corrige: ninguna respuesta del chat llega al usuario con
un dato que la red curada no respalda.

1. food_guard detecta (sin LLM) platos atribuidos a un lugar cuya ficha no los
   respalda.
2. Si hay hallazgos, una segunda llamada al LLM reescribe la respuesta: mismo
   tono, quitando o corrigiendo solo lo no respaldado, con la ficha real delante.
3. La reescritura se vuelve a verificar. Si sigue fallando (o el LLM no
   responde), se recortan las frases culpables a mano.

El caso normal (sin hallazgos) no cuesta nada: cero llamadas extra.
"""
import logging

from . import food_guard, llm

log = logging.getLogger("voyra.verificacion")

_SYSTEM_CORRECTOR = (
    "Eres el verificador de Voyra. Corriges respuestas del Companion (un local con criterio que "
    "acompaña a viajeros) para que solo afirmen lo que dice la ficha curada de cada lugar. "
    "Reglas: conserva el tono, el idioma, la longitud y todo lo que ya era correcto; elimina o "
    "corrige SOLO lo señalado; si quitas un plato, puedes sugerir algo que SÍ esté en la ficha; "
    "nunca añadas lugares ni datos que no estén en las fichas. Devuelve únicamente el texto final "
    "de la respuesta, sin comentarios ni explicaciones."
)

_REESCRITURAS = 2

_SIN_RESPUESTA = ("Déjame confirmarlo bien antes de recomendártelo: ¿me cuentas qué se te antoja "
                  "y te digo el lugar que mejor encaja?")


def _prompt(reply: str, hallazgos: list[dict]) -> str:
    problemas = "\n".join(
        f"- Dijiste «{h['plato_mencionado']}» para {h['lugar']}, pero su ficha no lo menciona."
        for h in hallazgos)
    fichas = "\n".join(sorted({f"- {h['lugar']}: {h['ficha']}" for h in hallazgos}))
    return (f"RESPUESTA ORIGINAL:\n{reply}\n\nPROBLEMAS:\n{problemas}\n\n"
            f"FICHAS CURADAS (única fuente de verdad):\n{fichas}\n\n"
            "Escribe la respuesta corregida.")


def verificar_y_corregir(reply: str, places: list[dict]) -> tuple[str, dict]:
    """(respuesta_final, informe). Nunca lanza: ante cualquier error devuelve
    la respuesta original para no tumbar el chat."""
    try:
        hallazgos = food_guard.verificar_respuesta(reply, places)
    except Exception:
        log.exception("verificacion_error_deteccion")
        return reply, {"corregida": False, "metodo": "error"}
    if not hallazgos:
        return reply, {"corregida": False, "metodo": None}

    informe = {"corregida": True, "hallazgos": [
        {"plato": h["plato_mencionado"], "lugar": h["lugar"]} for h in hallazgos]}

    # Hasta dos reescrituras: recortar frases deja a veces una respuesta que ya
    # no se entiende ("Ese sí es el almuerzo…" sin el lugar al que se refería),
    # así que antes de recortar se le da al LLM una segunda oportunidad con lo
    # que su primer intento aún tenía mal.
    pendientes, base = hallazgos, reply
    for intento in range(1, _REESCRITURAS + 1):
        try:
            nuevo = (llm.chat(_SYSTEM_CORRECTOR, [{"role": "user", "content": _prompt(base, pendientes)}]) or "").strip()
        except Exception as e:
            log.warning("verificacion_reescritura_fallo intento=%d error=%s", intento, type(e).__name__)
            break
        if not nuevo or nuevo.startswith("[demo]"):
            break
        restantes = food_guard.verificar_respuesta(nuevo, places)
        if not restantes:
            log.info("verificacion_corregida metodo=reescritura intento=%d hallazgos=%d", intento, len(hallazgos))
            return nuevo, {**informe, "metodo": "reescritura"}
        pendientes, base = restantes, nuevo

    recortada = food_guard.quitar_frases(reply, hallazgos) or _SIN_RESPUESTA
    log.info("verificacion_corregida metodo=recorte hallazgos=%d", len(hallazgos))
    return recortada, {**informe, "metodo": "recorte"}
