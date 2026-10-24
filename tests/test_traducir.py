"""Traducción de contenido dinámico + idioma único (cabecera X-UI-Lang) + destino libre del chat."""
import pytest

from app import traducir, transporte as t


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    traducir._mem.clear()
    monkeypatch.setattr(traducir, "_de_bd", lambda claves: {})
    monkeypatch.setattr(traducir, "_a_bd", lambda pares: None)


def test_es_y_idiomas_raros_no_se_tocan():
    assert traducir.traducir(["Hola"], "es") == ["Hola"]
    assert traducir.traducir(["Hola"], "xx") == ["Hola"]
    assert traducir.traducir([], "en") == []


def test_sin_llm_devuelve_el_original(monkeypatch):
    monkeypatch.setattr(traducir, "_llamar_llm", lambda textos, lang: None)
    assert traducir.traducir(["Paro en la Norte"], "en") == ["Paro en la Norte"]


def test_traduce_y_cachea(monkeypatch):
    llamadas = []

    def falso(textos, lang):
        llamadas.append(list(textos))
        return [f"[{lang}] {x}" for x in textos]

    monkeypatch.setattr(traducir, "_llamar_llm", falso)
    assert traducir.traducir(["Hola", "Adiós", "Hola"], "en") == ["[en] Hola", "[en] Adiós", "[en] Hola"]
    assert len(llamadas) == 1 and sorted(llamadas[0]) == ["Adiós", "Hola"]  # sin duplicados, un solo lote
    traducir.traducir(["Hola"], "en")
    assert len(llamadas) == 1  # segunda vez: de la caché
    traducir.traducir(["Hola"], "fr")
    assert len(llamadas) == 2  # otro idioma: otra traducción


def test_profundo_traduce_texto_y_respeta_ids_y_urls(monkeypatch):
    monkeypatch.setattr(traducir, "_llamar_llm", lambda textos, lang: [f"<{x}>" for x in textos])
    obj = {"titulo": "Control migratorio", "url": "https://x.co/a", "icono": "passport", "estimado_min": [20, 45],
           "tips": ["Lleva tu pasaporte", "Ten paciencia"], "name": "Aeropuerto El Dorado"}
    r = traducir.profundo(obj, "en")
    assert r["titulo"] == "<Control migratorio>"
    assert r["tips"] == ["<Lleva tu pasaporte>", "<Ten paciencia>"]
    assert r["url"] == "https://x.co/a" and r["icono"] == "passport" and r["name"] == "Aeropuerto El Dorado"
    assert r["estimado_min"] == [20, 45]
    assert obj["titulo"] == "Control migratorio"  # no muta el original


def test_profundo_en_espanol_devuelve_lo_mismo():
    obj = {"titulo": "x y z"}
    assert traducir.profundo(obj, "es") is obj


def test_ui_lang_solo_acepta_idiomas_conocidos():
    traducir.fijar_ui_lang("EN-us")
    assert traducir.ui_lang() == "en"
    traducir.fijar_ui_lang("zz")
    assert traducir.ui_lang() == ""
    traducir.fijar_ui_lang(None)
    assert traducir.ui_lang() == ""


@pytest.mark.parametrize("msg,esperado", [
    ("quiero ir a la Universidad Javeriana desde la Zona T", "Universidad Javeriana"),
    ("how do I get to Parque Simón Bolívar?", "Parque Simón Bolívar"),
    ("¿cómo llego al Centro Andino en taxi?", "Centro Andino"),
    ("cómo llego a mi casa", None),
    ("hola, ¿qué tal?", None),
    ("quiero ir allá", None),
])
def test_destino_libre(msg, esperado):
    assert t.destino_libre(msg) == esperado
