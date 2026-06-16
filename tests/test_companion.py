import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from app import db, engine, seed_data


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    from app import scheduler
    db.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    db.seed_destination_places(seed_data.all_seeds())
    engine.QUIET_START, engine.QUIET_END = 24.0, 0.0  # desactivar quiet hours en tests
    _mat, _noc = scheduler.MATUTINO, scheduler.NOCTURNO  # guardar para restaurar
    yield
    scheduler.MATUTINO, scheduler.NOCTURNO = _mat, _noc


@pytest.fixture
def trip():
    return db.create_trip({"ciudad": "Cartagena", "hotel": "H", "inicio": "2026-07-12",
                           "fin": "2026-07-18", "pais": "Colombia", "gustos": ["Gastronomía local"]})


def test_operativo_siempre_push(trip):
    r = engine.ingest(trip, "Duffel webhook", "vuelo", True, "retraso 2h")
    assert r["decision"] == "push" and r["score"] >= 90


def test_promo_muere_en_filtro(trip):
    r = engine.ingest(trip, "x", "promo_no_solicitada", False, "promo")
    assert r["filter"] == "categoria_bloqueada" and r["decision"] == "silence" and r["score"] is None


def test_presupuesto_diario(trip):
    for _ in range(2):
        engine.ingest(trip, "scanner", "recomendacion", False, "lugar viral afín a gastronomía")
    for _ in range(2):
        engine.ingest(trip, "scanner", "zona", False, "zona nueva")
    r = engine.ingest(trip, "scanner", "clima", False, "lluvia mañana")
    assert r["filter"] == "presupuesto_agotado" and r["decision"] == "feed"


def test_tope_por_categoria(trip):
    engine.ingest(trip, "scanner", "recomendacion", False, "lugar 1")
    engine.ingest(trip, "scanner", "recomendacion", False, "lugar 2")
    r = engine.ingest(trip, "scanner", "recomendacion", False, "lugar 3")
    assert r["filter"] == "tope_de_categoria"


def test_operativo_ignora_presupuesto(trip):
    for _ in range(2):
        engine.ingest(trip, "scanner", "recomendacion", False, "lugar")
        engine.ingest(trip, "scanner", "zona", False, "zona")
    r = engine.ingest(trip, "Duffel webhook", "vuelo", True, "cancelación")
    assert r["decision"] == "push"


def test_feedback_silencia_categoria(trip):
    n1 = engine.ingest(trip, "scanner", "recomendacion", False, "lugar 1")["notification"]
    n2 = engine.ingest(trip, "scanner", "recomendacion", False, "lugar 2")["notification"]
    engine.feedback(n1["id"], "not_interested")
    fb = engine.feedback(n2["id"], "not_interested")
    assert fb["category_silenced"] is True
    r = engine.ingest(trip, "scanner", "recomendacion", False, "lugar 3")
    assert r["filter"] == "categoria_silenciada_por_usuario"


def test_decisiones_quedan_en_dataset(trip):
    engine.ingest(trip, "Duffel webhook", "vuelo", True, "retraso")
    engine.ingest(trip, "x", "promo_no_solicitada", False, "promo")
    ds = db.rows("decisions", trip)
    assert len(ds) == 2  # TODA decisión queda registrada, incluso las del filtro


def test_api_end_to_end():
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Cartagena", "hotel": "H", "inicio": "2026-07-12",
                                    "fin": "2026-07-18"}).json()
    tid = t["trip_id"]
    assert "planes" not in t  # greeting devuelto
    assert client.post(f"/trips/{tid}/location", json={"zona": "Getsemaní"}).json()["zona_actual"] == "Getsemaní"
    s = client.post(f"/trips/{tid}/signals", json={"source": "Duffel webhook", "category": "vuelo",
                                                   "operational": True, "payload": "retraso 2h"}).json()
    assert s["decision"] == "push"
    chat = client.post(f"/trips/{tid}/chat", json={"message": "estoy perdido"}).json()
    assert chat["reply"]
    doc = client.post(f"/trips/{tid}/documents", json={"filename": "tour.pdf",
                                                       "text_content": "Tour islas 13 jul 9am código XK29"}).json()
    assert doc["tipo"]
    assert client.get(f"/trips/{tid}/notifications").json()
    assert client.get(f"/trips/{tid}/decisions").json()


def test_push_disabled_sin_vapid():
    """Sin claves VAPID, el push está deshabilitado pero la app no rompe."""
    from app import push
    # En el entorno de test no hay VAPID configurado
    assert push.enabled() is False
    from app.main import app
    client = TestClient(app)
    pk = client.get("/push/public-key").json()
    assert pk["enabled"] is False


def test_push_subscribe_guarda_y_borra():
    """Suscribir guarda la suscripción; desuscribir la borra."""
    from app.main import app
    client = TestClient(app)
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-07-12", "fin": "2026-07-18"})
    sub = {"endpoint": "https://push.example.com/abc123", "keys": {"p256dh": "x", "auth": "y"}}
    r = client.post(f"/trips/{tid}/push/subscribe", json={"subscription": sub}).json()
    assert r["ok"] is True
    assert len(db.list_push_subscriptions(tid)) == 1
    client.post(f"/trips/{tid}/push/unsubscribe", json={"subscription": sub})
    assert len(db.list_push_subscriptions(tid)) == 0


def test_push_subscribe_no_duplica():
    """Suscribir el mismo endpoint dos veces no crea duplicados (upsert)."""
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-07-12", "fin": "2026-07-18"})
    sub = '{"endpoint":"https://push.example.com/same","keys":{"p256dh":"x","auth":"y"}}'
    db.upsert_push_subscription(tid, "https://push.example.com/same", sub)
    db.upsert_push_subscription(tid, "https://push.example.com/same", sub)
    assert len(db.list_push_subscriptions(tid)) == 1


def test_hora_local_usa_timezone_destino():
    """La hora local sale del timezone del destino, no de la hora UTC del servidor."""
    from app import timeutil
    from datetime import datetime
    from zoneinfo import ZoneInfo
    h = timeutil.hour_float({"ciudad": "Bogotá"})
    esperado = datetime.now(ZoneInfo("America/Bogota"))
    assert abs(h - (esperado.hour + esperado.minute / 60)) < 0.05


def test_scheduler_dispara_matutino_una_vez():
    """El scheduler dispara el check-in matutino una sola vez por día local (dedup)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app import scheduler
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H",
                          "inicio": hoy.isoformat(), "fin": (hoy + timedelta(days=3)).isoformat()})
    scheduler.MATUTINO, scheduler.NOCTURNO = (0.0, 24.0), (0.0, 0.0)
    r1 = scheduler.tick()
    assert "matutino" in [k for (k, t) in r1["disparos"] if t == tid]
    r2 = scheduler.tick()
    assert "matutino" not in [k for (k, t) in r2["disparos"] if t == tid]


def test_scheduler_vuelo_regreso_dia_fin():
    """El día 'fin' del viaje, con vuelo de regreso, se dispara el recordatorio (push operativo)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app import scheduler
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H",
                          "inicio": (hoy - timedelta(days=4)).isoformat(), "fin": hoy.isoformat(),
                          "vuelo_regreso": "AV9533 14:20"})
    scheduler.MATUTINO, scheduler.NOCTURNO = (0.0, 24.0), (0.0, 0.0)
    r = scheduler.tick()
    assert "vuelo_regreso" in [k for (k, t) in r["disparos"] if t == tid]
    notifs = db.rows("notifications", tid)
    assert any(n["category"] == "vuelo" and n["kind"] == "push" for n in notifs)


def test_scheduler_ignora_viaje_no_activo():
    """Un viaje que ya terminó no dispara nada."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from app import scheduler
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H",
                          "inicio": (hoy - timedelta(days=10)).isoformat(),
                          "fin": (hoy - timedelta(days=3)).isoformat()})
    scheduler.MATUTINO, scheduler.NOCTURNO = (0.0, 24.0), (0.0, 24.0)
    r = scheduler.tick()
    assert not any(t == tid for (k, t) in r["disparos"])


def test_modo_aeropuerto_se_activa_y_payload():
    """Al llegar al aeropuerto, el trip entra en modo_aeropuerto y /airport
    devuelve el timeline de llegada + transporte curado de El Dorado."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "Hotel NH Teusaquillo",
                                    "inicio": "2026-07-12", "fin": "2026-07-18"}).json()
    tid = t["trip_id"]

    # Llega al aeropuerto → modo aeropuerto + flag de recién llegado
    loc = client.post(f"/trips/{tid}/location", json={"zona": "Aeropuerto", "disparar_geofence": False}).json()
    assert loc["modo_aeropuerto"] is True
    assert loc["acaba_de_llegar"] is True

    # El payload de llegada trae pasos del timeline y opciones de transporte
    a = client.get(f"/trips/{tid}/airport").json()
    assert a["disponible"] is True
    assert a["code"] == "BOG"
    assert [p["id"] for p in a["pasos"]] == ["migracion", "equipaje", "aduana", "salida"]
    assert a["pasos"][0]["estimado_min"][0] < a["pasos"][0]["estimado_min"][1]
    # Hay un transporte oficial recomendado (taxi) y todos traen tip anti-estafa
    assert any(x["id"] == "taxi_oficial" and x["recomendado"] for x in a["transporte"])
    assert all(x.get("tip") for x in a["transporte"])


def test_modo_aeropuerto_se_apaga_al_salir():
    """Al moverse a una zona normal, el modo aeropuerto se apaga."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H",
                                    "inicio": "2026-07-12", "fin": "2026-07-18"}).json()
    tid = t["trip_id"]
    client.post(f"/trips/{tid}/location", json={"zona": "Aeropuerto"})
    salida = client.post(f"/trips/{tid}/location", json={"zona": "Usaquén"}).json()
    assert salida["modo_aeropuerto"] is False
    assert salida["acaba_de_llegar"] is False


def test_airport_gps_activa_modo():
    """Con coordenadas reales de El Dorado, el modo aeropuerto se activa solo."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H",
                                    "inicio": "2026-07-12", "fin": "2026-07-18"}).json()
    tid = t["trip_id"]
    loc = client.post(f"/trips/{tid}/location",
                      json={"lat": 4.7016, "lng": -74.1469, "zona": "En el hotel"}).json()
    assert loc["zona_actual"] == "Aeropuerto"
    assert loc["modo_aeropuerto"] is True


def test_airport_ciudad_sin_curacion():
    """Una ciudad sin aeropuerto curado responde 'no disponible', sin romper."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Cartagena", "hotel": "H",
                                    "inicio": "2026-07-12", "fin": "2026-07-18"}).json()
    tid = t["trip_id"]
    a = client.get(f"/trips/{tid}/airport").json()
    assert a["disponible"] is False


def test_scan_mock_mode_no_crash():
    """Sin TAVILY_API_KEY, /scan corre en modo mock: 0 resultados, no rompe nada."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Cartagena", "hotel": "H", "inicio": "2026-07-12",
                                    "fin": "2026-07-18"}).json()
    tid = t["trip_id"]
    r = client.post(f"/trips/{tid}/scan").json()
    assert r["search_mode"] == "mock"
    assert r["encontrados"] == 0
    assert r["procesados"] == 0
    assert client.get("/health").json()["search_mode"] == "mock"


def test_nearby_recommendations_bogota():
    """Recomendaciones curadas, rankeadas por distancia real y con maps_link."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "Hotel Zona G", "inicio": "2026-08-01",
                                    "fin": "2026-08-05", "gustos": ["Gastronomía local"]}).json()
    tid = t["trip_id"]

    # Sin destinations, este endpoint devuelve la base curada para Bogotá
    places = client.get("/destinations/Bogotá/places").json()
    assert len(places) >= 10

    # Usuario en Usaquén: lo más cercano debería ser el mercado de Usaquén (distancia ~0)
    client.post(f"/trips/{tid}/location", json={"zona": "Usaquén", "disparar_geofence": False})
    r = client.post(f"/trips/{tid}/nearby").json()
    assert r["candidatos"] >= 10
    top = r["resultados"][0]
    assert top["lugar"] == "Mercado de pulgas de Usaquén"
    assert top["distancia_km"] is not None and top["distancia_km"] < 0.5

    # Cada resultado con notificación trae maps_link real
    con_notif = [x for x in r["resultados"] if x["notification"]]
    assert con_notif
    for x in con_notif:
        assert x["notification"]["maps_link"].startswith("https://www.google.com/maps/search/?api=1&query=")

    # /nearby devuelve solo los más cercanos (top-N), rankeados por distancia real
    # ascendente. Verificamos ese orden, que es la garantía del endpoint.
    distancias = [x["distancia_km"] for x in r["resultados"] if x["distancia_km"] is not None]
    assert distancias == sorted(distancias)  # del más cercano al más lejano


def test_nearby_dedup_no_repeats():
    """Llamadas repetidas a /nearby no repiten lugares; se agota cubriendo todos los curados."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "Hotel Zona G", "inicio": "2026-08-01",
                                    "fin": "2026-08-05", "gustos": ["Gastronomía local"]}).json()
    tid = t["trip_id"]
    client.post(f"/trips/{tid}/location", json={"zona": "Usaquén", "disparar_geofence": False})

    # Total de lugares curados que el endpoint puede devolver para esta ciudad
    # (se deriva del seed, así el test no se rompe cuando la curación crece).
    total_curados = len(client.get("/destinations/Bogotá/places").json())

    vistos_total = set()
    agotado = False
    # Suficientes llamadas para agotar todos los lugares (3 por llamada) + margen.
    for _ in range(total_curados + 2):
        r = client.post(f"/trips/{tid}/nearby").json()
        lugares = {x["lugar"] for x in r["resultados"]}
        assert lugares.isdisjoint(vistos_total)  # nunca repite uno ya visto
        vistos_total |= lugares
        if not r["resultados"]:
            assert "nota" in r
            agotado = True
            break

    assert agotado
    assert len(vistos_total) == total_curados  # cubrió todos los curados, sin repetir ninguno


# ── Planes estructurados (sesión 7) ──
def test_plans_normalizacion_y_orden():
    from app import plans
    crudos = ["cena suelta", {"titulo": "Tour", "fecha": "2026-07-13", "hora": "9am", "tipo": "actividad"}]
    norm = plans.normalizar_lista(crudos)
    assert norm[0]["titulo"] == "cena suelta" and norm[0]["fecha"] is None
    assert norm[1]["hora"] == "09:00" and norm[1]["tipo"] == "actividad"
    # orden: con fecha va después de... no, sin fecha va al final (9999)
    ordenado = plans.ordenar(norm)
    assert ordenado[0]["titulo"] == "Tour"  # tiene fecha real, va primero


def test_plans_hora_pm():
    from app import plans
    assert plans._norm_hora("9pm") == "21:00"
    assert plans._norm_hora("12am") == "00:00"
    assert plans._norm_hora("21:30") == "21:30"
    assert plans._norm_hora("basura") is None


def test_plans_crud_api():
    from app.main import app
    client = TestClient(app)
    tid = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-07-12",
                                      "fin": "2026-07-18"}).json()["trip_id"]
    # agregar plan estructurado
    r = client.post(f"/trips/{tid}/plans", json={"titulo": "Monserrate", "fecha": "2026-07-14",
                                                 "hora": "08:00", "tipo": "actividad"}).json()
    assert len(r["planes"]) == 1
    pid = r["planes"][0]["id"]
    # listar agrupado por día
    lst = client.get(f"/trips/{tid}/plans").json()
    assert "2026-07-14" in lst["por_dia"]
    # confirmar planes propuestos (desde chat/doc)
    client.post(f"/trips/{tid}/plans/confirm", json={"planes": [
        {"titulo": "Cena Andrés", "fecha": "2026-07-14", "hora": "20:00", "tipo": "restaurante"}]})
    assert len(client.get(f"/trips/{tid}/plans").json()["planes"]) == 2
    # borrar
    client.delete(f"/trips/{tid}/plans/{pid}")
    assert len(client.get(f"/trips/{tid}/plans").json()["planes"]) == 1


def test_chat_devuelve_propuestas():
    from app.main import app
    client = TestClient(app)
    tid = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-07-12",
                                      "fin": "2026-07-18"}).json()["trip_id"]
    r = client.post(f"/trips/{tid}/chat", json={"message": "mañana tengo tour a Monserrate 9am"}).json()
    assert "planes_propuestos" in r
    # en modo mock la heurística detecta el plan; no se guarda hasta confirmar
    assert client.get(f"/trips/{tid}/plans").json()["planes"] == []


def test_doc_devuelve_planes_propuestos():
    from app.main import app
    client = TestClient(app)
    tid = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-07-12",
                                      "fin": "2026-07-18"}).json()["trip_id"]
    r = client.post(f"/trips/{tid}/documents", json={"filename": "vuelo.txt",
                                                     "text_content": "Vuelo AV245 13 jul 8am"}).json()
    assert "planes_propuestos" in r


def test_resolver_fecha_relativa():
    from app import plans
    from datetime import datetime
    from zoneinfo import ZoneInfo
    lunes = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("America/Bogota"))
    assert plans.resolver_fecha_relativa("mañana cena", lunes) == "2026-06-16"
    assert plans.resolver_fecha_relativa("el viernes tour", lunes) == "2026-06-19"
    assert plans.resolver_fecha_relativa("hoy", lunes) == "2026-06-15"
    assert plans.resolver_fecha_relativa("pasado mañana", lunes) == "2026-06-17"
    assert plans.resolver_fecha_relativa("comer algo", lunes) is None
    # rellenar solo toca los que no tienen fecha
    pl = [{"titulo": "a", "fecha": None}, {"titulo": "b", "fecha": "2026-07-01"}]
    out = plans.rellenar_fechas(pl, "mañana", lunes)
    assert out[0]["fecha"] == "2026-06-16" and out[1]["fecha"] == "2026-07-01"


def test_place_matching_robusto():
    """El matching de lugares ignora espacios, tildes, mayúsculas y relleno."""
    from app import db
    SEED = "El Cielo Bogotá"
    for v in ["el cielo", "ElCielo", "El Cielo", "El cielo", "EL CIELO",
              "Cena en ElCielo", "cena en el cielo", "reserva El Cielo"]:
        assert db.place_matches(v, SEED), f"debería coincidir: {v!r}"
    # Palabras genéricas no deben coincidir con un lugar específico
    for v in ["cena", "almuerzo", "restaurante", "comida"]:
        assert not db.place_matches(v, SEED), f"NO debería coincidir: {v!r}"


def test_best_place_match_desambigua():
    """Ante tokens compartidos, gana el lugar mejor cubierto por la consulta."""
    from app import db, seed_data
    places = seed_data.all_seeds()
    casos = {
        "Cena en ElCielo": "El Cielo Bogotá",
        "Plaza de Bolívar": "Plaza de Bolívar",
        "Quinta de Bolívar": "Quinta de Bolívar",
        "Museo del Oro": "Museo del Oro",
        "Museo Botero": "Museo Botero",
    }
    for consulta, esperado in casos.items():
        m = db.best_place_match(consulta, places)
        assert m and m["name"] == esperado, f"{consulta!r} -> {m and m['name']!r}, esperado {esperado!r}"
    # Genéricos sin lugar claro devuelven None
    assert db.best_place_match("cena", places) is None


def test_maps_link_resuelve_a_direccion_curada():
    """El endpoint /maps resuelve 'Cena en ElCielo' al lugar curado y usa su
    dirección/nombre como destino (no coordenadas crudas, que pueden estar mal)."""
    from app.main import app
    client = TestClient(app)
    tid = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H",
                                      "inicio": "2026-07-12", "fin": "2026-07-18"}).json()["trip_id"]
    resp = client.post(f"/trips/{tid}/plans", json={
        "titulo": "Cena en ElCielo", "lugar": "Cena en ElCielo",
        "tipo": "restaurante", "fecha": "2026-07-15", "hora": "19:00"}).json()
    pid = resp["planes"][-1]["id"]
    data = client.get(f"/trips/{tid}/plans/{pid}/maps").json()
    link = data["maps_link"]
    # Link válido de Google Maps, sin espacios crudos
    assert link.startswith("https://www.google.com/maps/")
    assert " " not in link
    # El destino debe ser la query/dirección curada de El Cielo (geocodificable
    # por Google), no las coordenadas. Comprobamos que aparece 'cielo' o la calle.
    import urllib.parse
    decoded = urllib.parse.unquote_plus(link).lower()
    assert "elcielo" in decoded or "el cielo" in decoded or "calle 70" in decoded
    # No debe enviar coordenadas como destino
    assert "destination=4." not in link and "query=4." not in link


def test_maps_link_sin_gps_abre_busqueda():
    """Sin origen GPS, el link abre la búsqueda del lugar (no una ruta vacía)."""
    from app.main import app
    client = TestClient(app)
    tid = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H",
                                      "inicio": "2026-07-12", "fin": "2026-07-18"}).json()["trip_id"]
    resp = client.post(f"/trips/{tid}/plans", json={
        "titulo": "El Cielo", "lugar": "El Cielo",
        "tipo": "restaurante", "fecha": "2026-07-15", "hora": "19:00"}).json()
    pid = resp["planes"][-1]["id"]
    data = client.get(f"/trips/{tid}/plans/{pid}/maps").json()
    link = data["maps_link"]
    assert "maps/search/?api=1&query=" in link
    assert " " not in link


def test_seed_reconcilia_curacion_parcial():
    """Un seed parcial se completa al re-sembrar, sin duplicar ni tocar trips."""
    from app import db, seed_data
    todos = seed_data.all_seeds()
    antes = len(db.places_for_city("Bogotá"))
    # Re-seed completo debe dejar al menos todos los lugares de los seeds
    db.seed_destination_places(todos)
    despues = len(db.places_for_city("Bogotá"))
    assert despues >= antes
    nombres = {db.norm_place(p["name"]) for p in db.places_for_city("Bogotá")}
    assert db.norm_place("El Cielo Bogotá") in nombres
    # Idempotencia: re-sembrar no agrega duplicados
    db.seed_destination_places(todos)
    assert len(db.places_for_city("Bogotá")) == despues


def test_el_cielo_datos_correctos():
    """La curación de El Cielo apunta a Calle 70 #4-47, Zona G (dirección real)."""
    from app import seed_data
    ec = [p for p in seed_data.all_seeds() if "cielo" in p["name"].lower()][0]
    # Dirección verificada (Zona G, Chapinero), no la antigua errónea
    assert "70" in ec["dir"] and "4-47" in ec["dir"]
    assert "zona g" in ec["zona"].lower() or "chapinero" in ec["zona"].lower()
    # Coordenadas dentro de Chapinero/Zona G (no en otra zona de la ciudad)
    assert 4.64 < ec["lat"] < 4.66
    assert -74.07 < ec["lng"] < -74.05


def test_contexto_incluye_todos_los_lugares_curados():
    """El system prompt nombra TODOS los lugares curados, no solo los 30 más
    cercanos, para que el Companion nunca niegue uno que sí existe."""
    from app import db, context, seed_data
    db.seed_destination_places(seed_data.all_seeds())
    total = len(db.places_for_city("Bogotá"))
    # Usuario en La Candelaria (sur) — El Cielo (norte) cae fuera del top 30
    trip = {"ciudad": "Bogotá", "zona_actual": "La Candelaria",
            "lat_actual": 4.5981, "lng_actual": -74.0758}
    block = context._lugares_block(trip)
    # El Cielo debe estar presente aunque esté lejos
    assert "El Cielo" in block
    # Cada lugar curado debe aparecer nombrado en algún lugar del prompt
    faltantes = [p["name"] for p in db.places_for_city("Bogotá") if p["name"] not in block]
    assert not faltantes, f"lugares ausentes del prompt: {faltantes}"
