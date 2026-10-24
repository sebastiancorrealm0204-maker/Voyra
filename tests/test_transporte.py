"""Cómo llegar: lógica pura (tráfico, precio, recomendación, parseo de Google) y el endpoint."""
import datetime

import pytest
from fastapi.testclient import TestClient

from app import transporte as t

TZ = t.TZ


def test_nivel_de_trancon_por_cociente():
    assert t.nivel_trancon(1000, 1000)["nivel"] == "fluido"
    assert t.nivel_trancon(1300, 1000)["nivel"] == "moderado"
    assert t.nivel_trancon(1600, 1000)["nivel"] == "pesado"
    r = t.nivel_trancon(3480, 1440)  # 58 min vs 24 min
    assert r["nivel"] == "trancon" and r["extra_min"] == 34
    assert t.nivel_trancon(500, 0)["nivel"] == "fluido"  # sin dato de referencia no acusamos


def test_en_trayectos_cortos_la_proporcion_no_exagera():
    # 15 → 22 min: proporción 1,47 pero solo +7 min → moderado, no «pesado»
    assert t.nivel_trancon(22 * 60, 15 * 60)["nivel"] == "moderado"
    # 20 → 30 min (+10): ahora sí pesado
    assert t.nivel_trancon(30 * 60, 20 * 60)["nivel"] == "pesado"
    # 5 → 6,5 min (+2): fluido
    assert t.nivel_trancon(390, 300)["nivel"] == "fluido"


def test_precio_taxi_tiene_minimo_y_rango_redondeado():
    corto = t.precio_taxi(0.5, 3)
    assert corto["min"] >= 7000 and corto["max"] > corto["min"] and corto["moneda"] == "COP"
    largo = t.precio_taxi(17, 30)
    assert 30000 <= largo["min"] < largo["max"] <= 60000
    assert largo["min"] % 1000 == 0 and largo["max"] % 1000 == 0


def test_hora_pico_solo_en_dias_habiles():
    lunes_7 = datetime.datetime(2026, 10, 26, 7, 0, tzinfo=TZ)
    lunes_12 = datetime.datetime(2026, 10, 26, 12, 0, tzinfo=TZ)
    lunes_17 = datetime.datetime(2026, 10, 26, 17, 30, tzinfo=TZ)
    sabado_7 = datetime.datetime(2026, 10, 24, 7, 0, tzinfo=TZ)
    assert t.hora_pico(lunes_7) and t.hora_pico(lunes_17)
    assert not t.hora_pico(lunes_12) and not t.hora_pico(sabado_7)


@pytest.mark.parametrize("op,nivel,esperado", [
    ({"caminar": {"min": 10}, "taxi": {"min": 5}, "transporte": {"min": 20}}, "fluido", ("caminar", "cerca")),
    ({"taxi": {"min": 55}, "transporte": {"min": 60}}, "trancon", ("transporte", "trancon_gana_transporte")),
    ({"taxi": {"min": 25}, "transporte": {"min": 27}}, "fluido", ("transporte", "similar_y_mas_barato")),
    ({"taxi": {"min": 20}, "transporte": {"min": 45}}, "fluido", ("taxi", "taxi_mas_rapido")),
    ({"taxi": {"min": 20}}, "fluido", ("taxi", "solo_taxi")),
    ({"transporte": {"min": 40}}, "pesado", ("transporte", "solo_transporte")),
    ({}, "fluido", (None, "sin_opciones")),
])
def test_recomendacion(op, nivel, esperado):
    r = t.recomendar(op, nivel)
    assert (r["modo"], r["motivo"]) == esperado


RUTA_TRANSITO = {
    "duration": "2400s",
    "legs": [{"steps": [
        {"travelMode": "WALK", "staticDuration": "180s", "distanceMeters": 250},
        {"travelMode": "WALK", "staticDuration": "120s", "distanceMeters": 130},   # Google parte la caminata en pasos de esquina
        {"travelMode": "TRANSIT", "staticDuration": "1500s", "transitDetails": {
            "stopCount": 9, "stopDetails": {"departureStop": {"name": "Portal El Dorado"}, "arrivalStop": {"name": "Museo del Oro"}},
            "transitLine": {"nameShort": "M86", "color": "#ff0000", "agencies": [{"name": "Transmilenio-Dual"}],
                            "vehicle": {"type": "BUS", "name": {"text": "Autobús"}}}}},
        {"travelMode": "TRANSIT", "staticDuration": "420s", "transitDetails": {
            "stopCount": 3, "stopDetails": {"departureStop": {"name": "Museo del Oro"}, "arrivalStop": {"name": "Las Aguas"}},
            "transitLine": {"name": "B27", "agencies": [{"name": "Zonal-Urbano"}], "vehicle": {"type": "BUS", "name": {"text": "Autobús"}}}}},
        {"travelMode": "WALK", "staticDuration": "100s", "distanceMeters": 120},
        {"travelMode": "WALK", "staticDuration": "80s", "distanceMeters": 80},
    ]}],
}


def test_parseo_de_transito_de_google():
    r = t.parsear_transito(RUTA_TRANSITO)
    assert r["min"] == 40 and r["transbordos"] == 1 and r["caminata_min"] == 8  # 180+120+100+80 s
    tipos = [p["tipo"] for p in r["pasos"]]
    assert tipos == ["caminar", "transporte", "transporte", "caminar"]
    bus = r["pasos"][1]
    assert bus["linea"] == "M86" and bus["vehiculo"] == "TransMilenio" and bus["desde"] == "Portal El Dorado"
    assert bus["hasta"] == "Museo del Oro" and bus["paradas"] == 9
    assert r["pasos"][2]["vehiculo"] == "SITP"                      # Zonal-Urbano → SITP
    assert r["pasos"][0] == {"tipo": "caminar", "min": 5, "metros": 380}   # los dos pasos de caminata, unidos
    assert r["pasos"][3] == {"tipo": "caminar", "min": 3, "metros": 200}


def test_etiqueta_de_agencia():
    assert t.etiqueta_agencia(["Transmilenio-Dual"]) == "TransMilenio"
    assert t.etiqueta_agencia(["Zonal-Urbano"]) == "SITP"
    assert t.etiqueta_agencia(["Otra"], "Autobús") == "Autobús" and t.etiqueta_agencia([]) == ""


def test_buscar_lugar_ignora_tildes_y_mayusculas():
    lugares = [{"name": "Museo del Oro", "lat": 1, "lng": 2}, {"name": "Museo Botero", "lat": 3, "lng": 4}]
    assert t.buscar_lugar("museo del ORO", lugares)["name"] == "Museo del Oro"
    assert t.buscar_lugar("botero", lugares)["name"] == "Museo Botero"
    assert t.buscar_lugar("zzz", lugares) is None and t.buscar_lugar("", lugares) is None


ORIGEN = (4.7016, -74.1469, "Aeropuerto")
DESTINO = (4.5981, -74.0758, "La Candelaria")


@pytest.fixture(autouse=True)
def _sin_cache():
    t._cache = t.net.TTLCache(ttl_s=300, maxsize=16)


@pytest.fixture(autouse=True)
def _db_temporal(tmp_path):
    """Base de datos propia: sin esto, correr este archivo solo usaba voyra.db y chocaba con usuarios de otras corridas."""
    from app import auth, db, limits, seed_data
    db.DB_PATH = str(tmp_path / "transporte.db")
    db.init_db()
    auth.init_auth()
    limits.init_limits()
    db.seed_destination_places(seed_data.all_seeds())


def test_sin_routes_api_responde_en_modo_demo(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "")
    r = t.rutas(ORIGEN, DESTINO)
    assert r["fuente"] == "demo"
    assert {"taxi", "transporte"} <= set(r["opciones"]) and r["opciones"]["transporte"]["pasos"] == []  # no inventa líneas
    assert r["recomendacion"]["modo"] in ("taxi", "transporte")
    assert "uber" in r["enlaces"] and "maps_transporte" in r["enlaces"]
    assert r["origen"]["nombre"] == "Aeropuerto" and r["destino"]["nombre"] == "La Candelaria"


def test_con_routes_api_usa_trafico_real(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")

    def falso(modo, o, d, salida):
        return {"duration": "3480s", "staticDuration": "1440s", "distanceMeters": 17000} if modo == "DRIVE" else RUTA_TRANSITO

    monkeypatch.setattr(t, "_pedir_google", falso)
    r = t.rutas(ORIGEN, DESTINO)
    assert r["fuente"] == "google"
    assert r["opciones"]["taxi"]["min"] == 58 and r["opciones"]["taxi"]["min_sin_trafico"] == 24
    assert r["trancon"]["nivel"] == "trancon" and r["trancon"]["extra_min"] == 34
    assert r["opciones"]["transporte"]["min"] == 40
    assert r["recomendacion"] == {"modo": "transporte", "motivo": "trancon_gana_transporte"}


def test_si_google_falla_cae_al_modo_demo(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")

    def roto(*a, **k):
        raise t.RoutesNoDisponible("http_403")

    monkeypatch.setattr(t, "_pedir_google", roto)
    assert t.rutas(ORIGEN, DESTINO)["fuente"] == "demo"


def test_la_misma_consulta_se_sirve_de_cache(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")
    llamadas = []

    def falso(modo, o, d, salida):
        llamadas.append(modo)
        return {"duration": "600s", "staticDuration": "500s", "distanceMeters": 5000} if modo == "DRIVE" else None

    monkeypatch.setattr(t, "_pedir_google", falso)
    t.rutas(ORIGEN, DESTINO)
    n = len(llamadas)
    t.rutas(ORIGEN, DESTINO)
    assert len(llamadas) == n  # no vuelve a pegarle a Google


# ───────────── endpoint ─────────────
def _cliente():
    from app.main import app
    from tests.test_companion import _auth_headers
    c = TestClient(app)
    return c, _auth_headers(c)


def test_endpoint_destino_curado_aeropuerto_y_errores(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "")
    c, H = _cliente()
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "H"}, headers=H).json()["trip_id"]
    r = c.post(f"/trips/{tid}/transporte", json={"destino": "Museo del Oro", "salida_min": 0}, headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["fuente"] == "demo" and j["destino"]["nombre"] == "Museo del Oro" and j["opciones"]["taxi"]["min"] > 0
    r = c.post(f"/trips/{tid}/transporte", json={"destino": "aeropuerto", "origen_lat": 4.65, "origen_lng": -74.06}, headers=H)
    assert r.status_code == 200 and r.json()["destino"]["nombre"] == "Aeropuerto El Dorado"
    r = c.post(f"/trips/{tid}/transporte", json={"destino": "Lugar que no existe"}, headers=H)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "destino_no_encontrado"
    r = c.post(f"/trips/{tid}/transporte", json={"lat": 4.6, "lng": -74.07, "nombre": "Mi punto", "salida_min": 60}, headers=H)
    assert r.status_code == 200 and r.json()["salida_min"] == 60
    # otro usuario no puede usar el viaje
    c2, H2 = _cliente()
    assert c2.post(f"/trips/{tid}/transporte", json={"destino": "aeropuerto"}, headers=H2).status_code in (403, 404)


# ───────────── chat: detectar «cómo llego a…» ─────────────
LUGARES = [
    {"name": "Museo del Oro", "lat": 4.6019, "lng": -74.0719},
    {"name": "Cerro de Monserrate", "lat": 4.6057, "lng": -74.0560},
    {"name": "Plaza de Bolívar", "lat": 4.5981, "lng": -74.0760},
    {"name": "Parque de la 93", "lat": 4.6760, "lng": -74.0480},
    {"name": "Usaquén (centro histórico)", "lat": 4.6950, "lng": -74.0310},
]


@pytest.mark.parametrize("msg,esperado", [
    ("¿Cómo llego al Museo del Oro?", ("lugar", "Museo del Oro")),
    ("how do I get to Monserrate from here", ("lugar", "Cerro de Monserrate")),
    ("comment aller à la Plaza de Bolívar ?", ("lugar", "Plaza de Bolívar")),
    ("wie komme ich zum Parque de la 93", ("lugar", "Parque de la 93")),
    ("¿cuánto demora un taxi al aeropuerto?", ("aeropuerto", None)),
    ("how long to get to the airport", ("aeropuerto", None)),
    ("¿Cómo voy a mi hotel desde acá?", ("hotel", None)),
    ("cómo llego a Usaquén", ("lugar", "Usaquén (centro histórico)")),
])
def test_detecta_el_destino_en_varios_idiomas(msg, esperado):
    d = t.detectar_destino(msg, LUGARES)
    assert d is not None and d["tipo"] == esperado[0]
    if esperado[1]:
        assert d["nombre"] == esperado[1]


@pytest.mark.parametrize("msg", [
    "El Museo del Oro es muy bonito",            # nombra el lugar pero no pide ruta
    "¿Cómo llego a ser más feliz?",              # pide «cómo llego» sin destino conocido
    "Recomiéndame un café",
    "",
])
def test_no_detecta_falsos_positivos(msg):
    assert t.detectar_destino(msg, LUGARES) is None


def _ruta_real():
    return {
        "fuente": "google", "origen": {"nombre": "Zona T"}, "destino": {"nombre": "Museo del Oro"},
        "opciones": {"taxi": {"min": 40, "min_sin_trafico": 30, "km": 10.7, "precio": {"min": 25000, "max": 38000}},
                     "transporte": {"min": 44, "caminata_min": 9, "transbordos": 0, "pasos": [
                         {"tipo": "transporte", "linea": "L81", "vehiculo": "TransMilenio", "desde": "Pq. León de Greiff", "hasta": "San Victorino"}]}},
        "trancon": {"nivel": "pesado", "ratio": 1.6, "extra_min": 14}, "hora_pico": True,
        "recomendacion": {"modo": "transporte", "motivo": "trancon_gana_transporte"},
    }


def test_bloque_del_prompt_trae_solo_datos_reales():
    b = t.bloque_chat(_ruta_real())
    assert "RUTAS EN VIVO" in b and "«Zona T»" in b and "«Museo del Oro»" in b
    assert "40 min con tráfico (normalmente 30)" in b and "25.000–38.000 COP" in b and "ESTIMADO" in b
    assert "TransMilenio L81 (Pq. León de Greiff → San Victorino)" in b
    assert "hora pico" in b and "Conviene: transporte público" in b and "No inventes" in b


# ───────────── termómetro ─────────────
def test_termometro_sin_routes_api_no_inventa(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "")
    assert t.trafico() == {"disponible": False, "corredores": []}


def test_termometro_mide_los_corredores_y_se_cachea(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")
    t._cache_trafico = t.net.TTLCache(ttl_s=1200, maxsize=2)
    llamadas = []

    def falso(modo, o, d, salida):
        llamadas.append(modo)
        return {"duration": "2400s", "staticDuration": "1000s"}  # +23 min, ratio 2,4 → trancón

    monkeypatch.setattr(t, "_pedir_google", falso)
    r = t.trafico()
    assert r["disponible"] and len(r["corredores"]) == len(t.CORREDORES) and r["general"]["nivel"] == "trancon"
    assert {c["id"] for c in r["corredores"]} == {c["id"] for c in t.CORREDORES}
    n = len(llamadas)
    t.trafico()
    assert len(llamadas) == n  # una sola medición compartida por todos


def test_termometro_si_google_cae_responde_no_disponible(monkeypatch):
    monkeypatch.setattr(t, "API_KEY", "x")
    t._cache_trafico = t.net.TTLCache(ttl_s=1200, maxsize=2)

    def roto(*a, **k):
        raise t.RoutesNoDisponible("http_403")

    monkeypatch.setattr(t, "_pedir_google", roto)
    assert t.trafico()["disponible"] is False


# ───────────── el chat recibe la ruta real ─────────────
def test_el_chat_inyecta_la_ruta_real_en_el_prompt(monkeypatch):
    from app import llm
    monkeypatch.setattr(t, "API_KEY", "x")
    monkeypatch.setattr(t, "rutas", lambda o, d, s=0: {**_ruta_real(), "destino": {"nombre": d[2]}})
    capturado = {}

    def falso_chat(system, history):
        capturado["system"] = system
        return "ok"

    monkeypatch.setattr(llm, "chat", falso_chat)
    c, H = _cliente()
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "H"}, headers=H).json()["trip_id"]
    r = c.post(f"/trips/{tid}/chat", json={"message": "¿Cómo llego al Museo del Oro?"}, headers=H)
    assert r.status_code == 200
    assert "RUTAS EN VIVO" in capturado["system"] and "Museo del Oro" in capturado["system"]
    # sin pedir ruta, el prompt no la lleva
    c.post(f"/trips/{tid}/chat", json={"message": "recomiéndame un café"}, headers=H)
    assert "RUTAS EN VIVO" not in capturado["system"]


def test_el_chat_sigue_normal_sin_routes_api(monkeypatch):
    from app import llm
    monkeypatch.setattr(t, "API_KEY", "")
    capturado = {}
    monkeypatch.setattr(llm, "chat", lambda system, history: capturado.setdefault("system", system) and "ok")
    c, H = _cliente()
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "H"}, headers=H).json()["trip_id"]
    assert c.post(f"/trips/{tid}/chat", json={"message": "¿Cómo llego al Museo del Oro?"}, headers=H).status_code == 200
    assert "RUTAS EN VIVO" not in capturado["system"]


def test_endpoint_del_termometro_pide_sesion_y_responde(monkeypatch):
    from app.main import app
    monkeypatch.setattr(t, "API_KEY", "")
    assert TestClient(app).get("/transporte/trafico").status_code == 401
    c, H = _cliente()
    r = c.get("/transporte/trafico", headers=H)
    assert r.status_code == 200 and r.json()["disponible"] is False


# ─────────────────────────── alertas de movilidad ───────────────────────────
def test_filtrar_alertas_deja_solo_movilidad_en_bogota():
    crudos = [
        {"title": "Paro de transportadores bloquea la Autopista Norte en Bogotá", "content": "Hoy hay bloqueos en Bogotá.", "url": "u1", "fecha": "2026-10-23T10:00:00Z"},
        {"title": "Marcha en Medellín por la paz", "content": "Manifestación en Medellín", "url": "u2"},
        {"title": "Receta de ajiaco bogotano", "content": "El mejor ajiaco de Bogotá", "url": "u3"},
        {"title": "Paro de transportadores bloquea la Autopista Norte en Bogotá", "content": "duplicado", "url": "u1"},
        {"title": "TransMilenio cerrará estaciones por marcha", "content": "Marcha en el centro de Bogotá", "url": "u4"},
    ]
    r = t.filtrar_alertas(crudos)
    assert [a["url"] for a in r] == ["u1", "u4"]
    assert r[0]["fecha"] == "2026-10-23"


def test_alertas_sin_tavily_no_inventa(monkeypatch):
    from app import search
    monkeypatch.setattr(search, "MODE", "mock")
    assert t.alertas() == {"disponible": False, "alertas": []}


def test_bloque_alertas_vacio_y_con_datos():
    assert t.bloque_alertas({"alertas": []}) == ""
    b = t.bloque_alertas({"alertas": [{"titulo": "Paro en la Norte", "fecha": "2026-10-23"}]})
    assert "Paro en la Norte" in b and "cautela" in b


def test_pregunta_movilidad_detecta_paro_en_varios_idiomas():
    for m in ["¿hay paro hoy?", "is there a strike today", "y la marcha de mañana", "how do I get to Usaquén"]:
        assert t._PREGUNTA_MOVILIDAD.search(m), m
    assert not t._PREGUNTA_MOVILIDAD.search("recomiéndame un café")


from app import traducir as _traducir  # noqa: E402


# ───────────── integración: chat con tarjeta de ruta + idioma por cabecera ─────────────
def _ruta_falsa(destino):
    return {
        "fuente": "google", "origen": {"nombre": "Zona T"}, "destino": {"nombre": destino},
        "opciones": {"taxi": {"min": 40, "min_sin_trafico": 30, "km": 10.7, "precio": {"min": 25000, "max": 38000}}},
        "trancon": {"nivel": "pesado", "ratio": 1.6, "extra_min": 14}, "hora_pico": False,
        "recomendacion": {"modo": "taxi", "motivo": "taxi_mas_rapido"},
        "enlaces": {"uber": "u", "maps_transporte": "m", "maps_caminar": "c"},
    }


def test_chat_da_tarjeta_solo_para_lugares_curados(monkeypatch):
    from app import llm
    monkeypatch.setattr(t, "API_KEY", "x")
    capturado = {}
    monkeypatch.setattr(llm, "chat", lambda system, history: capturado.__setitem__("system", system) or "ok")
    monkeypatch.setattr(t, "rutas", lambda o, d, s=0: _ruta_falsa(d[2]))
    c, H = _cliente()
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "zona_actual": "Zona G"}, headers=H).json()["trip_id"]
    # curado → tarjeta
    r = c.post(f"/trips/{tid}/chat", json={"message": "¿Cómo llego al Museo del Oro?"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["ruta"]["destino"]["nombre"] == "Museo del Oro"
    # fuera de la curación → sin tarjeta, sin ruta, y el Companion recibe la instrucción de ofrecer curados
    r = c.post(f"/trips/{tid}/chat", json={"message": "quiero ir a la Universidad Javeriana"}, headers=H)
    assert r.json()["ruta"] is None
    assert "NO está en nuestra curación" in capturado["system"] and "Universidad Javeriana" in capturado["system"]
    # charla normal → sin tarjeta
    assert c.post(f"/trips/{tid}/chat", json={"message": "recomiéndame un café"}, headers=H).json()["ruta"] is None


def test_la_cabecera_de_idioma_alinea_el_viaje_y_el_companion(monkeypatch):
    from app import llm
    capturado = {}
    monkeypatch.setattr(llm, "chat", lambda system, history: capturado.setdefault("system", system) and "ok")
    c, H = _cliente()
    tid = c.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "idioma": "es"}, headers=H).json()["trip_id"]
    r = c.post(f"/trips/{tid}/chat", json={"message": "hola"}, headers={**H, "X-UI-Lang": "en"})
    assert r.status_code == 200
    assert "FINAL REMINDER" in capturado["system"] and "English" in capturado["system"]
    assert c.get(f"/trips/{tid}", headers=H).json()["idioma"] == "en"


def test_errores_http_se_traducen_con_la_cabecera(monkeypatch):
    monkeypatch.setattr(_traducir, "_llamar_llm", lambda textos, lang: [f"[{lang}] {x}" for x in textos])
    c, H = _cliente()
    r = c.post("/trips/no-existe/chat", json={"message": "hola"}, headers={**H, "X-UI-Lang": "en"})
    assert r.status_code == 404 and r.json()["detail"].startswith("[en] ")
    r = c.post("/trips/no-existe/chat", json={"message": "hola"}, headers=H)
    assert not r.json()["detail"].startswith("[")


@pytest.mark.parametrize("msg,esperado", [
    ("como ir al museum of gold", "Museo del Oro"),
    ("how do I get to the Gold Museum", "Museo del Oro"),
    ("comment aller au musée de l'or", "Museo del Oro"),
    ("wie komme ich zum Goldmuseum", "Museo del Oro"),
    ("como chegar ao museu do ouro", "Museo del Oro"),
    ("how do I get to the Botero Museum", "Museo Botero"),
    ("directions to Bolivar Square", "Plaza de Bolívar"),
    ("how to get to the botanical garden", "Jardín Botánico José Celestino Mutis"),
])
def test_destinos_curados_se_reconocen_en_otros_idiomas(msg, esperado):
    lugares = [{"name": n, "lat": 4.6, "lng": -74.07} for n in (
        "Museo del Oro", "Museo Botero", "Museo Nacional de Colombia", "Plaza de Bolívar",
        "Jardín Botánico José Celestino Mutis", "TransMilenio — Estación Museo del Oro", "Parque El Virrey")]
    d = t.detectar_destino(msg, lugares)
    assert d and d["nombre"] == esperado


def test_una_palabra_generica_sola_no_es_un_destino():
    lugares = [{"name": "Museo del Oro", "lat": 4.6, "lng": -74.07}, {"name": "Museo Botero", "lat": 4.6, "lng": -74.07}]
    assert t.detectar_destino("how do I get to the museum", lugares) is None
