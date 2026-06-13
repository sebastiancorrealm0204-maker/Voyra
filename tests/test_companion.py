import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from app import db, engine, seed_data


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    db.seed_destination_places(seed_data.all_seeds())
    engine.QUIET_START, engine.QUIET_END = 24.0, 0.0  # desactivar quiet hours en tests
    yield


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
        assert x["notification"]["maps_link"].startswith("https://www.google.com/maps/dir/?api=1&destination=")

    # El más lejano debería ser Zipaquirá (fuera de la ciudad, ~50km)
    distancias = [x["distancia_km"] for x in r["resultados"] if x["distancia_km"] is not None]
    assert max(distancias) > 5  # algo bastante más lejos que el resto de Usaquén


def test_nearby_dedup_no_repeats():
    """Llamadas repetidas a /nearby no repiten lugares; se agota cubriendo todos los curados."""
    from app.main import app
    client = TestClient(app)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "Hotel Zona G", "inicio": "2026-08-01",
                                    "fin": "2026-08-05", "gustos": ["Gastronomía local"]}).json()
    tid = t["trip_id"]
    client.post(f"/trips/{tid}/location", json={"zona": "Usaquén", "disparar_geofence": False})

    vistos_total = set()
    agotado = False
    for _ in range(6):
        r = client.post(f"/trips/{tid}/nearby").json()
        lugares = {x["lugar"] for x in r["resultados"]}
        assert lugares.isdisjoint(vistos_total)  # nunca repite uno ya visto
        vistos_total |= lugares
        if not r["resultados"]:
            assert "nota" in r
            agotado = True
            break

    assert agotado
    assert len(vistos_total) == 13  # cubrió los 13 lugares curados, sin repetir ninguno
