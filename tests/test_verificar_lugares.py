"""Lógica de verificar_lugares.py (sin llamar a Google): qué se considera un hallazgo."""
import verificar_lugares as v

SEED = {"name": "El Chato", "zona": "Chapinero Alto", "lat": 4.6402, "lng": -74.0625}


def google(**kw):
    base = {"place_id": "X", "nombre": "El Chato", "lat": 4.6403, "lng": -74.0626, "estado": "OPERATIONAL",
            "rating": 4.5, "horarios": ["lunes: 12:00–22:00"]}
    base.update(kw)
    return base


def codigos(hs):
    return [h["codigo"] for h in hs]


def test_lugar_sano_no_tiene_hallazgos():
    assert v.evaluar(SEED, google()) == []


def test_no_encontrado_es_grave():
    hs = v.evaluar(SEED, None)
    assert codigos(hs) == ["NO_ENCONTRADO"] and hs[0]["nivel"] == "alto"


def test_cerrado_permanente_es_grave_y_temporal_es_medio():
    assert v.evaluar(SEED, google(estado="CLOSED_PERMANENTLY"))[0] == {
        "nivel": "alto", "codigo": "CERRADO_PERMANENTE",
        "detalle": "Google lo marca como cerrado permanentemente: sacarlo de la curación."}
    assert v.evaluar(SEED, google(estado="CLOSED_TEMPORARILY"))[0]["nivel"] == "medio"


def test_nombre_distinto_se_marca_pero_variantes_no():
    assert "NOMBRE_DISTINTO" in codigos(v.evaluar(SEED, google(nombre="Bogotá Beer Company")))
    # tildes, mayúsculas y un sufijo no cuentan como otro lugar
    assert v.evaluar(SEED, google(nombre="EL CHATO Restaurante")) == []
    assert v.evaluar({**SEED, "name": "Criterión"}, google(nombre="Restaurante Criterion")) == []


def test_coordenadas_lejanas():
    hs = v.evaluar(SEED, google(lat=4.6502, lng=-74.0625))  # ~1,1 km
    assert "COORDENADAS" in codigos(hs)
    assert "COORDENADAS" not in codigos(v.evaluar(SEED, google(lat=4.6410)))  # ~90 m


def test_rating_cae_o_es_bajo():
    assert "RATING_BAJO" in codigos(v.evaluar(SEED, google(rating=4.0), previo={"rating": 4.6}))
    assert "RATING_BAJO" in codigos(v.evaluar(SEED, google(rating=3.8)))
    assert v.evaluar(SEED, google(rating=4.4), previo={"rating": 4.6}) == []


def test_sin_horario_es_leve_y_van_ordenados_por_gravedad():
    hs = v.evaluar(SEED, google(horarios=[], estado="CLOSED_PERMANENTLY"))
    assert codigos(hs) == ["CERRADO_PERMANENTE", "SIN_HORARIO"]


def test_resumen_pone_lo_grave_primero():
    res = [
        {"name": "Sano", "zona": "", "hallazgos": []},
        {"name": "Leve", "zona": "Z", "hallazgos": [{"nivel": "bajo", "codigo": "SIN_HORARIO", "detalle": "d"}]},
        {"name": "Cerrado", "zona": "Z", "hallazgos": [{"nivel": "alto", "codigo": "CERRADO_PERMANENTE", "detalle": "d"}]},
    ]
    md = v.resumen_md("Bogotá", res)
    assert md.index("## Cerrado") < md.index("## Leve")
    assert "Sin hallazgos (1)" in md and "Sano" in md
    assert "Sano" not in v.resumen_md("Bogotá", res, solo_problemas=True)


def test_normalizar_respuesta_de_google():
    n = v.normalizar({"id": "abc", "displayName": {"text": "El Chato"}, "location": {"latitude": 1.0, "longitude": 2.0},
                      "businessStatus": "OPERATIONAL", "rating": 4.4,
                      "regularOpeningHours": {"weekdayDescriptions": ["lunes: cerrado"]}})
    assert n["place_id"] == "abc" and n["nombre"] == "El Chato" and n["lat"] == 1.0
    assert n["horarios"] == ["lunes: cerrado"] and n["estado"] == "OPERATIONAL"
