import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from app import context, db, seed_data
import investigar_lugares as inv
from tests.test_companion import _auth_headers


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    from app import auth, limits
    db.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    auth.init_auth()
    limits.init_limits()
    db.seed_destination_places(seed_data.all_seeds())
    yield


def _client_trip():
    from app.main import app
    client = TestClient(app)
    H = _auth_headers(client)
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "inicio": "2099-07-12", "fin": "2099-07-18"}, headers=H).json()
    return client, H, t["trip_id"]


def test_ficha_de_lugar_curado_trae_todo():
    client, H, tid = _client_trip()
    r = client.get(f"/trips/{tid}/lugar", params={"nombre": "museo del oro"}, headers=H)
    assert r.status_code == 200
    f = r.json()
    assert f["name"] == "Museo del Oro"
    assert f["descripcion"] and f["local"].get("clave")
    for k in ("historia", "hacer", "no_hacer", "probar", "secretos", "horario_hoy", "fotos"):
        assert k in f
    assert f["fotos"] == []   # sin key de Google en tests: la ficha sale igual, sin fotos


def test_ficha_404_si_no_es_de_la_curacion():
    client, H, tid = _client_trip()
    r = client.get(f"/trips/{tid}/lugar", params={"nombre": "Restaurante Que No Existe Jamás"}, headers=H)
    assert r.status_code == 404


def test_ficha_exige_dueno_del_viaje():
    client, _, tid = _client_trip()
    otro = _auth_headers(client)
    assert client.get(f"/trips/{tid}/lugar", params={"nombre": "Museo del Oro"}, headers=otro).status_code in (403, 404)


def test_capa_profunda_se_pega_y_los_secretos_van_al_prompt():
    lugares = {p["name"]: p for p in seed_data.all_seeds()}
    prof = seed_data._profundo()
    if not prof:
        pytest.skip("sin capa app/lugares_profundo.json")
    nombre = next(iter(prof))
    local = lugares[nombre]["local"]
    assert local["profundo"] == prof[nombre]
    if prof[nombre].get("secretos"):
        assert "SECRETO: " + prof[nombre]["secretos"][0]["texto"] in context._local_suffix(local)


def test_item_marca_origen_por_cita_literal():
    fuentes = [("https://guia.co/museo", inv._n("El museo cierra los lunes y abre hasta las siete de la noche el resto de la semana."))]
    con = inv.item({"texto": "No vayas un lunes: cierra ese día.", "cita": "El museo cierra los lunes y abre hasta"}, fuentes)
    assert con == {"texto": "No vayas un lunes: cierra ese día.", "origen": "fuente", "url": "https://guia.co/museo"}
    sin = inv.item({"texto": "Pide la mesa junto a la ventana del segundo piso.", "cita": ""}, fuentes)
    assert sin == {"texto": "Pide la mesa junto a la ventana del segundo piso.", "origen": "modelo"}
    # Cifras sin respaldo: lo que más se inventa → fuera.
    assert inv.item({"texto": "La entrada cuesta $5.000 pesos los domingos.", "cita": ""}, fuentes) is None
    assert inv.item({"texto": "corto", "cita": ""}, fuentes) is None


def test_nombre_nucleo_quita_ciudad_y_parentesis():
    assert inv.nombre_nucleo("Sala de Despecho Bogotá") == "Sala de Despecho"
    assert inv.nombre_nucleo("Suesca (senderismo y escalada)") == "Suesca"
    assert inv.nombre_nucleo("Museo del Oro") == "Museo del Oro"


def test_validar_descarta_lugar_sin_ninguna_fuente():
    ev = {"web": "", "web_texto": "", "paginas": [{"url": "https://g.co/x", "texto": "Un texto cualquiera sobre el lugar en Bogotá."}]}
    raw = '{"secretos": [{"texto": "Pide la mesa del rincón junto a la ventana.", "cita": ""}]}'
    assert inv.validar(raw, ev) is None
