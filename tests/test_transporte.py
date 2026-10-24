"""Cómo llegar: lógica pura (tráfico, precio, recomendación, parseo de Google) y el endpoint."""
import datetime

import pytest
from fastapi.testclient import TestClient

from app import transporte as t

TZ = t.TZ


def test_nivel_de_trancon_por_cociente():
    assert t.nivel_trancon(1000, 1000)["nivel"] == "fluido"
    assert t.nivel_trancon(1300, 1000)["nivel"] == "moderado"
    assert t.nivel_trancon(1600, 1000)["nivel"] == "pesado"
    r = t.nivel_trancon(3480, 1440)  # 58 min vs 24 min
    assert r["nivel"] == "trancon" and r["extra_min"] == 34
    assert t.nivel_trancon(500, 0)["nivel"] == "fluido"  # sin dato de referencia no acusamos


def test_precio_taxi_tiene_minimo_y_rango_redondeado():
    corto = t.precio_taxi(0.5, 3)
    assert corto["min"] >= 7000 and corto["max"] > corto["min"] and corto["moneda"] == "COP"
    largo = t.precio_taxi(17, 30)
    assert 30000 <= largo["min"] < largo["max"] <= 60000
    assert largo["min"] % 1000 == 0 and largo["max"] % 1000 == 0


def test_hora_pico_solo_en_dias_habiles():
    lunes_7 = datetime.datetime(2026, 10, 26, 7, 0, tzinfo=TZ)
    lunes_12 = datetime.datetime(2026, 10, 26, 12, 0, tzinfo=TZ)
    lunes_17 = datetime.datetime(2026, 10, 26, 17, 30, tzinfo=TZ)
    sabado_7 = datetime.datetime(2026, 10, 24, 7, 0, tzinfo=TZ)
    assert t.hora_pico(lunes_7) and t.hora_pico(lunes_17)
    assert not t.hora_pico(lunes_12) and not t.hora_pico(sabado_7)


@pytest.mark.parametrize("op,nivel,esperado", [
    ({"caminar": {"min": 10}, "taxi": {"min": 5}, "transporte": {"min": 20}}, "fluido", ("caminar", "cerca")),
    ({"taxi": {"min": 55}, "transporte": {"min": 60}}, "trancon", ("transporte", "trancon_gana_transporte")),
    ({"taxi": {"min": 25}, "transporte": {"min": 27}}, "fluido", ("transporte", "similar_y_mas_barato")),
    ({"taxi": {"min": 20}, "transporte": {"min": 45}}, "fluido", ("taxi", "taxi_mas_rapido")),
    ({"taxi": {"min": 20}}, "fluido", ("taxi", "solo_taxi")),
    ({"transporte": {"min": 40}}, "pesado", ("transporte", "solo_transporte")),
    ({}, "fluido", (None, "sin_opciones")),
])
def test_recomendacion(op, nivel, esperado):
    r = t.recomendar(op, nivel)
    assert (r["modo"], r["motivo"]) == esperado


RUTA_TRANSITO = {
    "duration": "2400s",
    "legs": [{"steps": [
        {"travelMode": "WALK", "staticDuration": "180s", "distanceMeters": 250},
        {"travelMode": "WALK", "staticDuration": "120s", "distanceMeters": 130},   # Google parte la caminata en pasos de esquina
        {"travelMode": "TRANSIT", "staticDuration": "1500s", "transitDetails": {
            "stopCount": 9, "stopDetails": {"departureStop": {"name": "Portal El Dorado"}, "arrivalStop": {"name": "Museo del Oro"}},
            "transitLine": {"nameShort": "M86", "color": "#ff0000", "agencies": [{"name": "Transmilenio-Dual"}],
                            "vehicle": {"type": "BUS", "name": {"text": "Autobús"}}}}},
        {"travelMode": "TRANSIT", "staticDuration": "420s", "transitDetails": {
            "stopCount": 3, "stopDetails": {"departureStop": {"name": "Museo del Oro"}, "arrivalStop": {"name": "Las Aguas"}},
            "transitLine": {"name": "B27", "agencies": [{"name": "Zonal-Urbano"}], "vehicle": {"type": "BUS", "name": {"text": "Autobús"}}}}},
        {"travelMode": "WALK", "staticDuration": "100s", "distanceMeters": 120},
        {"travelMode": "WALK", "staticDuration": "80s", "distanceMeters": 80},
    ]}],
}


def test_parseo_de_transito_de_google():
    r = t.parsear_transito(RUTA_TRANSITO)
    assert r["min"] == 40 and r["transbordos"] == 1 and r["caminata_min"] == 8  # 180+120+100+80 s
    tipos = [p["tipo"] for p in r["pasos"]]
    assert tipos == ["caminar", "transporte", "transporte", "caminar"]
    bus = r["pasos"][1]
    assert bus["linea"] == "M86" and bus["vehiculo"] == "TransMilenio" and bus["desde"] == "Portal El Dorado"
    assert bus["hasta"] == "Museo del Oro" and bus["paradas"] == 9
    assert r["pasos"][2]["vehiculo"] == "SITP"                      # Zonal-Urbano → SITP
    assert r["pasos"][0] == {"tipo": "caminar", "min": 5, "metros": 380}   # los dos pasos de caminata, unidos
    assert r["pasos"][3] == {"tipo": "caminar", "min": 3, "metros": 200}


def test_etiqueta_de_agencia():
    assert t.etiqueta_agencia(["Transmilenio-Dual"]) == "TransMilenio"
    assert t.etiqueta_agencia(["Zonal-Urbano"]) == "SITP"
    assert t.etiqueta_agencia(["Otra"], "Autobús") == "Autobús" and t.etiqueta_agencia([]) == ""


def test_buscar_lugar_ignora_tildes_y_mayusculas():
    lugares = [{"name": "Museo del Oro", "lat": 1, "lng": 2}, {"name": "Museo Botero", "lat": 3, "lng": 4}]
    assert t.buscar_lugar("museo del ORO", lugares)["name"] == "Museo del Oro"
    assert t.buscar_lugar("botero", lugares)["name"] == "Museo Botero"
    assert t.buscar_lugar("zzz", lugares) is None and t.buscar_lugar("", lugares) is None


ORIGEN = (4.7016, -74.1469, "Aeropuerto")
DESTINO = (4.5981, -74.0758, "La Candelaria")


@pytest.fixture(autouse=True)
def _sin_cache():
    t._cache = t.net.TTLCache(ttl_s=300, maxsize=16)


@pytest.fixture(autouse=True)
def _db_temporal(tmp_path):
    """Base de datos propia: sin esto, correr este archivo solo usaba voyra.db y chocaba con usuarios de otras corridas."""
    from app import auth, db, limits, seed_data
    db.DB_PATH = str(tmp_path / "transporte.db")
    db.init_db()
    auth.init_auth()
    limits.init_limits()
    db.seed_destination_places(seed_data.all_seeds())


def test_sin_routes_api_responde_en_modo_demo(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "")
    r = t.rutas(ORIGEN, DESTINO)
    assert r["fuente"] == "demo"
    assert {"taxi", "transporte"} <= set(r["opciones"]) and r["opciones"]["transporte"]["pasos"] == []  # no inventa líneas
    assert r["recomendacion"]["modo"] in ("taxi", "transporte")
    assert "uber" in r["enlaces"] and "maps_transporte" in r["enlaces"]
    assert r["origen"]["nombre"] == "Aeropuerto" and r["destino"]["nombre"] == "La Candelaria"


def test_con_routes_api_usa_trafico_real(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")

    def falso(modo, o, d, salida):
        return {"duration": "3480s", "staticDuration": "1440s", "distanceMeters": 17000} if modo == "DRIVE" else RUTA_TRANSITO

    monkeypatch.setattr(t, "_pedir_google", falso)
    r = t.rutas(ORIGEN, DESTINO)
    assert r["fuente"] == "google"
    assert r["opciones"]["taxi"]["min"] == 58 and r["opciones"]["taxi"]["min_sin_trafico"] == 24
    assert r["trancon"]["nivel"] == "trancon" and r["trancon"]["extra_min"] == 34
    assert r["opciones"]["transporte"]["min"] == 40
    assert r["recomendacion"] == {"modo": "transporte", "motivo": "trancon_gana_transporte"}


def test_si_google_falla_cae_al_modo_demo(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")

    def roto(*a, **k):
        raise t.RoutesNoDisponible("http_403")

    monkeypatch.setattr(t, "_pedir_google", roto)
    assert t.rutas(ORIGEN, DESTINO)["fuente"] == "demo"


def test_la_misma_consulta_se_sirve_de_cache(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")
    llamadas = []

    def falso(modo, o, d, salida):
        llamadas.append(modo)
        return {"duration": "600s", "staticDuration": "500s", "distanceMeters": 5000} if modo == "DRIVE" else None

    monkeypatch.setattr(t, "_pedir_google", falso)
    t.rutas(ORIGEN, DESTINO)
    n = len(llamadas)
    t.rutas(ORIGEN, DESTINO)
    assert len(llamadas) == n  # no vuelve a pegarle a Google


# ───────────── endpoint ─────────────
def _cliente():
    from app.main import app
    from tests.test_companion import _auth_headers
    c = TestClient(app)
    return c, _auth_headers(c)


def test_endpoint_destino_curado_aeropuerto_y_errores(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "")
    c, H = _cliente()
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "H"}, headers=H).json()["trip_id"]
    r = c.post(f"/trips/{tid}/transporte", json={"destino": "Museo del Oro", "salida_min": 0}, headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["fuente"] == "demo" and j["destino"]["nombre"] == "Museo del Oro" and j["opciones"]["taxi"]["min"] > 0
    r = c.post(f"/trips/{tid}/transporte", json={"destino": "aeropuerto", "origen_lat": 4.65, "origen_lng": -74.06}, headers=H)
    assert r.status_code == 200 and r.json()["destino"]["nombre"] == "Aeropuerto El Dorado"
    r = c.post(f"/trips/{tid}/transporte", json={"destino": "Lugar que no existe"}, headers=H)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "destino_no_encontrado"
    r = c.post(f"/trips/{tid}/transporte", json={"lat": 4.6, "lng": -74.07, "nombre": "Mi punto", "salida_min": 60}, headers=H)
    assert r.status_code == 200 and r.json()["salida_min"] == 60
    # otro usuario no puede usar el viaje
    c2, H2 = _cliente()
    assert c2.post(f"/trips/{tid}/transporte", json={"destino": "aeropuerto"}, headers=H2).status_code in (403, 404)
