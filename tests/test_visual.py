"""Tarjetas visuales de lugares curados."""
from app import visual

LUGARES = [
    {"name": "El Chato", "category": "restaurante", "zona": "Chapinero Alto", "rating": 4.4, "lat": 4.6, "lng": -74.0, "place_id": "ChIJQclc-kSaP44RJgBygHivOB4",
     "local": {"clave": "Es el #1 de Latinoamérica.", "pedir": "El menú degustación."}},
    {"name": "Museo del Oro", "category": "atraccion", "zona": "Centro", "rating": 4.8, "lat": 4.6, "lng": -74.07},
    {"name": "Crepes & Waffles (Parque 93)", "category": "restaurante", "zona": "Parque 93", "lat": 4.67, "lng": -74.05},
    {"name": "TransMilenio — Estación Museo del Oro", "category": "transporte", "zona": "Centro", "lat": 4.6, "lng": -74.07},
    {"name": "Cruz Verde (Calle 127 / Norte)", "category": "clinica", "zona": "Norte", "lat": 4.7, "lng": -74.05},
]


def test_mencionados_en_orden_y_solo_categorias_visuales():
    r = visual.mencionados("Ve al Museo del Oro y luego cena en El Chato; Crepes & Waffles es plan B. TransMilenio Estación Museo del Oro.", LUGARES)
    assert [p["name"] for p in r] == ["Museo del Oro", "El Chato", "Crepes & Waffles (Parque 93)"]


def test_mencionados_respeta_el_maximo_y_no_inventa():
    assert visual.mencionados("hola, ¿qué tal?", LUGARES) == []
    assert len(visual.mencionados("El Chato, Museo del Oro y Crepes & Waffles", LUGARES, maximo=2)) == 2


def test_tarjetas_sin_google_salen_sin_foto(monkeypatch):
    monkeypatch.setattr(visual.places_live, "API_KEY", "")
    c = visual.tarjetas([LUGARES[0]], "es")
    assert c[0]["name"] == "El Chato" and c[0]["foto"] is None and c[0]["pedir"] == "El menú degustación." and c[0]["rating"] == 4.4


def test_tarjetas_traducen_el_dato_local(monkeypatch):
    monkeypatch.setattr(visual.places_live, "API_KEY", "")
    monkeypatch.setattr(visual.traducir, "traducir", lambda textos, lang: [f"[{lang}] {x}" for x in textos])
    c = visual.tarjetas([LUGARES[0]], "en")
    assert c[0]["clave"].startswith("[en] ") and c[0]["pedir"].startswith("[en] ")
    assert c[0]["name"] == "El Chato"  # el nombre del lugar no se traduce


def test_foto_con_google_caido_devuelve_none(monkeypatch):
    monkeypatch.setattr(visual.places_live, "API_KEY", "x")
    visual._cache_foto = visual.net.TTLCache(ttl_s=60, maxsize=8)

    def roto(*a, **k):
        raise RuntimeError("red")

    monkeypatch.setattr(visual.net, "request", roto)
    assert visual.foto("ChIJQclc-kSaP44RJgBygHivOB4") is None
    assert visual.foto("no valido!") is None
