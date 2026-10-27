import uuid

from fastapi.testclient import TestClient

from app import main


def _usuario(c):
    n = uuid.uuid4().int % 10**7
    email = f"lista_app_{n}@example.com"
    tel = f"+5730{n:07d}"
    r = c.post("/auth/signup", json={"email": email, "password": "claveSegura123", "nombre": "Ana", "telefono": tel, "pais": "Colombia"})
    assert r.status_code in (200, 201), r.text
    link = r.json().get("dev_verification_link")
    if link:
        c.get("/auth/verify", params={"token": link.split("token=")[1]})
    tok = c.post("/auth/login", json={"email": email, "password": "claveSegura123"}).json()["token"]
    return email, tel, {"Authorization": f"Bearer {tok}"}


def test_unirse_a_la_lista_desde_la_app():
    c = TestClient(main.app)
    email, tel, h = _usuario(c)
    assert c.get("/waitlist/me", headers=h).json() == {"unido": False}
    r = c.post("/waitlist/me", json={"momento": "dia2"}, headers=h)
    assert r.json()["status"] == "added"
    assert c.get("/waitlist/me", headers=h).json() == {"unido": True}
    # idempotente
    assert c.post("/waitlist/me", json={"momento": "cierre"}, headers=h).json()["status"] == "already"
    from app import db
    with db.conn() as con:
        row = con.execute("SELECT source, name, phone FROM waitlist WHERE email=?", (email,)).fetchone()
    assert row["source"] == "app-dia2" and row["name"] == "Ana" and row["phone"] == tel


def test_lista_desde_la_app_exige_sesion():
    c = TestClient(main.app)
    assert c.get("/waitlist/me").status_code == 401
    assert c.post("/waitlist/me", json={}).status_code == 401
