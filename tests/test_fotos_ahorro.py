"""Fotos: Place Details se paga una vez por lugar (los nombres quedan guardados) y la tarjeta trae 1 foto por defecto."""
import httpx
import pytest

from app import db, places_live, visual


@pytest.fixture
def google(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fotos.db"))
    monkeypatch.setattr(db, "IS_POSTGRES", False)
    monkeypatch.setattr(places_live, "API_KEY", "k")
    visual._tabla_lista.clear()
    visual._cache_foto.clear()
    llamadas = {"details": 0, "media": 0}

    def falso(metodo, url, **kw):
        req = httpx.Request(metodo, url)
        if url.endswith("/media"):
            llamadas["media"] += 1
            return httpx.Response(200, json={"photoUri": f"https://foto/{url.split('/')[-2]}"}, request=req)
        llamadas["details"] += 1
        return httpx.Response(200, json={"photos": [
            {"name": f"places/ChIJabcdefghij/photos/{i}", "authorAttributions": [{"displayName": f"autor{i}"}]} for i in range(4)]},
            request=req)
    monkeypatch.setattr(visual.net, "request", falso)
    return llamadas


def test_details_se_paga_una_sola_vez_aunque_se_reinicie_la_memoria(google):
    pid = "ChIJabcdefghij"
    assert len(visual.fotos(pid, 1)) == 1
    assert google["details"] == 1
    visual._cache_foto.clear()                    # reinicio del servidor o 6 h después
    assert len(visual.fotos(pid, 3)) == 3         # «ver más fotos»
    assert google["details"] == 1                 # sin otro Place Details
    assert google["media"] == 4                   # 1 + 3, solo de las que se pidieron


def test_nombres_viejos_se_renuevan(google, monkeypatch):
    pid = "ChIJabcdefghij"
    visual.fotos(pid, 1)
    visual._cache_foto.clear()
    original = visual._media
    monkeypatch.setattr(visual, "_media", lambda *a, **k: None)   # Google ya no reconoce los nombres guardados
    assert visual.fotos(pid, 1) == []
    monkeypatch.setattr(visual, "_media", original)
    visual._cache_foto.clear()
    assert len(visual.fotos(pid, 1)) == 1 and google["details"] >= 2   # se pidieron de nuevo


def test_tarjetas_traen_una_foto_por_defecto(google):
    t = visual.tarjetas([{"name": "X", "category": "restaurante", "place_id": "ChIJabcdefghij"}])
    assert len(t[0]["fotos"]) == 1
