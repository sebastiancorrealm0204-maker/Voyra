"""Códigos de acceso por hotel: controlan quién se registra, con tope de usos, y anotan el hotel de cada usuario."""
import uuid

from fastapi.testclient import TestClient

from app import accesos, db
from app.main import app

ADMIN = {"X-Admin-Token": "token-de-prueba"}


def _signup(c, codigo=""):
    email = f"cod{uuid.uuid4().hex[:8]}@example.com"
    n = uuid.uuid4().int % 10**7
    return email, c.post("/auth/signup", json={"email": email, "password": "supersecret", "telefono": f"+5730{n:07d}", "codigo": codigo})


def test_con_codigo_requerido_no_se_entra_sin_codigo_valido(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.setenv("REQUIRE_ACCESS_CODE", "1")
    c = TestClient(app)
    assert c.get("/auth/config").json() == {"codigo_requerido": True}
    cod = c.post("/admin/codigos", json={"hotel": "Dann Carlton", "max_usos": 2}, headers=ADMIN).json()["codigo"]
    assert cod.startswith("DAN-") and len(cod) == 8   # corto: 3 letras del hotel + 4 caracteres

    _, r = _signup(c)                                   # sin código
    assert r.status_code == 403 and r.json()["detail"]["error"] == "codigo_invalido"
    _, r = _signup(c, "NOEXISTE-1234")
    assert r.status_code == 403

    assert c.get(f"/auth/codigo/{cod.lower()}").json() == {"valido": True, "hotel": "Dann Carlton"}   # no gasta usos
    email, r = _signup(c, cod.lower())                  # minúsculas también sirve
    assert r.status_code == 200
    with db.conn() as con:
        u = con.execute("SELECT codigo_acceso, hotel_origen FROM users WHERE email=?", (email,)).fetchone()
    assert u["codigo_acceso"] == cod and u["hotel_origen"] == "Dann Carlton"

    assert _signup(c, cod)[1].status_code == 200        # segundo uso
    assert _signup(c, cod)[1].status_code == 403        # se acabaron los 2 cupos
    lista = {x["codigo"]: x for x in c.get("/admin/codigos", headers=ADMIN).json()["codigos"]}
    assert lista[cod]["usos"] == 2


def test_un_registro_fallido_no_gasta_el_cupo(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.setenv("REQUIRE_ACCESS_CODE", "1")
    c = TestClient(app)
    cod = c.post("/admin/codigos", json={"hotel": "Hotel Uno", "max_usos": 1}, headers=ADMIN).json()["codigo"]
    r = c.post("/auth/signup", json={"email": "no-es-email", "password": "supersecret", "codigo": cod})
    assert r.status_code == 400
    assert _signup(c, cod)[1].status_code == 200        # el cupo seguía disponible


def test_codigo_desactivado_ya_no_sirve(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.setenv("REQUIRE_ACCESS_CODE", "1")
    c = TestClient(app)
    cod = c.post("/admin/codigos", json={"hotel": "Hotel Dos"}, headers=ADMIN).json()["codigo"]
    assert c.post(f"/admin/codigos/{cod}/desactivar", headers=ADMIN).status_code == 200
    assert _signup(c, cod)[1].status_code == 403
    assert c.get(f"/auth/codigo/{cod}").json()["valido"] is False


def test_sin_requisito_el_codigo_es_opcional_y_anota_el_hotel(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.delenv("REQUIRE_ACCESS_CODE", raising=False)
    c = TestClient(app)
    cod = c.post("/admin/codigos", json={"hotel": "Hotel Tres"}, headers=ADMIN).json()["codigo"]
    assert c.get("/auth/config").json() == {"codigo_requerido": False}
    assert _signup(c)[1].status_code == 200             # sin código: entra
    email, r = _signup(c, cod)
    assert r.status_code == 200
    with db.conn() as con:
        assert con.execute("SELECT hotel_origen h FROM users WHERE email=?", (email,)).fetchone()["h"] == "Hotel Tres"


def test_el_admin_es_obligatorio(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    assert c.post("/admin/codigos", json={"hotel": "X"}).status_code == 403
    assert c.get("/admin/codigos").status_code == 403


def test_sin_limite_por_ip_por_defecto():
    from app import auth
    assert auth._SIGNUP_MAX_PER_IP == 0 or auth.ip_signup_blocked("1.2.3.4") is False


def test_lote_de_codigos_individuales(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.setenv("REQUIRE_ACCESS_CODE", "1")
    c = TestClient(app)
    lote = c.post("/admin/codigos/lote", json={"hotel": "Hotel Lote", "cantidad": 3}, headers=ADMIN).json()["codigos"]
    assert len(lote) == 3 and len({x["codigo"] for x in lote}) == 3 and all(x["max_usos"] == 1 for x in lote)
    uno = lote[0]["codigo"]
    assert _signup(c, uno)[1].status_code == 200
    assert _signup(c, uno)[1].status_code == 403       # cada código sirve para una sola persona
    assert _signup(c, lote[1]["codigo"])[1].status_code == 200


def test_el_codigo_se_puede_escribir_de_cualquier_forma(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.setenv("REQUIRE_ACCESS_CODE", "1")
    c = TestClient(app)
    cod = c.post("/admin/codigos", json={"hotel": "Hotel Forma", "max_usos": 0}, headers=ADMIN).json()["codigo"]
    sin_guion = cod.replace("-", "").lower()
    con_espacio = cod.replace("-", " ")
    for escrito in (sin_guion, con_espacio, cod.lower()):
        assert c.get(f"/auth/codigo/{escrito}").json()["valido"] is True
        assert _signup(c, escrito)[1].status_code == 200
