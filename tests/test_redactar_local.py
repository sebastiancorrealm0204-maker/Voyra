"""Validación de citas de redactar_local.py: el modelo solo puede escribir lo que la evidencia dice textualmente."""
import json

import redactar_local as r

E = {"name": "Café X", "category": "cafe", "zona": "Chapinero",
     "hechos": {"rating": 4.7, "resenas": 100, "resumen_google": "Café de especialidad con baristas campeones nacionales.",
                "practico": "Precio medio. horario según Google: lun–dom 9:00–18:00."},
     "fragmentos": [{"dominio": "tiktok.com", "propio": False,
                     "texto": "Pide recomendaciones al barista: ellos pueden orientar sobre orígenes y métodos de extracción."}]}


def _raw(**kw):
    base = {"descripcion": "Café de especialidad con baristas campeones nacionales, en Chapinero.",
            "descripcion_cita": "Café de especialidad con baristas campeones nacionales",
            "local": {"clave": {"texto": "Café de especialidad con baristas campeones.", "cita": "baristas campeones nacionales"},
                      "pedir": {"texto": "Pide recomendaciones al barista.", "cita": "Pide recomendaciones al barista"}}}
    base.update(kw)
    return json.dumps(base)


def test_acepta_solo_campos_con_cita_literal():
    out = r.validar(_raw(), E)
    assert out["local"]["pedir"] == "Pide recomendaciones al barista." and "clave" in out["local"]
    assert out["descripcion"].startswith("Café de especialidad")


def test_descarta_campos_con_cita_inventada_o_corta():
    raw = _raw(local={"clave": {"texto": "Café de especialidad con baristas campeones.", "cita": "baristas campeones nacionales"},
                      "pedir": {"texto": "El flat white de la casa.", "cita": "el flat white es famoso en toda la ciudad"},   # inventada
                      "dato": {"texto": "Tienen terraza.", "cita": "terraza"}})                                               # demasiado corta
    out = r.validar(raw, E)
    assert set(out["local"]) == {"clave"}


def test_sin_clave_respaldada_no_hay_ficha():
    raw = _raw(local={"clave": {"texto": "El mejor café.", "cita": "el mejor café de toda Colombia, sin duda"}})
    assert r.validar(raw, E) is None
    assert r.validar("esto no es json", E) is None


def test_descripcion_sin_cita_usa_la_clave():
    out = r.validar(_raw(descripcion_cita="algo que no está en la evidencia para nada"), E)
    assert out["descripcion"] == out["local"]["clave"]


def test_practico_viene_de_google_y_tiene_respaldo_si_falta():
    p = {"name": "Café X"}
    r.aplicar(p, {"descripcion": "d" * 30, "local": {"clave": "c"}}, r.practico(E))
    assert p["local"]["practico"].startswith("Precio medio. horario según Google")
    r.aplicar(p, {"descripcion": "d" * 30, "local": {"clave": "c"}}, "")
    assert "confírmalo" in p["local"]["practico"]


def test_limpiar_quita_precios_ojos_que_no_avisan_trivialidades_y_repeticiones():
    local = {"clave": "Rooftop con cócteles y vistas de la ciudad en Chicó.",
             "pedir": "Cócteles de la casa y platos para compartir.",
             "ver": "Los cócteles de la casa y los platos para compartir.",             # repite a `pedir`
             "ojo": "El servicio es amable y atento con los clientes.",                 # elogio, no advertencia
             "dato": "En su cuenta de Instagram publican fotos del lugar.",             # trivial
             "momento": "El presupuesto ronda los $120.000 por persona.",               # precio
             }
    assert r.limpiar(local) == {"clave": local["clave"], "pedir": local["pedir"]}
    assert r.limpiar({"clave": "Cocina de mar en un salón con terraza.", "ojo": "Llega temprano: se llena los fines de semana."})["ojo"]


def test_limpiar_quita_texto_del_modelo_sobre_la_evidencia_y_repeticiones_parciales():
    local = {"clave": "Cocina colombiana con el mondongo como plato insignia.",
             "pedir": "Pide lo que quieras; la evidencia no menciona platos específicos.",        # el modelo hablando de sí mismo
             "ver": "Los sabores de la cocina colombiana en el plato."}                            # repite la clave
    assert r.limpiar(local) == {"clave": local["clave"]}
