"""Pipeline de enriquecimiento social: TikTok / Instagram → campo `dato` del lugar.

QUÉ HACE Y POR QUÉ (lee esto antes de tocarlo)
─────────────────────────────────────────────────────────────────────────────
El diferencial de Voyra es saber lo que un local sabe — y mucho de ese saber vive
en TikTok e IG como "joyas perdidas". Pero hay dos verdades que definen el diseño:

  1) Lo VIRAL no es la joya. Para cuando algo tiene 8 TikToks, tiene fila de 2h.
     La joya es EL DETALLE dentro del video ("pídela sin chile", "la segunda
     birrería, no la del letrero"), no el hecho de que sea tendencia.
  2) Las APIs de TikTok/IG son frágiles y restringidas. Scrapear feeds a escala
     se rompe solo. Lo robusto es extraer el detalle de contenido PUNTUAL
     (caption/transcripción de un video concreto, o aporte de un creador) y
     curarlo con humano en el loop.

Por eso este módulo NO scrapea. Recibe TEXTO de una pieza social concreta (su
caption/transcripción) + a qué lugar se refiere, y usa el modelo BARATO de
extracción para destilar UN dato local específico y verificable — o devolver
nada si el contenido es puro hype. El resultado entra como 'pending' a la cola de
revisión; NADA se publica sin que un humano lo apruebe (regla de confianza).

El input puede venir hoy de aportes manuales o de creadores; mañana, de un fetcher
automático que llame a `submit()` con el mismo formato. La parte valiosa —destilar
señal de ruido— ya queda resuelta aquí.
─────────────────────────────────────────────────────────────────────────────
"""
from . import db, llm

_SYSTEM = (
    "Eres el extractor de 'joyas locales' de Voyra. Recibes el texto de una pieza de "
    "redes sociales (TikTok/Instagram) sobre un lugar concreto de viaje, y tu trabajo es "
    "destilar UN (1) dato local específico, accionable y verificable que un viajero "
    "agradecería — el tipo de cosa que solo sabe alguien que estuvo ahí.\n\n"
    "QUÉ SÍ es un buen dato (extráelo):\n"
    "• Qué pedir exactamente ('pide la torta media ahogada, la entera empapa de más').\n"
    "• Un truco de timing o acceso ('llega antes de la 1pm o hay fila de 40 min').\n"
    "• Una trampa o aclaración ('la sede buena es la de la esquina, no la del centro comercial').\n"
    "• Un detalle escondido ('el mejor mirador es la terraza de arriba, casi nadie sube').\n\n"
    "QUÉ NO es un dato (RECHÁZALO devolviendo dato=null):\n"
    "• Hype genérico sin información ('increíble', 'tienes que ir', 'el mejor lugar', 'vibes').\n"
    "• Afirmaciones que el texto NO respalda. NO inventes ni completes con tu conocimiento.\n"
    "• Datos que no son de ESE lugar, o que son tan generales que ya están en cualquier guía.\n\n"
    "REGLAS DE SALIDA:\n"
    "• Escribe el dato en español neutro, en UNA frase corta, en tono de amigo local "
    "(igual que los `dato` curados de Voyra). Sin emojis, sin hashtags, sin '¡increíble!'.\n"
    "• Solo puedes afirmar lo que el texto sostiene. Si dudas, dato=null.\n"
    "• 'tipo': 'durable' si el dato es atemporal (qué pedir, un truco permanente); "
    "'fresco' si es de coyuntura (algo que abrió esta semana, un evento, una tendencia "
    "del momento que caduca).\n"
    "• 'confianza': 0..1 — qué tan específico y respaldado por el texto está el dato.\n\n"
    "Responde SOLO con JSON, sin texto alrededor, sin markdown:\n"
    '{\"dato\": \"...\" | null, \"tipo\": \"durable\"|\"fresco\", \"confianza\": 0.0, \"motivo\": \"breve por qué\"}'
)

# Cuánto vive una joya 'fresco' antes de caducar sola (no se muestra pasado esto).
_FRESCO_TTL_SEG = 14 * 24 * 3600  # 14 días


def _mock_extract(place_name: str, source_text: str) -> dict:
    """Sin keys de LLM (test/demo): heurística mínima — si el texto trae un verbo
    de acción concreto, propone un dato pobre; si es puro hype, lo rechaza."""
    low = source_text.lower()
    hype = ("increíble", "amazing", "lo mejor", "tienes que", "must", "vibes", "hermoso")
    accion = ("pide", "pedir", "llega", "antes de", "evita", "la sede", "pregunta", "ten en cuenta")
    if any(a in low for a in accion) and not all(h in low for h in hype):
        frag = source_text.strip().split(".")[0][:120]
        return {"dato": frag, "tipo": "durable", "confianza": 0.5,
                "motivo": "[mock] heurística: hay un detalle accionable."}
    return {"dato": None, "tipo": "durable", "confianza": 0.0,
            "motivo": "[mock] solo hype/genérico, sin dato concreto."}


def extract_dato(place_name: str, city: str, source_text: str) -> dict:
    """Destila un candidato a `dato` desde el texto social. NO toca la base de datos.

    Devuelve siempre un dict: {dato: str|None, tipo, confianza, motivo}.
    dato=None significa 'no hay joya extraíble' (hype/genérico/no verificable).
    """
    source_text = (source_text or "").strip()
    if not source_text:
        return {"dato": None, "tipo": "durable", "confianza": 0.0, "motivo": "texto vacío"}

    if llm.EXTRACT_PROVIDER == "mock" or not llm._available():
        return _mock_extract(place_name, source_text)

    user = (
        f"LUGAR: {place_name} (en {city}).\n"
        f"TEXTO DE LA PIEZA SOCIAL:\n\"\"\"\n{source_text[:2000]}\n\"\"\"\n\n"
        "Destila el dato local según tus reglas y responde solo el JSON."
    )
    try:
        raw = llm._call(llm.EXTRACT_PROVIDER, _SYSTEM, [{"role": "user", "content": user}], max_tokens=300)
        out = llm._parse_json(raw)
    except Exception as e:  # noqa: BLE001 — ante cualquier fallo, no proponemos nada
        return {"dato": None, "tipo": "durable", "confianza": 0.0, "motivo": f"error de extracción: {e}"}

    dato = out.get("dato")
    if isinstance(dato, str):
        dato = dato.strip()
    if not dato:
        return {"dato": None, "tipo": out.get("tipo", "durable"),
                "confianza": float(out.get("confianza") or 0.0), "motivo": out.get("motivo", "")}
    return {
        "dato": dato,
        "tipo": "fresco" if out.get("tipo") == "fresco" else "durable",
        "confianza": float(out.get("confianza") or 0.0),
        "motivo": out.get("motivo", ""),
    }


def submit(place_name: str, city: str, source_text: str, *,
           source_type: str = "manual", source_url: str | None = None,
           min_confianza: float = 0.45) -> dict:
    """Extrae un dato y, si pasa el umbral, lo encola como 'pending' para revisión.

    No publica nada: solo crea el candidato. Devuelve el resultado de la extracción
    + el id de la mejora encolada (o None si se descartó).
    """
    res = extract_dato(place_name, city, source_text)
    if not res["dato"] or res["confianza"] < min_confianza:
        return {**res, "encolado": False, "enrichment_id": None}

    campo = "fresco" if res["tipo"] == "fresco" else "dato"
    expires = (db.now() + _FRESCO_TTL_SEG) if campo == "fresco" else None
    eid = db.add_enrichment(
        city, place_name, res["dato"], field=campo,
        source_type=source_type, source_url=source_url,
        confidence=res["confianza"], expires_at=expires, status="pending",
    )
    return {**res, "encolado": True, "enrichment_id": eid, "campo": campo}
