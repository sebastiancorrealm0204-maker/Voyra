"""Memoria del viajero: lo que el Companion recuerda de ti entre viajes.

Un local con criterio no te trata como desconocido cada vez: recuerda que no
comes mariscos, que la última vez en Bogotá fuiste a La Candelaria y que te
gustan los cafés tranquilos. Este módulo arma ese bloque para el system prompt
a partir de datos que el propio usuario ya dio (perfil global + viajes
anteriores). Gratis: cero llamadas LLM, solo lectura de la base.
"""
import logging

from . import auth, db, plans

log = logging.getLogger("voyra.memoria")

MAX_VIAJES = 4
MAX_LUGARES_POR_VIAJE = 6

# Campos del perfil que sirven para recomendar (no se mandan pasaporte ni teléfono).
_PERFIL = (
    ("nombre", "Se llama"),
    ("preferencias", "Preferencias"),
    ("alergias", "Alergias / no puede comer"),
    ("condiciones_medicas", "Condiciones médicas"),
    ("notas", "Notas que dejó"),
)


def _viajes_anteriores(user_id: str, trip_id: str) -> list[str]:
    out = []
    for t in db.list_trips_for_user(user_id):
        if t.get("id") == trip_id:
            continue
        ciudad = (t.get("ciudad") or "").strip()
        if not ciudad:
            continue
        linea = f"{ciudad} ({t.get('inicio', '?')} → {t.get('fin', '?')})"
        gustos = [g for g in (t.get("gustos") or []) if g]
        if gustos:
            linea += f"; gustos de ese viaje: {', '.join(gustos)}"
        lugares = []
        for p in plans.normalizar_lista(t.get("planes", [])):
            nombre = (p.get("lugar") or p.get("titulo") or "").strip()
            if nombre and nombre not in lugares:
                lugares.append(nombre)
        if lugares:
            linea += f"; fue a: {', '.join(lugares[:MAX_LUGARES_POR_VIAJE])}"
        out.append(linea)
        if len(out) >= MAX_VIAJES:
            break
    return out


def bloque(user_id: str | None, trip_id: str) -> str:
    """Bloque MEMORIA DEL VIAJERO para el system prompt. Vacío si no hay nada
    que recordar. Nunca rompe el chat."""
    if not user_id:
        return ""
    try:
        perfil = auth.get_profile(user_id)
        datos = [f"- {etq}: {perfil[k].strip()}" for k, etq in _PERFIL
                 if isinstance(perfil.get(k), str) and perfil.get(k).strip()]
        viajes = _viajes_anteriores(user_id, trip_id)
    except Exception:
        log.warning("memoria_fallo user=%s", user_id, exc_info=True)
        return ""
    if not datos and not viajes:
        return ""
    partes = ["\nMEMORIA DEL VIAJERO (lo que ya sabes de esta persona, de su perfil y de viajes anteriores):"]
    partes += datos
    if viajes:
        partes.append("- Viajes anteriores contigo:")
        partes += [f"  · {v}" for v in viajes]
    partes.append(
        "CÓMO USAR LA MEMORIA: úsala como lo haría un amigo local que ya te conoce — con naturalidad "
        "y solo cuando aporte ('como la última vez te gustó X, aquí tienes algo parecido'). Las "
        "alergias y condiciones médicas son OBLIGATORIAS: nunca recomiendes algo que choque con ellas. "
        "No recites la memoria ni la menciones si no viene al caso."
    )
    return "\n".join(partes) + "\n"
