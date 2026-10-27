"""Tests de confiabilidad/rendimiento: reintentos, failover, cachés, idempotencia,
atomicidad, scheduler, waitlist y /health. Todo corre sin keys (modo mock) y con
transportes HTTP falsos: no sale nada a internet."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, engine, llm, net, seed_data


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from app import auth, limits, scheduler, waitlist
    db.DB_PATH = str(tmp_path / "rel.db")
    db.init_db()
    auth.init_auth()
    limits.init_limits()
    waitlist.init_waitlist()
    waitlist.reset_rate_limit()
    waitlist.reset_count_cache()
    db.seed_destination_places(seed_data.all_seeds())
    engine.QUIET_START, engine.QUIET_END = 24.0, 0.0
    llm._breaker.reset()
    _mat, _noc = scheduler.MATUTINO, scheduler.NOCTURNO
    yield
    scheduler.MATUTINO, scheduler.NOCTURNO = _mat, _noc
    llm._breaker.reset()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)


@pytest.fixture
def fake_http(monkeypatch):
    """Instala un cliente HTTP compartido con transporte falso. Devuelve una
    función para fijar el handler y la lista de requests vistos."""
    vistos = []
    estado = {"handler": lambda req: httpx.Response(200, json={})}

    def _transport(req):
        vistos.append(req)
        return estado["handler"](req)

    monkeypatch.setattr(net, "_client", httpx.Client(transport=httpx.MockTransport(_transport)))

    def set_handler(h):
        estado["handler"] = h
    yield set_handler, vistos
    net.close()


def _trip(**kw):
    base = {"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-07-12", "fin": "2026-07-18",
            "pais": "Colombia", "gustos": []}
    base.update(kw)
    return db.create_trip(base)


def _auth():
    from app.main import app
    from test_companion import _auth_headers
    client = TestClient(app)
    return client, _auth_headers(client)


# ── DB: seed, orden de filas, atomicidad, caché ──
def test_seed_no_duplica_ciudades_secundarias():
    """Antes cada re-seed duplicaba Guadalajara (solo se borraba la 1ª ciudad)."""
    n_gdl = len(db.places_for_city("Guadalajara"))
    n_bog = len(db.places_for_city("Bogotá"))
    assert n_gdl > 0
    for _ in range(3):
        db.seed_destination_places(seed_data.all_seeds())
    assert len(db.places_for_city("Guadalajara")) == n_gdl
    assert len(db.places_for_city("Bogotá")) == n_bog


def test_rows_devuelve_los_ultimos_en_orden():
    tid = _trip()
    for i in range(215):
        db.insert("messages", {"trip_id": tid, "role": "user", "content": f"m{i}",
                               "created_at": 1000.0 + i})
    rs = db.rows("messages", tid)
    assert len(rs) == 200
    assert rs[-1]["content"] == "m214"          # el más nuevo SÍ está
    assert rs[0]["content"] == "m15"            # y siguen en orden cronológico
    assert [r["created_at"] for r in rs] == sorted(r["created_at"] for r in rs)


def test_update_trip_concurrente_no_pierde_escrituras():
    tid = _trip()
    errores = []

    def _w(i):
        try:
            db.update_trip(tid, {f"k{i}": i})
        except Exception as e:  # pragma: no cover - solo si hay bug
            errores.append(e)

    ths = [threading.Thread(target=_w, args=(i,)) for i in range(20)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errores
    t = db.get_trip(tid)
    assert all(t.get(f"k{i}") == i for i in range(20))


def test_update_trip_inexistente_lanza_keyerror():
    with pytest.raises(KeyError):
        db.update_trip("noexiste", {"a": 1})


def test_cache_places_devuelve_copias_y_se_invalida_con_mejoras():
    a = db.places_for_city("Bogotá")
    a[0]["name"] = "MUTADO"
    assert db.places_for_city("Bogotá")[0]["name"] != "MUTADO"  # copia, no referencia
    nombre = db.places_for_city("Bogotá")[0]["name"]
    db.add_enrichment("Bogotá", nombre, "pide el spritz", status="approved", source_type="tiktok")
    lugar = next(p for p in db.places_for_city("Bogotá") if p["name"] == nombre)
    assert any(x["texto"] == "pide el spritz" for x in (lugar["local"] or {}).get("dato_social", []))


def test_get_trip_with_owner_y_lista_por_usuario():
    tid = db.create_trip({"ciudad": "Bogotá"}, user_id="u1")
    db.create_trip({"ciudad": "Cali"}, user_id="u2")
    trip, owner = db.get_trip_with_owner(tid)
    assert trip["id"] == tid and owner == "u1"
    assert db.get_trip_with_owner("nada") == (None, None)
    assert [t["id"] for t in db.list_trips_for_user("u1")] == [tid]


# ── Motor: fallback determinístico ──
def test_operativo_llega_aunque_el_llm_falle(monkeypatch):
    tid = _trip()

    def _boom(*a, **k):
        raise llm.ProviderUnavailable("caído")
    monkeypatch.setattr(llm, "score_event", _boom)
    r = engine.ingest(tid, "Duffel webhook", "vuelo", True, "Tu vuelo AV245 se retrasó 2h")
    assert r["decision"] == "push" and r["score"] >= 90
    assert r["notification"]["body"].startswith("Tu vuelo AV245")
    assert db.rows("decisions", tid)[-1]["llm_mode"] == "fallback"


def test_no_operativo_con_llm_roto_va_a_feed(monkeypatch):
    tid = _trip()
    monkeypatch.setattr(llm, "score_event", lambda *a, **k: {"score": "no-es-numero"})
    r = engine.ingest(tid, "Scanner", "recomendacion", False, "Feria gastronómica")
    assert r["decision"] == "feed"


# ── Webhook Duffel idempotente ──
def test_webhook_duffel_idempotente_y_404():
    from app.main import app
    client = TestClient(app)
    tid = _trip()
    ev = {"id": "wev_123", "type": "order.airline_initiated_change_detected",
          "data": {"description": "Cambio de horario"}}
    r1 = client.post(f"/webhooks/duffel/{tid}", json=ev).json()
    r2 = client.post(f"/webhooks/duffel/{tid}", json=ev).json()
    assert r1["decision"] == "push"
    assert r2.get("duplicado") is True and r2["notification"] is None
    assert len([n for n in db.rows("notifications", tid) if n["category"] == "vuelo"]) == 1
    # Sin id (payload legado) se procesa siempre
    assert client.post(f"/webhooks/duffel/{tid}", json={"data": {}}).json()["decision"] == "push"
    assert client.post("/webhooks/duffel/noexiste", json=ev).status_code == 404


# ── LLM: reintentos, failover, circuit breaker ──
def test_post_reintenta_5xx_y_luego_ok(fake_http, no_sleep):
    set_handler, vistos = fake_http
    seq = iter([503, 502, 200])
    set_handler(lambda req: httpx.Response(next(seq), json={"choices": [{"message": {"content": "hola"}}]}))
    resp = llm._post_con_reintentos("https://x.test/v1", {}, {})
    assert resp.status_code == 200 and len(vistos) == 3


def test_post_429_persistente_lanza_ratelimited(fake_http, no_sleep):
    set_handler, vistos = fake_http
    set_handler(lambda req: httpx.Response(429, headers={"retry-after": "1"}))
    with pytest.raises(llm.RateLimited):
        llm._post_con_reintentos("https://x.test/v1", {}, {})
    assert len(vistos) == llm._REINTENTOS + 1


def test_post_4xx_no_reintenta(fake_http, no_sleep):
    set_handler, vistos = fake_http
    set_handler(lambda req: httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        llm._post_con_reintentos("https://x.test/v1", {}, {})
    assert len(vistos) == 1


def test_post_timeout_es_provider_unavailable(fake_http, no_sleep):
    set_handler, _ = fake_http

    def _to(req):
        raise httpx.ReadTimeout("lento", request=req)
    set_handler(_to)
    with pytest.raises(llm.ProviderUnavailable):
        llm._post_con_reintentos("https://x.test/v1", {}, {})


def test_failover_ante_caida_y_breaker(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS["groq"], "key", "k1")
    monkeypatch.setitem(llm.PROVIDERS["deepseek"], "key", "k2")
    llamadas = []

    def _one(p, system, messages, max_tokens=800):
        llamadas.append(p)
        if p == "groq":
            raise llm.ProviderUnavailable("caído")
        return f"ok-{p}"
    monkeypatch.setattr(llm, "_call_one", _one)
    for _ in range(llm._breaker.umbral):
        assert llm._call("groq", "s", []) == "ok-deepseek"
    assert llm.breaker_state().get("groq") == "open"
    llamadas.clear()
    assert llm._call("groq", "s", []) == "ok-deepseek"
    assert llamadas == ["deepseek"]  # breaker abierto: ni se intenta groq


def test_respuesta_ilegible_cuenta_como_falla_transitoria(fake_http, no_sleep, monkeypatch):
    set_handler, _ = fake_http
    monkeypatch.setitem(llm.PROVIDERS["groq"], "key", "k1")
    set_handler(lambda req: httpx.Response(200, json={"inesperado": True}))
    with pytest.raises(llm.ProviderUnavailable):
        llm._call_one("groq", "s", [{"role": "user", "content": "hola"}])


# ── Utilidades de red ──
def test_ttlcache_expira_y_lru():
    c = net.TTLCache(ttl_s=0.05, maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)              # expulsa 'a' (LRU)
    assert c.get("a") is None and c.get("c") == 3
    time.sleep(0.06)
    assert c.get("c") is None


def test_search_cachea_consultas_repetidas(fake_http, monkeypatch):
    from app import search
    set_handler, vistos = fake_http
    monkeypatch.setattr(search, "MODE", "real")
    monkeypatch.setattr(search, "cuota_agotada", lambda: False)
    search._cache.clear()
    set_handler(lambda req: httpx.Response(200, json={"results": [
        {"title": "T", "content": "C", "url": "https://u"}]}))
    a = search.search("bogota noticias", max_results=2, topic="news")
    b = search.search("bogota noticias", max_results=2, topic="news")
    assert a == b and len(a) == 1 and len(vistos) == 1
    search._cache.clear()


def test_search_se_apaga_al_llegar_al_tope_mensual(fake_http, monkeypatch):
    from app import search
    set_handler, vistos = fake_http
    monkeypatch.setattr(search, "MODE", "real")
    monkeypatch.setattr(search, "_uso_cache", {"t": 0.0, "bloqueado": False})
    search._cache.clear()

    def h(req):
        if req.url.path.endswith("/usage"):
            return httpx.Response(200, json={"account": {"plan_usage": 920, "plan_limit": 1000}})
        return httpx.Response(200, json={"results": [{"title": "T", "content": "C", "url": "https://u"}]})
    set_handler(h)
    assert search.search("bogota tope", max_results=2) == []
    assert not any(str(r.url).endswith("/search") for r in vistos)  # no gastó cuota


def test_search_sigue_si_falla_la_consulta_de_uso(fake_http, no_sleep, monkeypatch):
    from app import search
    set_handler, _ = fake_http
    monkeypatch.setattr(search, "MODE", "real")
    monkeypatch.setattr(search, "_uso_cache", {"t": 0.0, "bloqueado": False})
    search._cache.clear()

    def h(req):
        if req.url.path.endswith("/usage"):
            return httpx.Response(500)
        return httpx.Response(200, json={"results": [{"title": "T", "content": "C", "url": "https://u"}]})
    set_handler(h)
    assert len(search.search("bogota sigue", max_results=2)) == 1
    search._cache.clear()


def test_search_falla_devuelve_lista_vacia(fake_http, no_sleep, monkeypatch):
    from app import search
    set_handler, _ = fake_http
    monkeypatch.setattr(search, "MODE", "real")
    monkeypatch.setattr(search, "cuota_agotada", lambda: False)
    search._cache.clear()
    set_handler(lambda req: httpx.Response(500))
    assert search.search("x") == []


def test_places_cerca_cachea(fake_http, monkeypatch):
    from app import places_live
    set_handler, vistos = fake_http
    monkeypatch.setattr(places_live, "API_KEY", "k")
    places_live._cache_cerca.clear()
    set_handler(lambda req: httpx.Response(200, json={"places": [
        {"id": "p1", "displayName": {"text": "Farmacia"}, "location": {"latitude": 4.6, "longitude": -74.0}}]}))
    r1 = places_live.buscar_cerca("farmacia", 4.65001, -74.05001)
    r2 = places_live.buscar_cerca("farmacia", 4.65002, -74.05002)  # mismo punto a ~1 m
    assert r1 == r2 and r1[0]["name"] == "Farmacia" and len(vistos) == 1
    places_live._cache_cerca.clear()


# ── Geocoding del hotel (antes nunca corría por un NameError silenciado) ──
def test_geocoding_hotel_se_guarda(monkeypatch):
    from app import main, places_live
    monkeypatch.setattr(places_live, "disponible", lambda: True)
    monkeypatch.setattr(places_live, "geocodificar_hotel",
                        lambda h, c, p="": {"lat": 4.65, "lng": -74.05, "place_id": "x", "dir": "Cra 7"})
    tid = _trip()
    hilos_antes = set(threading.enumerate())
    main._geocodificar_hotel_bg(tid, "Hotel X", "Bogotá", "Colombia")
    for t in set(threading.enumerate()) - hilos_antes:
        t.join(timeout=5)
    t = db.get_trip(tid)
    assert t["lat_hotel"] == 4.65 and t["place_id_hotel"] == "x"


# ── Scheduler: reintento tras falla ──
def test_scheduler_reintenta_si_el_disparo_falla(monkeypatch):
    from datetime import datetime
    from app import scheduler, timeutil, watchers
    hoy = datetime.now(timeutil.tz_for_city("Bogotá")).date().isoformat()
    tid = _trip(inicio=hoy, fin="2099-01-01")
    scheduler.MATUTINO = (0.0, 24.0)
    scheduler.NOCTURNO = (99.0, 99.0)
    real = watchers.check_in_matutino
    estado = {"falla": True}

    def _flaky(t):
        if estado["falla"]:
            raise RuntimeError("LLM caído")
        return real(t)
    monkeypatch.setattr(watchers, "check_in_matutino", _flaky)
    r1 = scheduler.tick()
    assert not any(k == "matutino" for k, _ in r1["disparos"])
    assert not db.proactive_already_sent(tid, "matutino", hoy)   # desmarcado
    estado["falla"] = False
    r2 = scheduler.tick()
    assert ("matutino", tid) in r2["disparos"]
    r3 = scheduler.tick()
    assert ("matutino", tid) not in r3["disparos"]                # y una sola vez


def test_checkin_con_llm_caido_usa_texto_fijo(monkeypatch):
    from app import watchers

    def _boom(*a, **k):
        raise llm.ProviderUnavailable("caído")
    monkeypatch.setattr(llm, "chat", _boom)
    tid = _trip()
    r = watchers.check_in(tid)
    assert "mañana" in r["message"]
    assert db.rows("messages", tid)[-1]["content"] == r["message"]


def test_cleanup_borra_sesiones_vencidas():
    from app import auth
    u = auth.create_user("old@x.com", "supersecret")
    tok = auth.create_session(u["id"])
    with db.conn() as c:
        c.execute("UPDATE sessions SET expires_at=? WHERE token=?", (time.time() - 10, tok))
    assert auth.user_for_session(tok) is None
    auth.cleanup_expired()
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM sessions").fetchone()["n"] == 0


# ── Chat: historial acotado ──
def test_historial_acotado_y_empieza_en_usuario():
    from app import context
    tid = _trip()
    for i in range(80):
        db.insert("messages", {"trip_id": tid, "role": "user" if i % 2 else "companion",
                               "content": f"m{i}", "created_at": 2000.0 + i})
    h = context.historial(tid, max_msgs=10)
    assert len(h) <= 10 and h[0]["role"] == "user" and h[-1]["content"] == "m79"


def test_chat_api_sigue_funcionando_con_fanout():
    client, H = _auth()
    tid = client.post("/trips", json={"ciudad": "Bogotá"}, headers=H).json()["trip_id"]
    r = client.post(f"/trips/{tid}/chat", json={"message": "estoy perdido"}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"reply", "planes_propuestos", "chain_results", "intencion", "restante_chat"}


def test_chat_con_llm_caido_responde_200(monkeypatch):
    client, H = _auth()
    tid = client.post("/trips", json={"ciudad": "Bogotá"}, headers=H).json()["trip_id"]

    def _boom(*a, **k):
        raise llm.ProviderUnavailable("caído")
    monkeypatch.setattr(llm, "chat", _boom)
    r = client.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=H)
    assert r.status_code == 200 and "conexión" in r.json()["reply"]


# ── Waitlist ──
def test_waitlist_alta_idempotente_y_contador():
    from app.main import app
    client = TestClient(app)
    body = {"name": "Ana", "email": "Ana@Example.com ", "phone": "+57 300", "source": "landing-companion"}
    assert client.get("/waitlist/count").json() == {"count": 0}
    r1 = client.post("/waitlist", json=body)
    r2 = client.post("/waitlist", json=body)
    assert r1.status_code == 200 and r1.json()["status"] == "added"
    assert r2.json()["status"] == "already"
    assert client.get("/waitlist/count").json() == {"count": 1}
    assert client.post("/waitlist", json={"email": "no-es-email"}).status_code == 400
    assert client.post("/waitlist", json={"name": "x"}).status_code == 422


def test_waitlist_cors_para_la_landing():
    from app.main import app
    client = TestClient(app)
    r = client.options("/waitlist", headers={
        "Origin": "https://companion-landing.vercel.app",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "https://companion-landing.vercel.app")


def test_waitlist_rate_limit_por_ip(monkeypatch):
    from app import waitlist
    from app.main import app
    monkeypatch.setattr(waitlist, "_RL_MAX", 3)
    client = TestClient(app)
    h = {"X-Forwarded-For": "1.2.3.4"}
    for i in range(3):
        assert client.post("/waitlist", json={"email": f"a{i}@x.com"}, headers=h).status_code == 200
    assert client.post("/waitlist", json={"email": "a9@x.com"}, headers=h).status_code == 429
    # otra IP no se ve afectada
    assert client.post("/waitlist", json={"email": "b@x.com"},
                       headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200


def test_waitlist_admin_protegido(monkeypatch):
    from app.main import app
    client = TestClient(app)
    client.post("/waitlist", json={"email": "lead@x.com", "name": "Lead"})
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get("/admin/waitlist").status_code == 503
    monkeypatch.setenv("ADMIN_TOKEN", "secreto")
    assert client.get("/admin/waitlist", headers={"X-Admin-Token": "mal"}).status_code == 403
    r = client.get("/admin/waitlist", headers={"X-Admin-Token": "secreto"})
    assert r.status_code == 200 and r.json()["count"] == 1
    csv = client.get("/admin/waitlist.csv", params={"token": "secreto"})
    assert csv.status_code == 200 and "lead@x.com" in csv.text
    assert "Lead" in client.get("/admin/waitlist/panel", params={"token": "secreto"}).text


def test_waitlist_concurrente_mismo_email():
    from app import waitlist
    res = []

    def _alta():
        res.append(waitlist.add("same@x.com", name="S")["status"])
    ths = [threading.Thread(target=_alta) for _ in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert res.count("added") == 1 and res.count("already") == 7


# ── /health y middleware ──
def test_health_reporta_db_y_mantiene_claves():
    from app.main import app
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["db"]["ok"] is True
    for k in ("llm_mode", "search_mode", "push_enabled", "places_live", "email_mode", "limits"):
        assert k in j
    assert r.headers.get("x-request-id")


def test_health_503_si_la_db_cae(monkeypatch):
    from app.main import app
    monkeypatch.setattr(db, "ping", lambda: {"ok": False, "engine": "sqlite", "error": "X"})
    r = TestClient(app).get("/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"


def test_gzip_en_respuestas_grandes():
    from app.main import app
    r = TestClient(app).get("/destinations/Bogotá/places", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    assert isinstance(r.json(), list)


def test_timezone_guadalajara():
    from app import timeutil
    assert timeutil.tz_for_city("Guadalajara").key == "America/Mexico_City"
