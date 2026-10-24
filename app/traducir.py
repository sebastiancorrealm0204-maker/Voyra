"""Traducción de contenido dinámico (curado en español o de prensa) al idioma del usuario.

La interfaz estática ya está en 6 idiomas en el frontend; esto cubre lo que llega del backend en español:
titulares de alertas, ficha del aeropuerto, razones de recomendaciones, etc.

- es → no hace nada. Sin LLM (mock) → devuelve el original (nunca rompe).
- Caché en BD por (idioma, texto): cada frase se traduce UNA vez para todos los usuarios, así el costo no crece
  con los viajeros. Más una caché en memoria para no ir a la BD en cada request.
- Los nombres propios locales (lugares, platos, barrios, líneas, marcas) se conservan tal cual.
"""
import contextvars
import hashlib
import logging

from . import city_knowledge, db, llm

log = logging.getLogger("voyra.traducir")

IDIOMAS = {"en", "pt", "fr", "de", "it"}
TODOS = IDIOMAS | {"es"}
# Idioma de la interfaz del request en curso (lo fija el middleware desde la cabecera X-UI-Lang).
_ui_lang: contextvars.ContextVar[str] = contextvars.ContextVar("ui_lang", default="")


def fijar_ui_lang(valor: str | None) -> None:
    v = (valor or "").strip().lower()[:2]
    _ui_lang.set(v if v in TODOS else "")


def ui_lang() -> str:
    """Idioma de la interfaz del request actual ('' si no vino)."""
    return _ui_lang.get()
_mem: dict[str, str] = {}
MAX_LOTE = 40
MAX_CHARS = 1200


def _clave(lang: str, texto: str) -> str:
    return hashlib.sha1(f"{lang}|{texto}".encode("utf-8")).hexdigest()


def _de_bd(claves: list[str]) -> dict[str, str]:
    if not claves:
        return {}
    out: dict[str, str] = {}
    try:
        with db.conn() as c:
            for i in range(0, len(claves), 200):
                lote = claves[i:i + 200]
                rs = c.execute(
                    f"SELECT id, texto FROM traducciones WHERE id IN ({','.join('?' * len(lote))})", tuple(lote)
                ).fetchall()
                out.update({r["id"]: r["texto"] for r in rs})
    except Exception as e:
        log.warning("traducciones_leer_fallo error=%s", type(e).__name__)
    return out


def _a_bd(pares: dict[str, str]) -> None:
    try:
        with db.conn() as c:
            for k, v in pares.items():
                c.execute("INSERT INTO traducciones (id, texto, created_at) VALUES (?,?,?) ON CONFLICT (id) DO NOTHING",
                          (k, v, db.now()))
    except Exception as e:
        log.warning("traducciones_guardar_fallo error=%s", type(e).__name__)


def _llamar_llm(textos: list[str], lang: str) -> list[str] | None:
    """Traduce un lote con el LLM barato. None si no hay LLM o falla (el llamador usa el original)."""
    if llm.SCORING_PROVIDER == "mock" or not llm._available():
        return None
    nombre = city_knowledge.lang_name(lang)
    system = (
        f"Eres un traductor profesional para una app de viajes. Traduce al {nombre} cada texto del usuario. "
        "Conserva TAL CUAL los nombres propios locales (lugares, barrios, platos, calles, líneas y estaciones de "
        "transporte, marcas, apps), los números, precios, horas, emojis y URLs. Tono natural y claro, mismo nivel de "
        "formalidad. Si un texto ya está en ese idioma, déjalo igual. No agregues explicaciones. "
        'Responde SOLO un JSON válido: {"1": "...", "2": "..."} con las mismas claves.'
    )
    cuerpo = "\n".join(f'{i}: {t}' for i, t in enumerate(textos, 1))
    try:
        raw = llm._call(llm.SCORING_PROVIDER, system, [{"role": "user", "content": cuerpo}], max_tokens=3500)
        data = llm._parse_json(raw)
        out = []
        for i, t in enumerate(textos, 1):
            v = data.get(str(i))
            out.append(v.strip() if isinstance(v, str) and v.strip() else t)
        return out
    except Exception as e:
        log.warning("traducir_llm_fallo lang=%s error=%s", lang, type(e).__name__)
        return None


def traducir(textos: list[str], lang: str) -> list[str]:
    """Traduce una lista de textos (mismo orden). Nunca lanza: ante cualquier falla devuelve el original."""
    lang = (lang or "es").lower()
    if lang not in IDIOMAS or not textos:
        return list(textos)
    textos = [(t or "")[:MAX_CHARS] for t in textos]
    claves = [_clave(lang, t) for t in textos]
    res: dict[str, str] = {k: _mem[k] for k in claves if k in _mem}
    faltan = [k for k in set(claves) if k not in res and textos[claves.index(k)].strip()]
    if faltan:
        res.update(_de_bd(faltan))
        faltan = [k for k in faltan if k not in res]
    if faltan:
        orig = [textos[claves.index(k)] for k in faltan]
        for i in range(0, len(orig), MAX_LOTE):
            lote_k, lote_t = faltan[i:i + MAX_LOTE], orig[i:i + MAX_LOTE]
            tr = _llamar_llm(lote_t, lang)
            if tr is None:
                continue
            nuevos = dict(zip(lote_k, tr))
            res.update(nuevos)
            _a_bd(nuevos)
    for k, v in res.items():
        _mem[k] = v
    return [res.get(k, t) for k, t in zip(claves, textos)]


# Claves de un dict que NO son texto para el usuario (ids, urls, códigos, coordenadas…).
_NO_TRADUCIR = {"id", "url", "href", "link", "maps_link", "icono", "icon", "code", "codigo", "tipo", "kind",
                "numero", "num", "telefono", "phone", "city", "ciudad", "name", "nombre", "official_site",
                "live_times_url", "fuente", "source", "fecha", "modo", "motivo", "nivel"}


def profundo(obj, lang: str, omitir: set[str] | None = None):
    """Devuelve una copia de `obj` (dict/list anidado) con todas las cadenas traducibles traducidas en UN solo lote."""
    lang = (lang or "es").lower()
    if lang not in IDIOMAS:
        return obj
    omitir = _NO_TRADUCIR | (omitir or set())
    hallados: list[str] = []

    def recolectar(o, k=None):
        if isinstance(o, str):
            if k not in omitir and len(o.strip()) > 2 and not o.startswith(("http", "/")):
                hallados.append(o)
        elif isinstance(o, dict):
            for kk, v in o.items():
                recolectar(v, kk)
        elif isinstance(o, list):
            for v in o:
                recolectar(v, k)

    recolectar(obj)
    if not hallados:
        return obj
    unicos = list(dict.fromkeys(hallados))
    mapa = dict(zip(unicos, traducir(unicos, lang)))

    def aplicar(o, k=None):
        if isinstance(o, str):
            return mapa.get(o, o) if k not in omitir else o
        if isinstance(o, dict):
            return {kk: aplicar(v, kk) for kk, v in o.items()}
        if isinstance(o, list):
            return [aplicar(v, k) for v in o]
        return o

    return aplicar(obj)
