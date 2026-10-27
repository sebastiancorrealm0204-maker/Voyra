"""Panel de administración: acceso por correo admin o token, usuarios y sus límites, ajustes y suspensión."""
import uuid

from fastapi.testclient import TestClient

from app import limits
from app.main import app

TOKEN = {"X-Admin-Token": "token-de-prueba"}


def _usuario(client, email=None):
    email = email or f"adm{uuid.uuid4().hex[:8]}@example.com"
    n = uuid.uuid4().int % 10**7
    r = client.post("/auth/signup", json={"email": email, "password": "supersecret", "telefono": f"+5730{n:07d}"})
    link = r.json().get("dev_verification_link")
    if link:
        client.get("/auth/verify", params={"token": link.split("token=")[1]})
    tok = client.post("/auth/login", json={"email": email, "password": "supersecret"}).json()["token"]
    return email, {"Authorization": f"Bearer {tok}"}


def test_solo_el_admin_entra(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    email_admin = f"jefe{uuid.uuid4().hex[:6]}@example.com"
    monkeypatch.setenv("ADMIN_EMAILS", email_admin.upper())      # mayúsculas: no importa
    _, normal = _usuario(c)
    _, admin = _usuario(c, email_admin)
    assert c.get("/admin/resumen").status_code == 403
    assert c.get("/admin/resumen", headers=normal).status_code == 403
    assert c.get("/admin/resumen", headers=admin).status_code == 200      # con su usuario y contraseña
    assert c.get("/admin/resumen", headers=TOKEN).status_code == 200      # o con el token
    assert c.get("/auth/me", headers=admin).json()["es_admin"] is True
    assert c.get("/auth/me", headers=normal).json()["es_admin"] is False


def test_limites_por_usuario_y_ajustes_globales(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    email, h = _usuario(c)
    uid = c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"][0]["id"]

    assert c.patch(f"/admin/usuarios/{uid}/limites", json={"limit_chat": 77}, headers=TOKEN).status_code == 200
    d = c.get(f"/admin/usuarios/{uid}", headers=TOKEN).json()
    assert d["limites_efectivos"]["chat"] == 77 and d["overrides"]["limit_chat"] == 77
    assert c.get("/auth/me", headers=h).json()["cuotas"]["chat"]["limite"] == 77       # lo ve el usuario
    assert c.patch(f"/admin/usuarios/{uid}/limites", json={"limit_chat": None}, headers=TOKEN).status_code == 200
    assert c.get(f"/admin/usuarios/{uid}", headers=TOKEN).json()["overrides"]["limit_chat"] is None

    # ajuste global desde el panel (vuelve al default con null)
    assert c.put("/admin/config", json={"key": "limit.chat", "value": "33"}, headers=TOKEN).status_code == 200
    assert c.get(f"/admin/usuarios/{uid}", headers=TOKEN).json()["limites_efectivos"]["chat"] == 33
    assert c.put("/admin/config", json={"key": "limit.chat", "value": None}, headers=TOKEN).status_code == 200
    assert c.get(f"/admin/usuarios/{uid}", headers=TOKEN).json()["limites_efectivos"]["chat"] == limits.FREE_LIMITS["chat"]
    assert c.put("/admin/config", json={"key": "no_existe", "value": "1"}, headers=TOKEN).status_code == 422
    assert c.put("/admin/config", json={"key": "limit.chat", "value": "-4"}, headers=TOKEN).status_code == 422


def test_exigir_codigo_se_cambia_desde_el_panel(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.delenv("REQUIRE_ACCESS_CODE", raising=False)
    c = TestClient(app)
    try:
        assert c.get("/auth/config").json()["codigo_requerido"] is False
        c.put("/admin/config", json={"key": "require_access_code", "value": "1"}, headers=TOKEN)
        assert c.get("/auth/config").json()["codigo_requerido"] is True
    finally:
        c.put("/admin/config", json={"key": "require_access_code", "value": None}, headers=TOKEN)


def test_suspender_cierra_la_sesion_y_no_deja_volver(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    email, h = _usuario(c)
    uid = c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"][0]["id"]
    assert c.get("/auth/me", headers=h).status_code == 200
    assert c.post(f"/admin/usuarios/{uid}/suspender", json={"suspendido": True}, headers=TOKEN).status_code == 200
    assert c.get("/auth/me", headers=h).status_code == 401                                       # sesión cerrada
    assert c.post("/auth/login", json={"email": email, "password": "supersecret"}).status_code == 403
    assert c.post(f"/admin/usuarios/{uid}/suspender", json={"suspendido": False}, headers=TOKEN).status_code == 200
    assert c.post("/auth/login", json={"email": email, "password": "supersecret"}).status_code == 200


def test_reiniciar_hoy_devuelve_los_cupos_diarios(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    monkeypatch.setitem(limits.FREE_LIMITS, "chat", 1)
    c = TestClient(app)
    email, h = _usuario(c)
    tid = c.post("/trips", json={"ciudad": "Bogotá"}, headers=h).json()["trip_id"]
    assert c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=h).status_code == 200
    assert c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=h).status_code == 429
    uid = c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"][0]["id"]
    assert c.post(f"/admin/usuarios/{uid}/reiniciar-hoy", headers=TOKEN).status_code == 200
    assert c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=h).status_code == 200


def test_resumen_feedback_y_lista_de_espera(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    r = c.get("/admin/resumen", headers=TOKEN).json()
    assert {"usuarios", "viajes", "uso_hoy", "costo_estimado_hoy", "lista_espera", "feedback", "codigos"} <= set(r)
    assert "limite" in c.get("/admin/feedback", headers=TOKEN).json()
    assert "filas" in c.get("/admin/lista-espera", headers=TOKEN).json()
    assert "ajustes" in c.get("/admin/config", headers=TOKEN).json()


def test_quitar_celular_libera_el_numero(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    n = uuid.uuid4().int % 10**7
    tel = f"+5730{n:07d}"
    email = f"cel{uuid.uuid4().hex[:8]}@example.com"
    assert c.post("/auth/signup", json={"email": email, "password": "supersecret", "telefono": tel}).status_code == 200
    otro = f"cel{uuid.uuid4().hex[:8]}@example.com"
    assert c.post("/auth/signup", json={"email": otro, "password": "supersecret", "telefono": tel}).status_code == 409     # ocupado
    uid = c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"][0]["id"]
    assert c.post(f"/admin/usuarios/{uid}/quitar-celular", headers=TOKEN).status_code == 200
    assert c.post("/auth/signup", json={"email": otro, "password": "supersecret", "telefono": tel}).status_code == 200      # ya libre


def test_eliminar_cuenta_borra_todo_lo_suyo(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    email, h = _usuario(c)
    tid = c.post("/trips", json={"ciudad": "Bogotá"}, headers=h).json()["trip_id"]
    c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers=h)
    uid = c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"][0]["id"]
    assert c.delete(f"/admin/usuarios/{uid}", headers=TOKEN).status_code == 200
    assert c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"] == []
    assert c.get("/auth/me", headers=h).status_code == 401
    from app import db
    with db.conn() as con:
        assert con.execute("SELECT COUNT(*) n FROM trips WHERE id=?", (tid,)).fetchone()["n"] == 0
        assert con.execute("SELECT COUNT(*) n FROM messages WHERE trip_id=?", (tid,)).fetchone()["n"] == 0
    assert c.delete(f"/admin/usuarios/{uid}", headers=TOKEN).status_code == 404


def test_no_se_puede_eliminar_a_un_admin(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    jefe = f"jefe{uuid.uuid4().hex[:6]}@example.com"
    monkeypatch.setenv("ADMIN_EMAILS", jefe)
    _, h = _usuario(c, jefe)
    uid = c.get("/admin/usuarios", params={"q": jefe}, headers=TOKEN).json()["usuarios"][0]["id"]
    assert c.delete(f"/admin/usuarios/{uid}", headers=h).status_code == 403
    assert c.delete(f"/admin/usuarios/{uid}", headers=TOKEN).status_code == 403


def test_editar_datos_clave_y_plan(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    email, h = _usuario(c)
    uid = c.get("/admin/usuarios", params={"q": email}, headers=TOKEN).json()["usuarios"][0]["id"]
    n = uuid.uuid4().int % 10**7
    tel = f"+5731{n:07d}"
    assert c.patch(f"/admin/usuarios/{uid}/datos", json={"nombre": "Ana Prueba", "telefono": tel, "plan": "tester"}, headers=TOKEN).status_code == 200
    d = c.get(f"/admin/usuarios/{uid}", headers=TOKEN).json()
    assert d["nombre"] == "Ana Prueba" and d["telefono"] == tel and d["plan"] == "tester"
    assert c.patch(f"/admin/usuarios/{uid}/datos", json={"plan": "dios"}, headers=TOKEN).status_code == 422
    assert c.patch(f"/admin/usuarios/{uid}/datos", json={"telefono": "123"}, headers=TOKEN).status_code == 422
    # el celular no puede repetirse en otra cuenta
    email2, _ = _usuario(c)
    uid2 = c.get("/admin/usuarios", params={"q": email2}, headers=TOKEN).json()["usuarios"][0]["id"]
    assert c.patch(f"/admin/usuarios/{uid2}/datos", json={"telefono": tel}, headers=TOKEN).status_code == 409
    # contraseña nueva: cierra las sesiones y entra con la nueva
    assert c.post(f"/admin/usuarios/{uid}/clave", json={"password": "corta"}, headers=TOKEN).status_code == 422
    assert c.post(f"/admin/usuarios/{uid}/clave", json={"password": "claveNueva123"}, headers=TOKEN).status_code == 200
    assert c.get("/auth/me", headers=h).status_code == 401
    assert c.post("/auth/login", json={"email": email, "password": "supersecret"}).status_code == 401
    assert c.post("/auth/login", json={"email": email, "password": "claveNueva123"}).status_code == 200


def test_viajes_lista_espera_y_sistema(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    email, h = _usuario(c)
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "Hotel Prueba Admin"}, headers=h).json()["trip_id"]
    lista = c.get("/admin/viajes", params={"q": "Hotel Prueba Admin"}, headers=TOKEN).json()["viajes"]
    assert any(v["id"] == tid and v["email"] == email for v in lista)
    assert c.delete(f"/admin/viajes/{tid}", headers=TOKEN).status_code == 200
    assert c.delete(f"/admin/viajes/{tid}", headers=TOKEN).status_code == 404

    c.post("/waitlist", json={"email": "quitar-lista@example.com", "name": "X"})
    assert c.delete("/admin/lista-espera/quitar-lista@example.com", headers=TOKEN).status_code == 200
    assert c.delete("/admin/lista-espera/quitar-lista@example.com", headers=TOKEN).status_code == 404

    s = c.get("/admin/sistema", headers=TOKEN).json()
    assert s["base_de_datos"]["ok"] is True and "llaves" in s and "solo_en_railway" in s
    assert all(isinstance(v, bool) for v in s["llaves"].values())      # nunca valores de llaves, solo si están puestas


def test_ajustes_de_gasto_aplican_en_vivo(monkeypatch):
    monkeypatch.setenv("COMPANION_ADMIN_TOKEN", "token-de-prueba")
    c = TestClient(app)
    try:
        assert c.put("/admin/config", json={"key": "maps_global", "value": "0"}, headers=TOKEN).status_code == 200
        email, h = _usuario(c)
        # con el tope global en 0, la búsqueda en vivo se pausa para todos
        from app import limits
        import pytest
        with pytest.raises(limits.QuotaError) as e:
            limits.check_and_consume("cualquiera", "maps")
        assert e.value.global_block is True
    finally:
        c.put("/admin/config", json={"key": "maps_global", "value": None}, headers=TOKEN)
