"""Detector determinístico de 'platos inventados'.

Caso real que motivó esto: el Companion dijo que en Crepes & Waffles (cuya
descripción curada dice "crepes dulces y salados, ensaladas y helados") se
puede pedir bandeja paisa. El modelo no inventó el NOMBRE del lugar (eso ya
estaba protegido): inventó un PLATO que asoció por conocimiento general
("cadena colombiana" → "debe tener comida típica colombiana").

La regla del system prompt pide no hacerlo, pero un prompt nunca es garantía,
sobre todo con modelos baratos. Este módulo detecta el error sin gastar LLM;
app/verificacion.py usa los hallazgos para CORREGIR la respuesta antes de que
llegue al usuario.

Cómo decide (frase por frase, para no culpar a un lugar por el plato de otro):
- Un plato vigilado aparece en una frase.
- El plato se atribuye a los lugares curados nombrados en esa frase o, si la
  frase no nombra ninguno ("ahí pide el ajiaco"), al último lugar nombrado antes.
- Si ninguno de esos lugares lo respalda en su FICHA (descripción + bloque
  LOCAL + joyas sociales aprobadas, lo mismo que ve el modelo en el prompt),
  es un hallazgo.

No es exhaustivo: cubre los platos colombianos típicos que un modelo rellena
por asociación. Ampliar PLATOS_VIGILADOS es la forma más rápida de cubrir más.
"""
import logging
import re
import unicodedata

logger = logging.getLogger("voyra.food_guard")

# Platos/preparaciones típicos que un modelo tiende a "regalar" por asociación
# con "cocina colombiana" aunque la ficha curada no los mencione.
PLATOS_VIGILADOS = (
    "bandeja paisa", "ajiaco", "lechona", "tamal", "tamales", "sancocho",
    "arepa de huevo", "patacon", "patacón", "mondongo", "changua",
    "arroz con coco", "cazuela de mariscos", "fritanga", "empanada colombiana",
)

_LOCAL_CAMPOS = ("clave", "pedir", "ver", "momento", "ojo", "dato", "practico")


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def _contiene(texto_norm: str, termino_norm: str) -> bool:
    """Coincidencia por palabra completa ('tamal' no matchea dentro de 'tamaleria')."""
    return re.search(r"(?<![a-z])" + re.escape(termino_norm) + r"(?![a-z])", texto_norm) is not None


def ficha(lugar: dict) -> str:
    """Todo lo que el Companion puede afirmar de un lugar: su única fuente de verdad."""
    partes = [lugar.get("descripcion") or ""]
    local = lugar.get("local") or {}
    if isinstance(local, dict):
        partes += [local[k] for k in _LOCAL_CAMPOS if isinstance(local.get(k), str)]
        for clave in ("dato_social", "fresco"):
            partes += [(it.get("texto") or "") for it in (local.get(clave) or []) if isinstance(it, dict)]
    return " ".join(p.strip() for p in partes if p and p.strip())


def _respalda(lugar: dict, plato_norm: str) -> bool:
    texto = _norm(ficha(lugar))
    if _contiene(texto, plato_norm):
        return True
    # singular/plural ("tamales" en la respuesta, "tamal" en la ficha y viceversa)
    if plato_norm.endswith("es") and _contiene(texto, plato_norm[:-2]):
        return True
    return _contiene(texto, plato_norm + "es")


def frases(texto: str) -> list[str]:
    """Parte en frases conservando la puntuación final."""
    return [f for f in re.split(r"(?<=[.!?…])\s+", (texto or "").strip()) if f]


def verificar_respuesta(reply: str, places: list[dict]) -> list[dict]:
    """Hallazgos de platos no respaldados (lista vacía si todo bien). Cada
    hallazgo trae la frase culpable para poder corregirla o recortarla."""
    if not reply or not places:
        return []
    nombrados = [(p, _norm(p.get("name", ""))) for p in places if p.get("name")]
    hallazgos, ultimo = [], []
    for frase in frases(reply):
        f_norm = _norm(frase)
        en_frase = [p for p, n in nombrados if n and _contiene(f_norm, n)]
        if en_frase:
            ultimo = en_frase
        candidatos = en_frase or ultimo
        if not candidatos:
            continue
        for plato in PLATOS_VIGILADOS:
            p_norm = _norm(plato)
            if not _contiene(f_norm, p_norm):
                continue
            if any(_respalda(l, p_norm) for l in candidatos):
                continue
            for l in candidatos:
                h = {"plato_mencionado": plato, "lugar": l["name"], "frase": frase, "ficha": ficha(l)}
                if h not in hallazgos:
                    hallazgos.append(h)
    # "tamal" y "tamales" en la misma frase y lugar son un solo error
    unicos, vistos = [], set()
    for h in hallazgos:
        k = (_norm(h["plato_mencionado"]).rstrip("s").rstrip("e"), h["lugar"], h["frase"])
        if k not in vistos:
            vistos.add(k)
            unicos.append(h)
    for h in unicos:
        logger.warning("PLATO NO RESPALDADO: '%s' junto a '%s'. Frase: %s",
                       h["plato_mencionado"], h["lugar"], h["frase"])
    return unicos


def quitar_frases(reply: str, hallazgos: list[dict]) -> str:
    """Corrección mecánica de último recurso: elimina las frases culpables."""
    malas = {h["frase"] for h in hallazgos}
    return " ".join(f for f in frases(reply) if f not in malas).strip()
