"""Feedback a cambio de más límite: el porqué es obligatorio, se amplía una vez al día y los viajes suman de a uno."""
import uuid

from fastapi.testclient import TestClient

from app import limits
from app.main import app


def _usuario(client):
    email = f"fb{uuid.uuid4().hex[:8]}@example.com"
    link = client.post("/auth/signup", json={"email": email, "password": "supersecret"}).json()["dev_verification_link"]
    client.get("/auth/verify", params={"token": link.split("token=")[1]})
    tok = client.post("/auth/login", json={"email": email, "password": "supersecret"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_feedback_amplia_el_limite_del_dia(monkeypatch):
    monkeypatch.setitem(limits.FREE_LIMITS, "chat", 2)
    c = TestClient(app)
    H = _usuario(c)
    tid = c.post("/trips", json={"ciudad": "Bogotá"}, headers=H).json()["trip_id"]
    for _ in range(2):
        assert c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=H).status_code == 200
    assert c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=H).status_code == 429

    r = c.post("/feedback/limite", json={"recurso": "chat", "gusta": True, "motivo": "Me ayudó a encontrar dónde cenar"}, headers=H)
    assert r.status_code == 200 and r.json()["ampliado"] is True
    assert r.json()["cuotas"]["chat"]["limite"] == 4
    assert c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=H).status_code == 200

    # una sola ampliación por día
    r2 = c.post("/feedback/limite", json={"recurso": "chat", "gusta": False, "motivo": "Falta más información de rutas"}, headers=H)
    assert r2.json()["ampliado"] is False and r2.json()["razon"] == "ya_ampliado_hoy"
    assert r2.json()["cuotas"]["chat"]["limite"] == 4


def test_el_porque_es_obligatorio(monkeypatch):
    c = TestClient(app)
    H = _usuario(c)
    r = c.post("/feedback/limite", json={"recurso": "chat", "gusta": True, "motivo": "ok"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"]["error"] == "motivo_corto"
    assert c.post("/feedback/limite", json={"recurso": "otro", "gusta": True, "motivo": "texto suficiente"}, headers=H).status_code == 422
    assert c.post("/feedback/limite", json={"recurso": "chat", "gusta": True, "motivo": "texto suficiente"}).status_code == 401


def test_feedback_suma_un_viaje_hasta_el_maximo(monkeypatch):
    monkeypatch.setattr(limits, "MAX_TRIPS_PER_USER", 1)
    from app import feedback_limite
    monkeypatch.setattr(feedback_limite, "BONUS_TRIPS_MAX", 1)
    c = TestClient(app)
    H = _usuario(c)
    assert c.post("/trips", json={"ciudad": "Bogotá"}, headers=H).status_code == 200
    assert c.post("/trips", json={"ciudad": "Bogotá"}, headers=H).status_code == 429
    r = c.post("/feedback/limite", json={"recurso": "trips", "gusta": True, "motivo": "Quiero planear otro viaje"}, headers=H)
    assert r.json()["ampliado"] is True and r.json()["trips"]["limite"] == 2
    assert c.post("/trips", json={"ciudad": "Bogotá"}, headers=H).status_code == 200
    r = c.post("/feedback/limite", json={"recurso": "trips", "gusta": True, "motivo": "Quiero planear otro viaje más"}, headers=H)
    assert r.json()["ampliado"] is False and r.json()["razon"] == "tope_viajes"
