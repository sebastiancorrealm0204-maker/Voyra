"""Lógica pura de descubrir_joyas.py (sin llamar a Tavily, al modelo ni a Google)."""
import json

import descubrir_joyas as j

TEXTOS = [
    {"url": "https://www.tiktok.com/@a/video/1", "dominio": "tiktok.com", "titulo": "Restaurantes ocultos",
     "contenido": "1. Real Gallina Criolla 2. Arakataka dentro del Museo del Oro"},
    {"url": "https://www.reddit.com/r/Bogota/x", "dominio": "reddit.com", "titulo": "Secretos",
     "contenido": "Arakataka es una joya, cierra los lunes. Starbucks no."},
]


def test_plan_intercala_fuentes_y_respeta_el_tope():
    plan = j.plan_busquedas(["Usaquén"], ["social", "reddit", "web"], 4)
    assert len(plan) == 4
    assert [f for _, f in plan] == ["social", "reddit", "web", "social"]
    assert j.plan_busquedas(["Usaquén"], ["social"], 100)[-1][0] == "lugares escondidos en Usaquén Bogotá"


def test_parsea_extraccion_y_descarta_ruido():
    raw = "```json\n" + json.dumps({"lugares": [
        {"nombre": "Arakataka", "tipo": "restaurante", "barrio": "Candelaria", "detalle": "Dentro del Museo del Oro", "fuentes": [0, 1]},
        {"nombre": "Starbucks", "tipo": "cafe", "fuentes": [1]},                    # cadena
        {"nombre": "Sin fuente", "tipo": "bar", "fuentes": []},                     # sin respaldo
        {"nombre": "Fuera", "tipo": "bar", "fuentes": [9]},                         # índice inexistente
        {"nombre": "Raro", "tipo": "inventado", "fuentes": [0]},                    # tipo desconocido → vacío
    ]}) + "\n```"
    out = j.parsear_extraccion(raw, TEXTOS)
    assert [p["nombre"] for p in out] == ["Arakataka", "Raro"]
    assert len(out[0]["fuentes"]) == 2 and out[1]["tipo"] == ""
    assert j.parsear_extraccion("no es json", TEXTOS) == []


def test_agrupa_el_mismo_lugar_entre_textos():
    a = {"nombre": "Arakataka", "tipo": "", "barrio": "", "detalle": "uno", "fuentes": [TEXTOS[0] | {"texto": "x"}]}
    b = {"nombre": "ARAKATAKA", "tipo": "restaurante", "barrio": "Candelaria", "detalle": "dos", "fuentes": [TEXTOS[1] | {"texto": "y"}]}
    g = j.agrupar([a, b])
    assert len(g) == 1
    uno = next(iter(g.values()))
    assert uno["tipo"] == "restaurante" and uno["barrio"] == "Candelaria" and uno["detalles"] == ["uno", "dos"]
    assert len(uno["fuentes"]) == 2


def test_joya_exige_pocas_resenas_buen_rating_y_operativo():
    base = {"estado": "OPERATIONAL", "rating": 4.6, "resenas": 120}
    assert j.es_joya(base)
    assert not j.es_joya({**base, "resenas": 5000})        # ya es popular
    assert not j.es_joya({**base, "resenas": 3})           # ficha poco fiable
    assert not j.es_joya({**base, "rating": 4.0})
    assert not j.es_joya({**base, "estado": "CLOSED_PERMANENTLY"})


def test_clasifica_por_fuentes_y_cierre():
    g = {"rating": 4.7, "resenas": 100}
    assert j.clasificar({"menciones": 2, "cierre": False}, {"rating": 4.4, "resenas": 500}) == "recomendado"
    assert j.clasificar({"menciones": 1, "cierre": False}, g) == "recomendado"          # 1 fuente pero rating alto y pocas reseñas
    assert j.clasificar({"menciones": 1, "cierre": False}, {"rating": 4.4, "resenas": 500}) == "dudoso"
    assert j.clasificar({"menciones": 3, "cierre": True}, g) == "descartado_cierre"


def test_entrada_compatible_con_aprobar_candidatos():
    import aprobar_candidatos as ap
    grupo = {"nombre": "Arakataka", "tipo": "restaurante", "barrio": "", "detalles": ["Dentro del Museo del Oro"],
             "fuentes": [{"dominio": "tiktok.com", "url": "u1", "titulo": "t", "texto": "x"},
                         {"dominio": "reddit.com", "url": "u2", "titulo": "t", "texto": "y"}]}
    g = {"place_id": "PID", "nombre": "Arakataka", "direccion": "Cra 6", "lat": 4.5981, "lng": -74.0758, "rating": 4.6,
         "resenas": 120, "primary": "restaurant", "tipos": ["restaurant"], "estado": "OPERATIONAL"}
    corro = j.evidencia(grupo)
    e = j.a_entrada_joya(g, grupo, corro, j.clasificar(corro, g))
    assert e["tags"] == ["Joyas escondidas"] and e["category"] == "restaurante" and e["zona"] == "La Candelaria"
    assert e["_evidencia"]["estado"] == "recomendado" and e["descripcion"].startswith("Según redes")
    seed = ap.a_seed(e)                                      # no lanza: tiene descripcion y no hay cierre
    assert seed["name"] == "Arakataka" and seed["tags"] == ["Joyas escondidas"]
    assert "Arakataka" in j.resumen_md([e])


def test_no_asocia_nombres_distintos_que_comparten_raiz():
    assert not j.nombre_coincide("Éntrenos", "Evoluciona Entrenamientos")
    assert j.nombre_coincide("Baw Thai Spa", "Baw Thai Spa - El Nogal")
    assert j.nombre_coincide("Real Gallina Criolla", "La Real Gallina Criolla")


def test_descarta_textos_de_otra_ciudad():
    assert j.texto_de_otra_ciudad("Bares o restaurantes raros en Guadalajara", "a dos cuadras de la calle Vallarta",
                                  "https://www.reddit.com/r/Guadalajara/comments/1/")
    assert not j.texto_de_otra_ciudad("Restaurantes ocultos en Bogotá", "también en Medellín")
    assert not j.texto_de_otra_ciudad("Lugares secretos", "con clima agradable en La Candelaria")   # «clima» ≠ «lima»
