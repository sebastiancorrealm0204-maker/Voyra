"""Capa para extranjeros: preguntar / frase / extranjero. La regla del producto: mejor un campo vacío que uno falso."""
import json

import enriquecer_extranjeros as E
import redactar_local as W
import reunir_local as R
from app import context

EVIDENCIA = {
    "name": "Casa Ajiaco", "category": "restaurante", "zona": "La Candelaria", "descripcion": "Sopa típica.",
    "hechos": {"rating": 4.6, "resenas": 900, "resumen_google": "Restaurante tradicional de comida bogotana."},
    "fragmentos": [{"dominio": "guia.com", "propio": False,
                    "texto": "Pregunta por el ajiaco con alcaparras y crema en la mesa del patio, es lo que piden los locales."}],
    "local_actual": {"clave": "El ajiaco de siempre.", "pedir": "El ajiaco santafereño con pollo y mazorca."},
}


# ── hechos de Google ──
def test_los_hechos_de_pago_y_reserva_salen_solo_de_lo_que_google_confirma():
    h = R.extranjero_hechos({"paymentOptions": {"acceptsCreditCards": True, "acceptsDebitCards": True}, "reservable": True,
                             "servesVegetarianFood": True, "internationalPhoneNumber": "+57 1 234 5678",
                             "reviews": [{"originalText": {"languageCode": "en"}}, {"originalText": {"languageCode": "en-US"}},
                                         {"originalText": {"languageCode": "es"}}]})
    assert h["tarjeta"] == "credito_y_debito" and h["resenas_en_ingles"] == 2 and h["resenas_muestra"] == 3
    t = R.extranjero_texto(h)
    assert "Aceptan tarjeta de crédito y débito." in t and "Se puede reservar." in t and "vegetarianas" in t and "+57 1 234 5678" in t
    assert "reseñas de viajeros en inglés (2 de 3" in t


def test_un_false_de_google_no_se_afirma():
    # Google pone False también cuando simplemente no sabe: «no hay opciones vegetarianas» sin certeza sería peor que callar.
    t = R.extranjero_texto(R.extranjero_hechos({"reservable": False, "servesVegetarianFood": False}))
    assert t == ""
    assert R.extranjero_texto(R.extranjero_hechos({"paymentOptions": {"acceptsCashOnly": True}})) == "Solo efectivo: lleva pesos."
    assert R.extranjero_hechos({})["tarjeta"] is None


def test_pocas_resenas_en_ingles_no_cuentan_como_senal():
    t = R.extranjero_texto({"resenas_en_ingles": 1, "resenas_muestra": 5})
    assert "inglés" not in t


# ── frase ──
def test_la_frase_debe_hablar_de_lo_que_ya_esta_respaldado():
    local = {"pedir": "El ajiaco santafereño con pollo y mazorca."}
    assert W.frase_valida({"texto": "Quisiera un ajiaco, por favor.", "sobre": "pedir"}, local) == "Quisiera un ajiaco, por favor."
    assert W.frase_valida({"texto": "¿Dónde queda el baño?", "sobre": "pedir"}, local) == ""             # no habla del plato
    assert W.frase_valida({"texto": "Un ajiaco por 25.000 pesos, gracias.", "sobre": "pedir"}, local) == ""  # trae precio
    assert W.frase_valida({"texto": "Un ajiaco, por favor.", "sobre": "preguntar"}, local) == ""             # campo que no existe
    assert W.frase_valida({"texto": "Ajiaco", "sobre": "pedir"}, local) == ""                                # muy corta
    assert W.frase_valida({"texto": " ".join(["ajiaco"] * 20), "sobre": "pedir"}, local) == ""              # muy larga
    assert W.frase_valida(None, local) == ""


def test_validar_ficha_nueva_acepta_preguntar_con_cita_y_descarta_la_inventada():
    cita = "Pregunta por el ajiaco con alcaparras y crema en la mesa del patio"
    raw = json.dumps({"descripcion": "Sopa típica bogotana.", "descripcion_cita": "Restaurante tradicional de comida bogotana",
                      "local": {"clave": {"texto": "Restaurante tradicional de comida bogotana en La Candelaria.",
                                          "cita": "Restaurante tradicional de comida bogotana"},
                                "pedir": {"texto": "El ajiaco con alcaparras y crema.", "cita": "ajiaco con alcaparras y crema"},
                                "preguntar": {"texto": "Pregunta por la mesa del patio para tu ajiaco.", "cita": cita}},
                      "frase": {"texto": "Quisiera el ajiaco con alcaparras y crema, por favor.", "sobre": "pedir"}})
    r = W.validar(raw, EVIDENCIA)
    assert r and r["local"]["preguntar"] and r["local"]["frase"].startswith("Quisiera el ajiaco")
    inventado = json.loads(raw)
    inventado["local"]["preguntar"]["cita"] = "Pide el cóctel secreto de la casa que no sale en la carta"   # no está en la evidencia
    r2 = W.validar(json.dumps(inventado), EVIDENCIA)
    assert r2 and "preguntar" not in r2["local"]


# ── lugares ya curados ──
def test_existente_preguntar_con_cita_y_frase_sobre_el_pedir_curado():
    raw = json.dumps({"preguntar": {"texto": "Pregunta por la mesa del patio, donde piden los locales.",
                                    "cita": "Pregunta por el ajiaco con alcaparras y crema en la mesa del patio"},
                      "frase": {"texto": "¿Me trae el ajiaco santafereño, por favor?", "sobre": "pedir"}})
    r = E.validar_existente(raw, EVIDENCIA)
    assert "mesa del patio" in r["preguntar"] and r["frase"] == "¿Me trae el ajiaco santafereño, por favor?"


def test_existente_descarta_lo_sin_cita_o_repetido():
    sin_cita = json.dumps({"preguntar": {"texto": "Pregunta por el postre secreto de la casa.", "cita": "el postre secreto de la casa está escondido"}})
    assert E.validar_existente(sin_cita, EVIDENCIA) == {}
    repetido = json.dumps({"preguntar": {"texto": "El ajiaco santafereño con pollo y mazorca.",
                                         "cita": "Pregunta por el ajiaco con alcaparras y crema en la mesa del patio"}})
    assert "preguntar" not in E.validar_existente(repetido, EVIDENCIA)          # ya lo dice `pedir`
    assert E.validar_existente("no es json", EVIDENCIA) == {}


def test_el_overlay_suma_campos_sin_borrar_lo_anterior():
    overlay = {"Casa Ajiaco": {"extranjero": "Aceptan tarjeta de crédito."}}
    overlay, n = E.aplicar_overlay({"Casa Ajiaco": {"preguntar": "Pregunta por la mesa del patio.", "frase": "", "pedir_actual": "x", "name": "Casa Ajiaco"},
                                    "Vacío": {"frase": ""}}, overlay)
    assert n == 1 and overlay["Casa Ajiaco"] == {"extranjero": "Aceptan tarjeta de crédito.", "preguntar": "Pregunta por la mesa del patio."}
    assert "Vacío" not in overlay


def test_seleccion_de_existentes_y_candidatos():
    seeds = [{"name": "A", "city_display": "Bogotá", "category": "restaurante", "rating": 4.9, "place_id": "a", "local": {"pedir": "x"}},
             {"name": "B", "city_display": "Bogotá", "category": "restaurante", "rating": 4.8, "place_id": "b", "local": {"pedir": "x"}},
             {"name": "C", "city_display": "Bogotá", "category": "atraccion", "rating": 5.0, "place_id": "c", "local": {"pedir": "x"}},
             {"name": "D", "city_display": "Bogotá", "category": "cafe", "rating": 5.0, "place_id": "d", "local": {}}]
    assert [p["name"] for p in E.elegir_existentes(seeds, {"A"}, 5)] == ["B"]     # sin A (ya tiene la capa), sin atracción, sin `pedir`
    cands = [{"name": "Nuevo Uno", "category": "bar", "rating": 4.7, "place_id": "n1"},
             {"name": "Nuevo Dos", "category": "restaurante", "rating": 4.9, "place_id": "n2"},
             {"name": "A", "category": "restaurante", "rating": 5.0, "place_id": "zz"}]
    assert [c["name"] for c in E.elegir_candidatos(cands, {W._n("A")}, set(), 5)] == ["Nuevo Dos", "Nuevo Uno"]


# ── el Companion los ve ──
def test_el_prompt_muestra_la_capa_para_extranjeros():
    s = context._local_suffix({"clave": "Ajiaco clásico.", "pedir": "El ajiaco.", "preguntar": "Pregunta por la mesa del patio.",
                               "frase": "Quisiera un ajiaco, por favor.", "practico": "Gama media.",
                               "extranjero": "Aceptan tarjeta de crédito y débito."})
    assert "PREGUNTA POR: Pregunta por la mesa del patio." in s
    assert "CÓMO DECIRLO EN ESPAÑOL: Quisiera un ajiaco, por favor." in s
    assert "PARA VIAJEROS EXTRANJEROS: Aceptan tarjeta de crédito y débito." in s
    assert "VIAJERO EXTRANJERO" in context._EXTRANJERO_BLOCK


def test_preguntar_debe_ser_algo_especifico_no_informacion_general():
    assert W.limpiar({"clave": "Italiano de lujo en la Zona T.", "preguntar": "Pregunta por las sedes en Bogotá; hay varias."}).get("preguntar") is None
    assert W.limpiar({"clave": "Italiano de lujo en la Zona T.", "preguntar": "Pregunta por el horario de la cocina los domingos."}).get("preguntar") is None
    assert W.limpiar({"clave": "Italiano de lujo en la Zona T.", "preguntar": "Pregunta si el pato está disponible ese día."}).get("preguntar")


def test_el_filtro_de_precios_ahora_reconoce_pesos_cop_y_usd():
    # Antes las expresiones tenían caracteres de control en lugar de \b y estas palabras nunca se atrapaban.
    for txt in ("Cuesta 25 pesos", "Sale en COP aproximado", "unos 10 usd por persona", "Plato de 25.000"):
        assert W._PRECIO_HORA.search(txt), txt
    assert W._ADVERTENCIA.search("Ojo con la fila en la entrada") and W._ADVERTENCIA.search("Hay cola los sábados")
