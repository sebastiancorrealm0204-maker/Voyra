"""Red de seguridad determinística contra el bug de 'platos inventados'.

Caso real que motivó esto: el Companion dijo que en Crepes & Waffles (cuya
descripción curada dice "crepes dulces y salados, ensaladas y helados") se
puede pedir bandeja paisa. El modelo no inventó el NOMBRE del lugar (eso ya
estaba protegido) — inventó un PLATO que asoció por conocimiento general
("cadena colombiana" → "debe tener comida típica colombiana"), no porque
estuviera en la descripción curada.

La regla en el system prompt (context.py / llm.py) ya le pide al modelo no
hacer esto, pero un prompt nunca es garantía absoluta — sobre todo con modelos
chicos/baratos (DeepSeek) que generalizan más agresivo. Esta capa es la red de
seguridad MECÁNICA: después de que el modelo responde, revisamos si mencionó
alguno de estos platos "típicos" junto al nombre de un lugar curado cuya
descripción NO contiene esa palabra. Si pasa, no bloqueamos la respuesta (eso
rompería la conversación), pero:
  1) lo registramos en logs para que el founder lo vea y pueda corregir el
     prompt o la curación,
  2) opcionalmente, lo marcamos en la respuesta para auditoría desde el frontend
     en modo debug.

No es exhaustivo (no puede serlo: cualquier palabra podría ser "inventada").
Cubre los platos colombianos típicos que un modelo más probablemente rellena
por asociación con "cadena/restaurante colombiano", que es el patrón real que
vimos. Ampliar esta lista es la forma más rápida de capturar más casos.
"""
import logging
import unicodedata

logger = logging.getLogger("voyra.food_guard")

# Platos/preparaciones típicos que un modelo tiende a "regalar" por asociación
# con "cocina colombiana" aunque la descripción curada no los mencione.
PLATOS_VIGILADOS = (
    "bandeja paisa", "ajiaco", "lechona", "tamal", "tamales", "sancocho",
    "arepa de huevo", "patacon", "patacón", "mondongo", "changua",
    "arroz con coco", "cazuela de mariscos", "fritanga", "empanada colombiana",
)


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def verificar_respuesta(reply: str, places: list[dict]) -> list[dict]:
    """Revisa si `reply` menciona un plato vigilado junto al nombre de un lugar
    curado cuya descripción NO contiene ese plato. Devuelve una lista de
    hallazgos (vacía si todo bien) para loguear/auditar — NUNCA bloquea ni
    modifica la respuesta.

    `places`: la lista de lugares curados de la ciudad (con 'name' y 'descripcion').
    """
    if not reply or not places:
        return []
    r_norm = _norm(reply)
    hallazgos = []
    for plato in PLATOS_VIGILADOS:
        p_norm = _norm(plato)
        if p_norm not in r_norm:
            continue
        # El plato aparece en la respuesta. ¿Junto a qué lugar curado, y esa
        # descripción de verdad lo respalda?
        for lugar in places:
            nombre = lugar.get("name", "")
            if not nombre or _norm(nombre) not in r_norm:
                continue
            desc_norm = _norm(lugar.get("descripcion", ""))
            if p_norm not in desc_norm:
                hallazgos.append({
                    "plato_mencionado": plato,
                    "lugar": nombre,
                    "descripcion_curada": lugar.get("descripcion", ""),
                })
    if hallazgos:
        for h in hallazgos:
            logger.warning(
                "POSIBLE PLATO INVENTADO: el chat mencionó '%s' junto a '%s', "
                "pero la descripción curada de ese lugar no lo contiene. "
                "Descripción real: %s",
                h["plato_mencionado"], h["lugar"], h["descripcion_curada"],
            )
    return hallazgos
