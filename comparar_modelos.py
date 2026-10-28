"""Compara modelos de LLM en el flujo REAL de «cómo llego a…» del Companion (router/resolutor + prompt + chat).

Uso (desde la carpeta del backend, con el venv):
    PYTHONPATH=. .venv/Scripts/python.exe comparar_modelos.py                 # candidatos disponibles según las keys
    PYTHONPATH=. .venv/Scripts/python.exe comparar_modelos.py deepseek-chat   # solo ese

Candidatos (cada uno necesita su key en el entorno; DeepSeek se lee del .env):
    deepseek-chat      DEEPSEEK_API_KEY   (el actual)
    gpt-*, o4-*        OPENAI_API_KEY     (OPENAI_MODELS="gpt-5-mini,gpt-5-nano": los que quieras probar)
    claude-haiku-4-5   ANTHROPIC_API_KEY
    groq:<modelo>      GROQ_API_KEY
    ollama:<modelo>    local, gratis (http://localhost:11434)

Usa una base de datos temporal (no toca voyra.db) y una ruta de Google FALSA: solo se evalúa lo que decide el modelo.
Los casos son los mensajes que fallaron (el de «downtown» + variantes del mismo error). Agrega más en CASOS.
"""
import os
import re
import sys
import tempfile
import time

# ── entorno aislado, ANTES de importar la app ──
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="cmp_modelos_"), "cmp.db")
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "1"
for linea in open(".env", encoding="utf-8") if os.path.exists(".env") else []:
    k, _, v = linea.strip().partition("=")
    if k.endswith("_API_KEY") and v and k not in os.environ:
        os.environ[k] = v
for k in ("GOOGLE_MAPS_API_KEY", "TAVILY_API_KEY"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db, limits, llm, main, places_live, transporte, visual  # noqa: E402

auth._SIGNUP_MAX_PER_IP = 10**6
places_live.API_KEY = ""
main._consume_or_429 = lambda *a, **k: None
limits.check_and_consume = lambda *a, **k: None  # sin tope diario de rutas en la prueba
transporte.API_KEY = "x"
transporte.rutas = lambda o, d, s=0: {
    "fuente": "google", "origen": {"nombre": "Zona T"}, "destino": {"nombre": d[2]},
    "opciones": {"taxi": {"min": 40, "min_sin_trafico": 30, "km": 10.7, "precio": {"min": 25000, "max": 38000}}},
    "trancon": {"nivel": "pesado", "ratio": 1.6, "extra_min": 14}, "hora_pico": False,
    "recomendacion": {"modo": "taxi", "motivo": "taxi_mas_rapido"}, "enlaces": {"uber": "u", "maps_transporte": "m", "maps_caminar": "c"},
}

# ── casos: msg, idioma esperado, destino esperado en la tarjeta (None = sin tarjeta), palabras que la respuesta debe tocar,
#    y cuántos OTROS lugares curados se toleran en la respuesta (0 = ninguno: es una pregunta de ruta, no de recomendación) ──
CASOS = [
    {"id": "downtown_en",      "msg": "How do I get downtown?",                      "lang": "en", "dest": "Centro Hist", "toca": ["downtown", "center", "centro", "candelaria", "historic"], "otros": 1},
    {"id": "downtown_mix",     "msg": "quiero ir a downtown",                        "lang": "es", "dest": "Centro Hist", "toca": ["centro", "downtown", "candelaria"], "otros": 1},
    {"id": "centro_es",        "msg": "¿Cómo llego al centro?",                      "lang": "es", "dest": "Centro Hist", "toca": ["centro", "candelaria"], "otros": 1},
    {"id": "downtown_hotel",   "msg": "take me downtown from my hotel",              "lang": "en", "dest": "Centro Hist", "toca": ["downtown", "center", "centro", "candelaria", "historic"], "otros": 1},
    {"id": "zona_t",           "msg": "cómo llego a la zona rosa",                   "lang": "es", "dest": "Zona T", "toca": ["zona t", "zona rosa"], "otros": 0},
    {"id": "museo_oro",        "msg": "How do I get to the Gold Museum?",            "lang": "en", "dest": "Museo del Oro", "toca": ["museo del oro", "gold museum"], "otros": 0},
    {"id": "aeropuerto",       "msg": "cómo llego al aeropuerto",                    "lang": "es", "dest": "El Dorado", "toca": ["aeropuerto", "el dorado"], "otros": 0},
    {"id": "no_curado",        "msg": "quiero ir a la Universidad Javeriana",        "lang": "es", "dest": None, "toca": ["javeriana"], "otros": 1},
    {"id": "no_curado_en",     "msg": "how do I get to the Colpatria tower?",        "lang": "en", "dest": "Colpatria", "toca": ["colpatria"], "otros": 0},
    {"id": "ambiguo",          "msg": "cómo llego a donde venden esmeraldas",        "lang": "es", "dest": None, "toca": [], "otros": 1},
]

_EN = re.compile(r"\b(the|you|your|and|is|to|from|how|get|take|here|can|will|are)\b", re.I)
_ES = re.compile(r"\b(el|la|los|las|tu|te|de|que|para|con|una|es|por|puedes|desde|puedo|estás|voy)\b", re.I)


def _idioma(txt: str) -> str:
    return "en" if len(_EN.findall(txt)) > len(_ES.findall(txt)) else "es"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def _registrar(nombre: str):
    """Deja a `cand` como ÚNICO proveedor (sin failover que esconda los fallos)."""
    if nombre.startswith("ollama:"):
        cfg = {"key": "ollama", "model": nombre[7:], "url": "http://localhost:11434/v1/chat/completions", "style": "openai"}
    elif nombre.startswith("groq:"):
        cfg = {"key": os.environ.get("GROQ_API_KEY", ""), "model": nombre[5:], "url": "https://api.groq.com/openai/v1/chat/completions", "style": "openai"}
    elif nombre.startswith("claude"):
        cfg = {"key": os.environ.get("ANTHROPIC_API_KEY", ""), "model": nombre, "url": "https://api.anthropic.com/v1/messages", "style": "anthropic"}
    elif nombre.startswith("deepseek"):
        cfg = {"key": os.environ.get("DEEPSEEK_API_KEY", ""), "model": nombre, "url": "https://api.deepseek.com/chat/completions", "style": "openai"}
    else:  # gpt-*, o*, etc. → OpenAI
        cfg = {"key": os.environ.get("OPENAI_API_KEY", ""), "model": nombre, "url": "https://api.openai.com/v1/chat/completions", "style": "openai"}
    if not cfg["key"]:
        return False
    llm.PROVIDERS.clear()
    llm.PROVIDERS["cand"] = cfg
    llm.CHAT_PROVIDER = llm.EXTRACT_PROVIDER = llm.SCORING_PROVIDER = "cand"
    return True


def _correr(nombre: str, c: TestClient, H: dict, lugares: list[dict]) -> list[dict]:
    res = []
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "Hotel Test", "zona_actual": "Zona G"}, headers=H).json()["trip_id"]
    for caso in CASOS:
        with db.conn() as cx:  # cada caso arranca sin historial (un viaje, para no chocar con el tope de creación de viajes)
            cx.execute("DELETE FROM messages WHERE trip_id=?", (tid,))
        t0 = time.perf_counter()
        try:
            r = c.post(f"/trips/{tid}/chat", json={"message": caso["msg"]}, headers={**H, "X-UI-Lang": caso["lang"]})
            j = r.json()
            reply, ruta = j.get("reply", ""), j.get("ruta")
        except Exception as e:  # noqa: BLE001
            reply, ruta = f"[ERROR {type(e).__name__}]", None
        ms = int((time.perf_counter() - t0) * 1000)
        fallos = []
        dest = (ruta or {}).get("destino", {}).get("nombre")
        if caso["dest"] is None and ruta:
            fallos.append(f"tarjeta inesperada a {dest}")
        if caso["dest"] and not (dest and caso["dest"].lower() in dest.lower()):
            fallos.append(f"tarjeta a {dest!r}, esperaba {caso['dest']!r}")
        n = _norm(reply)
        if caso["toca"] and not any(k in n for k in caso["toca"]):
            fallos.append("la respuesta no habla del destino pedido")
        citados = [p["name"] for p in visual.mencionados(reply, lugares, maximo=6)]
        esperados = [k for k in caso["toca"] if any(k in _norm(p) for p in citados)]
        otros = [p for p in citados if not any(k in _norm(p) for k in caso["toca"])]
        if len(otros) > caso["otros"]:
            fallos.append(f"nombra otros lugares: {otros[:3]}")
        if _idioma(reply) != caso["lang"]:
            fallos.append(f"idioma {_idioma(reply)}")
        if len(reply.split()) > 130:
            fallos.append(f"larga ({len(reply.split())} palabras)")
        res.append({"id": caso["id"], "ok": not fallos, "fallos": fallos, "ms": ms, "reply": reply})
    return res


def main_():
    from tests.test_companion import _auth_headers
    c = TestClient(main.app)
    H = _auth_headers(c)
    lugares = db.places_for_city("Bogotá")
    pedidos = sys.argv[1:] or (["deepseek-chat"] + [m for m in os.environ.get("OPENAI_MODELS", "").split(",") if m]
                               + (["claude-haiku-4-5"] if os.environ.get("ANTHROPIC_API_KEY") else [])
                               + [m for m in os.environ.get("EXTRA_MODELS", "").split(",") if m])
    resumen = []
    for nombre in pedidos:
        if not _registrar(nombre):
            print(f"· {nombre}: sin API key en el entorno, se omite")
            continue
        res = _correr(nombre, c, H, lugares)
        ok = sum(r["ok"] for r in res)
        ms = sorted(r["ms"] for r in res)
        resumen.append((nombre, ok, len(res), ms[len(ms) // 2]))
        print(f"\n=== {nombre}: {ok}/{len(res)} ok · mediana {ms[len(ms) // 2]} ms")
        for r in res:
            print(f"  {'OK ' if r['ok'] else 'FAIL'} {r['id']:<15} {r['ms']:>5} ms  {'; '.join(r['fallos'])}")
            print(f"       → {r['reply'][:230].replace(chr(10), ' ')}")
    print("\nRESUMEN")
    for n, ok, tot, mm in resumen:
        print(f"  {n:<28} {ok}/{tot}  mediana {mm} ms")


if __name__ == "__main__":
    main_()
