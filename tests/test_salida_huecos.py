"""Avisos de salida (tiempos realistas, texto con horas reales) y recomendaciones por hueco cruzadas con los gustos."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import db, estrategia, geo, huecos, scheduler, watchers

TZ = ZoneInfo("America/Bogota")
HOY = "2026-11-03"
MONSERRATE = {"name": "Cerro de Monserrate", "lat": 4.60557, "lng": -74.055446, "category": "mirador", "tags": [],
              "zona": "La Candelaria / Cerros", "descripcion": "Mirador sobre Bogotá.", "place_id": None}


def en(h, m=0):
    return datetime(2026, 11, 3, h, m, tzinfo=TZ)


def test_los_tiempos_de_viaje_son_de_carro_no_de_caminata():
    # Antes: 15 min/km → 8.5 km daban 128 min y el aviso salía 3 h antes del plan.
    assert 30 <= geo.travel_minutes(8.5, "Bogotá") <= 50
    assert geo.travel_minutes(15, "Bogotá") < 80
    assert geo.travel_minutes(0.5, "Bogotá") == 10          # a pie, con el mínimo


def test_el_titulo_sale_de_los_minutos_que_faltan_y_no_contradice_el_cuerpo():
    t, b = watchers.texto_salida("es", "ya", "Tour a Monserrate", "10:30", "09:58", 32, 10)
    assert t == "🚕 Sal en 10 min" and "10:30" in b and "32" in b
    assert watchers.texto_salida("es", "ya", "Tour", "10:30", "09:58", 32, 3)[0] == "🚕 Sal ya"
    t, b = watchers.texto_salida("es", "aviso", "Tour a Monserrate", "10:30", "09:58", 32, 45)
    assert "Sal ya" not in t and "09:58" in b            # el aviso con margen nunca dice «sal ya»
    assert watchers.texto_salida("en", "ya", "Tour", "10:30", "09:58", 32, 10)[0] == "🚕 Leave in 10 min"
    for idioma in ("es", "en", "pt", "fr", "de", "it"):
        assert watchers.texto_salida(idioma, "aviso", "X", "10:30", "09:58", 32, 45)[1]


@pytest.fixture
def agenda(monkeypatch):
    enviados, marcas = [], set()
    plan = {"id": "plan1234abcd", "fecha": HOY, "hora": "10:30", "titulo": "Tour a Monserrate", "lugar": "Cerro de Monserrate",
            "tipo": "actividad"}
    monkeypatch.setattr(db, "get_plans", lambda tid: [plan])
    monkeypatch.setattr(db, "places_for_city", lambda c: [MONSERRATE])
    monkeypatch.setattr(db, "mark_proactive_sent", lambda tid, k, d: (tid, k, d) not in marcas and not marcas.add((tid, k, d)))
    monkeypatch.setattr(watchers, "aviso_hora_de_salir",
                        lambda tid, plan, fase, salida, viaje, faltan, lugar=None: enviados.append((fase, salida, lugar)))
    return enviados


def test_tour_a_monserrate_avisa_dos_veces_y_nunca_con_horas_de_sobra(agenda):
    trip = {"id": "t1", "ciudad": "Bogotá", "zona_actual": "Zona G", "idioma": "es"}
    momento = en(6, 0)
    primero = None
    while momento < en(11, 0):
        antes = len(agenda)
        scheduler._aviso_planes(trip, momento)
        if len(agenda) > antes and primero is None:
            primero = momento
        momento += timedelta(minutes=15)
    assert [f for f, _, _ in agenda] == ["aviso", "ya"]            # uno de cada, una sola vez
    assert agenda[0][2] == "Cerro de Monserrate"                    # el toque abre «Cómo llegar» al lugar del plan
    assert en(10, 30) - primero <= timedelta(minutes=95)             # el primer aviso llega a ~1 h 20 del plan, no a 3 h


# ── huecos ──
CAFE = {"name": "Tropicalia Coffee", "lat": 4.663761, "lng": -74.050684, "category": "cafe", "tags": [], "rating": 4.3,
        "zona": "Chapinero Alto", "place_id": None, "local": {"clave": "Cafetería mejor posicionada de Colombia."}}
BAR = {"name": "Bar Lejos", "lat": 4.66, "lng": -74.05, "category": "bar", "tags": ["Vida nocturna"], "rating": 4.9}


@pytest.fixture
def hueco(monkeypatch):
    salidas = {"push": [], "chat": [], "rec": []}
    plan = {"id": "p1", "fecha": HOY, "hora": "15:00", "titulo": "Tour", "lugar": "", "tipo": "actividad"}
    monkeypatch.setattr(db, "get_plans", lambda tid: [plan])
    monkeypatch.setattr(db, "places_for_city", lambda c: [CAFE, BAR])
    monkeypatch.setattr(db, "recently_recommended", lambda *a, **k: set())
    monkeypatch.setattr(db, "recommended_within", lambda *a, **k: False)
    monkeypatch.setattr(db, "proactive_already_sent", lambda *a: False)
    monkeypatch.setattr(db, "engagement_count", lambda *a: 0)
    monkeypatch.setattr(db, "mark_proactive_sent", lambda *a: True)
    monkeypatch.setattr(db, "insert", lambda t, row: salidas["chat"].append(row))
    monkeypatch.setattr(db, "mark_recommended", lambda tid, n: salidas["rec"].append(n))
    monkeypatch.setattr(geo, "resolve_origin", lambda trip: (4.658, -74.057))
    monkeypatch.setattr(estrategia.horarios, "estado", lambda pid, cuando: None)
    monkeypatch.setattr(watchers, "_push_checkin", lambda tid, t, txt, abrir="chat", dest=None, gancho=None: salidas["push"].append((t, gancho, abrir, dest)))
    salidas["trip"] = {"id": "t1", "ciudad": "Bogotá", "idioma": "es", "gustos": ["Café de especialidad", "Vida nocturna"]}
    return salidas


def test_hueco_recomienda_segun_el_gusto_del_onboarding(hueco):
    assert huecos.revisar(hueco["trip"], en(11, 0)) == ["hueco_1"]
    titulo, gancho, abrir, dest = hueco["push"][0]
    assert titulo == "⏳ Libre hasta las 15:00"
    assert gancho == "☕ Tropicalia Coffee · a 11 min de ti" and len(gancho) <= 72
    assert (abrir, dest) == ("como_llegar", "Tropicalia Coffee")
    assert "marcaste que te gusta «Café de especialidad»" in hueco["chat"][0]["content"] and hueco["rec"] == ["Tropicalia Coffee"]


def test_hueco_no_sale_si_no_cabe_o_esta_en_un_plan_o_esta_cerrado(hueco, monkeypatch):
    assert huecos.revisar(hueco["trip"], en(13, 45)) == []            # solo 75 min libres: no alcanza
    assert huecos.revisar(hueco["trip"], en(15, 20)) == []            # ya está dentro del plan de las 15:00
    monkeypatch.setattr(estrategia.horarios, "estado", lambda pid, cuando: {"abierto": False, "cierra": None, "abre": "12:00", "24h": False})
    assert huecos.revisar(hueco["trip"], en(11, 0)) == []             # cerrado cuando llegaría
    assert huecos.revisar({**hueco["trip"], "gustos": ["Playas"]}, en(11, 0)) == []   # nada calza con sus gustos


def test_hueco_respeta_limites(hueco, monkeypatch):
    assert huecos.revisar(hueco["trip"], en(9, 0)) == []              # fuera de la ventana horaria
    monkeypatch.setattr(db, "recommended_within", lambda *a, **k: True)
    assert huecos.revisar(hueco["trip"], en(11, 0)) == []             # ya se le recomendó algo hace poco
    monkeypatch.setattr(db, "recommended_within", lambda *a, **k: False)
    monkeypatch.setattr(db, "proactive_already_sent", lambda *a: True)
    assert huecos.revisar(hueco["trip"], en(11, 0)) == []             # ya salieron los 2 del día


def test_los_cafes_sin_etiquetas_cuentan_por_su_categoria():
    assert watchers.gustos_que_calzan(CAFE, ["Café de especialidad"]) == ["Café de especialidad"]
    assert watchers.gustos_que_calzan(BAR, ["Vida nocturna", "Café de especialidad"]) == ["Vida nocturna"]
    assert watchers.gustos_que_calzan(CAFE, ["Playas"]) == []


# ── agenda calculada: el chat recibe los números resueltos, no hace la cuenta ──
from app import agenda as agenda_mod  # noqa: E402


def _trip_monserrate():
    return {"id": "t1", "ciudad": "Bogotá", "zona_actual": "Zona G", "idioma": "es"}


def test_la_agenda_dice_que_todavia_no_es_hora_de_salir(agenda_hoy):
    b = agenda_mod.bloque(_trip_monserrate(), en(6, 30))
    assert "Tour a Monserrate, hoy a las 10:30: faltan 4 h" in b
    assert "TODAVÍA NO ES HORA DE SALIR" in b and "hora de salir 10:00" in b
    assert "ES HORA DE SALIR YA" not in b


def test_la_agenda_cambia_cuando_se_acerca_la_hora(agenda_hoy):
    assert "ya casi" in agenda_mod.bloque(_trip_monserrate(), en(9, 50))
    assert "ES HORA DE SALIR YA" in agenda_mod.bloque(_trip_monserrate(), en(10, 5))
    assert "ya empezó" in agenda_mod.bloque(_trip_monserrate(), en(10, 40))
    assert agenda_mod.bloque(_trip_monserrate(), en(12, 0)) == ""          # plan viejo: no se menciona


@pytest.fixture
def agenda_hoy(monkeypatch):
    plan = {"id": "plan1234abcd", "fecha": HOY, "hora": "10:30", "titulo": "Tour a Monserrate", "lugar": "Cerro de Monserrate", "tipo": "actividad"}
    monkeypatch.setattr(db, "get_plans", lambda tid: [plan])
    monkeypatch.setattr(db, "places_for_city", lambda c: [MONSERRATE])


def test_el_prompt_del_chat_incluye_la_agenda_calculada(monkeypatch, agenda_hoy):
    from app import context, timeutil
    monkeypatch.setattr(timeutil, "now_local", lambda trip: en(6, 30))
    monkeypatch.setattr(db, "rows", lambda *a, **k: [])
    trip = {"id": "t1", "ciudad": "Bogotá", "hotel": "H", "zona_actual": "Zona G", "idioma": "es", "gustos": [], "inicio": HOY, "fin": "2026-11-06",
            "planes": [{"fecha": HOY, "hora": "10:30", "titulo": "Tour a Monserrate", "lugar": "Cerro de Monserrate", "tipo": "actividad"}]}
    prompt = context.build(trip, mensaje="hola")
    assert "AGENDA DE HOY CALCULADA" in prompt and "TODAVÍA NO ES HORA DE SALIR" in prompt


# ── el viaje se calcula DESDE el plan anterior, no desde el hotel (caso real: Monserrate → La Candelaria, «sal 44 min antes») ──
GRAFITI = {"name": "Tour de grafiti por La Candelaria", "lat": 4.5996, "lng": -74.0719, "category": "experiencia", "tags": [],
           "zona": "La Candelaria", "descripcion": "Tour.", "place_id": None}


def _dos_planes(monkeypatch, hora2):
    planes = [
        {"id": "p1aaaaaaaa", "fecha": HOY, "hora": "10:30", "titulo": "Tour a Monserrate", "lugar": "Cerro de Monserrate", "tipo": "actividad"},
        {"id": "p2bbbbbbbb", "fecha": HOY, "hora": hora2, "titulo": "Tour de grafiti", "lugar": "Tour de grafiti por La Candelaria", "tipo": "actividad"},
    ]
    monkeypatch.setattr(db, "get_plans", lambda tid: planes)
    monkeypatch.setattr(db, "places_for_city", lambda c: [MONSERRATE, GRAFITI])


def test_el_viaje_al_segundo_plan_sale_del_primero_no_del_hotel(monkeypatch):
    _dos_planes(monkeypatch, "14:00")
    segundo = agenda_mod.calcular(_trip_monserrate(), en(6, 30))[1]
    assert segundo["desde"] == "Cerro de Monserrate" and segundo["viaje_min"] <= 20   # antes: ~44 min desde la Zona G
    assert segundo["apretado_min"] == 0
    assert "desde Cerro de Monserrate" in agenda_mod.bloque(_trip_monserrate(), en(6, 30))


def test_si_el_itinerario_esta_apretado_se_avisa(monkeypatch):
    _dos_planes(monkeypatch, "12:30")          # Monserrate dura ~2 h (hasta 12:30) y el siguiente es a las 12:30
    segundo = agenda_mod.calcular(_trip_monserrate(), en(6, 30))[1]
    assert segundo["apretado_min"] > 0
    assert "el itinerario está apretado" in agenda_mod.bloque(_trip_monserrate(), en(6, 30))


# ── estrategia: «quiero un café» con gusto por el café y un plan después ──
from app import estrategia  # noqa: E402


def _cafe(nombre, lat, lng, rating, clave=None):
    return {"name": nombre, "lat": lat, "lng": lng, "category": "cafe", "tags": [], "rating": rating, "zona": "Chapinero",
            "place_id": None, "descripcion": "Cafe.", "local": {"clave": clave} if clave else {}}


@pytest.fixture
def cafes(monkeypatch):
    plaza = {"name": "Plaza X", "lat": 4.62, "lng": -74.07, "category": "atraccion", "tags": [], "zona": "Centro", "place_id": None, "descripcion": "Lugar."}
    lugares = [_cafe("Cafe Cerquita", 4.659, -74.056, 4.0), _cafe("Cafe Vecino", 4.6585, -74.0575, 4.2),
               _cafe("Cafe Top", 4.630, -74.065, 4.8, "Tostador de especialidad: el mejor filtrado de Bogotá."), plaza]
    monkeypatch.setattr(db, "places_for_city", lambda c: lugares)
    monkeypatch.setattr(agenda_mod, "origen", lambda trip: (4.658, -74.057))
    monkeypatch.setattr(estrategia.horarios, "estado", lambda pid, cuando: None)
    plan = {"id": "p1", "fecha": HOY, "hora": "12:00", "titulo": "Tour del centro", "lugar": "Plaza X", "tipo": "actividad"}
    return {"id": "t1", "ciudad": "Bogotá", "idioma": "es", "gustos": ["Café de especialidad"]}, [plan]


def test_cafe_con_tiempo_propone_la_mejor_experiencia_de_paso(cafes):
    trip, planes = cafes
    b = estrategia.bloque(trip, "quiero tomarme un café cerca del hotel", en(9, 30), planes)
    assert "OPCIONES CERCA" in b and "Cafe Cerquita" in b and "Cafe Vecino" in b
    assert "SI TIENE TIEMPO: Cafe Top" in b and "DE PASO" in b
    assert "marcó que le gusta" in b                                  # lo marcó en el onboarding


def test_cafe_sin_tiempo_no_manda_al_usuario_lejos(cafes):
    trip, planes = cafes
    b = estrategia.bloque(trip, "quiero un café", en(11, 30), planes)   # faltan 30 min para el tour
    assert "Cafe Cerquita" in b and "SI TIENE TIEMPO" not in b and "No propongas un lugar lejano" in b


def test_estrategia_solo_aplica_a_pedidos_de_ese_tipo(cafes):
    trip, planes = cafes
    assert estrategia.bloque(trip, "cómo llego al museo del oro", en(9, 30), planes) == ""


# ── víspera ──
from app import anzuelos  # noqa: E402


def test_vispera_manda_un_dato_de_local_del_plan_de_manana(monkeypatch):
    salidas = {"push": [], "chat": []}
    lugar = {**MONSERRATE, "local": {"momento": "Sube antes de las 10: la fila crece.", "ojo": "Lleva efectivo."}}
    plan = {"id": "p9", "fecha": "2026-11-04", "hora": "10:30", "titulo": "Tour a Monserrate", "lugar": "Cerro de Monserrate", "tipo": "actividad"}
    monkeypatch.setattr(db, "get_plans", lambda tid: [plan])
    monkeypatch.setattr(db, "places_for_city", lambda c: [lugar])
    monkeypatch.setattr(db, "engagement_count", lambda *a: 0)
    monkeypatch.setattr(db, "mark_proactive_sent", lambda *a: True)
    monkeypatch.setattr(db, "insert", lambda t, row: salidas["chat"].append(row))
    monkeypatch.setattr(watchers, "_push_checkin", lambda tid, t, txt, abrir="chat", dest=None, gancho=None: salidas["push"].append((t, gancho, abrir)))
    trip = {"id": "t1", "ciudad": "Bogotá", "idioma": "es"}
    assert anzuelos.vispera(trip, en(15, 0)) == []                      # fuera de la ventana de la noche
    assert anzuelos.vispera(trip, en(20, 0)) == ["vispera_p9"]
    titulo, gancho, abrir = salidas["push"][0]
    assert titulo == "🗓️ Mañana: Tour a Monserrate" and gancho == "💡 Sube antes de las 10: la fila crece." and abrir == "planes"
    assert "Tip de local: Sube antes de las 10" in salidas["chat"][0]["content"]
    monkeypatch.setattr(db, "engagement_count", lambda *a: watchers.ENGAGEMENT_MAX_DIA)
    assert anzuelos.vispera(trip, en(20, 0)) == []                      # tope diario de avisos «de interés»


# ── el MISMO razonamiento sirve para cualquier pedido, no solo café ──
def _lugar(nombre, cat, lat, lng, rating, clave="", tags=None):
    return {"name": nombre, "lat": lat, "lng": lng, "category": cat, "tags": tags or [], "rating": rating, "zona": "Centro",
            "place_id": None, "descripcion": "Cafe.", "local": {"clave": clave} if clave else {}}


@pytest.fixture
def cultura(monkeypatch):
    plaza = _lugar("Plaza X", "atraccion", 4.62, -74.07, 4.0)
    lugares = [_lugar("Museito", "atraccion", 4.659, -74.056, 4.2),
               _lugar("Museo Imperdible", "atraccion", 4.630, -74.065, 4.8, "El museo más imperdible de Bogotá, patrimonio nacional."),
               _lugar("Graffiti Tour", "experiencia", 4.6585, -74.0575, 4.6, "Tour guiado de arte urbano.", ["Grafiti y arte urbano"]),
               _lugar("Restaurante Cerca", "restaurante", 4.6582, -74.0572, 4.5), plaza]
    monkeypatch.setattr(db, "places_for_city", lambda c: lugares)
    monkeypatch.setattr(agenda_mod, "origen", lambda trip: (4.658, -74.057))
    monkeypatch.setattr(estrategia.horarios, "estado", lambda pid, cuando: None)
    plan = {"id": "p1", "fecha": HOY, "hora": "13:00", "titulo": "Almuerzo en Plaza X", "lugar": "Plaza X", "tipo": "restaurante"}
    return {"id": "t1", "ciudad": "Bogotá", "idioma": "es", "gustos": ["Museos y galerías", "Grafiti y arte urbano"]}, [plan]


def test_museo_con_tiempo_propone_el_mejor_de_paso(cultura):
    trip, planes = cultura
    b = estrategia.bloque(trip, "quiero conocer un museo", en(9, 30), planes)
    assert "algo de cultura" in b and "Museito" in b
    assert "SI TIENE TIEMPO: Museo Imperdible" in b and "DE PASO" in b
    assert "marcó que le gusta" in b and "Museos y galerías" in b


def test_pedido_abierto_se_decide_por_gustos_y_hora(cultura):
    trip, planes = cultura
    b = estrategia.bloque(trip, "tengo tiempo libre, ¿qué hago?", en(9, 30), planes)
    assert "busca un plan" in b
    assert "Restaurante Cerca" not in b                                # a las 9:30 no es hora de comida y no es de sus gustos
    assert "Graffiti Tour" in b and "[calza con: Grafiti y arte urbano]" in b


def test_sin_tiempo_el_razonamiento_tambien_aplica_a_museos(cultura):
    trip, planes = cultura
    b = estrategia.bloque(trip, "quiero conocer un museo", en(12, 15), planes)    # faltan 45 min para el almuerzo
    assert "SI TIENE TIEMPO" not in b and "No propongas un lugar lejano" in b


def test_mensajes_que_no_piden_lugar_no_activan_la_estrategia(cultura):
    trip, planes = cultura
    for msg in ("gracias!", "a qué hora sale mi vuelo", "cómo llego al aeropuerto"):
        assert estrategia.bloque(trip, msg, en(9, 30), planes) == ""


def test_el_prompt_trae_la_regla_general_de_razonamiento(monkeypatch):
    from app import context
    monkeypatch.setattr(db, "rows", lambda *a, **k: [])
    trip = {"id": "t1", "ciudad": "Bogotá", "hotel": "H", "zona_actual": "Zona G", "idioma": "es", "gustos": [], "inicio": HOY, "fin": "2026-11-06"}
    assert "RAZONAMIENTO ESTRATÉGICO" in context.build(trip, mensaje="hola") and "DE PASO" in context.build(trip, mensaje="hola")


def test_el_gusto_solo_cruza_por_categoria_cuando_la_categoria_es_el_gusto():
    # «Gastronomía local» o «Grafiti» NO son «cualquier restaurante / cualquier experiencia»: sin etiqueta no calzan.
    assert watchers.gustos_que_calzan({"category": "restaurante", "tags": []}, ["Gastronomía local"]) == []
    assert watchers.gustos_que_calzan({"category": "experiencia", "tags": []}, ["Grafiti y arte urbano"]) == []
    assert watchers.gustos_que_calzan({"category": "restaurante", "tags": ["Gastronomía local"]}, ["Gastronomía local"]) == ["Gastronomía local"]


def test_no_se_le_recomienda_el_lugar_de_su_propio_plan(cultura):
    trip, planes = cultura
    planes = planes + [{"id": "p2", "fecha": HOY, "hora": "15:00", "titulo": "Visita al Museo Imperdible", "lugar": "Museo Imperdible", "tipo": "actividad"}]
    b = estrategia.bloque(trip, "quiero conocer un museo", en(9, 30), planes)
    assert "Museo Imperdible" not in b.split("CÓMO RESPONDER")[0]


def test_lo_dominical_no_se_ofrece_entre_semana(monkeypatch, cultura):
    trip, planes = cultura
    ciclovia = _lugar("Ciclovía dominical", "experiencia", 4.6585, -74.0572, 4.9, "Domingos 7am-2pm cierran las vías.", ["Grafiti y arte urbano"])
    monkeypatch.setattr(db, "places_for_city", lambda c: [ciclovia, _lugar("Museito", "atraccion", 4.659, -74.056, 4.2)])
    assert "Ciclovía" not in estrategia.bloque(trip, "tengo tiempo libre, ¿qué hago?", en(9, 30), planes)      # martes
    domingo = datetime(2026, 11, 8, 9, 30, tzinfo=TZ)
    assert "Ciclovía" in estrategia.bloque(trip, "tengo tiempo libre, ¿qué hago?", domingo, [])


def test_el_bloque_de_estrategia_va_al_final_del_prompt(monkeypatch):
    from app import context, timeutil
    monkeypatch.setattr(timeutil, "now_local", lambda trip: en(9, 30))
    monkeypatch.setattr(db, "rows", lambda *a, **k: [])
    monkeypatch.setattr(db, "places_for_city", lambda c: [_cafe("Cafe Cerquita", 4.659, -74.056, 4.5)])
    monkeypatch.setattr(agenda_mod, "origen", lambda trip: (4.658, -74.057))
    monkeypatch.setattr(estrategia.horarios, "estado", lambda pid, cuando: None)
    trip = {"id": "t1", "ciudad": "Bogotá", "hotel": "H", "zona_actual": "Zona G", "idioma": "es", "gustos": ["Café de especialidad"],
            "inicio": HOY, "fin": "2026-11-06", "planes": []}
    prompt = context.build(trip, mensaje="quiero un café")
    assert "ESTRATEGIA PARA ESTE PEDIDO" in prompt and prompt.rindex("ESTRATEGIA PARA ESTE PEDIDO") > prompt.rindex("RAZONAMIENTO ESTRATÉGICO")
    assert prompt.rstrip().endswith("(«¿te llevo a alguna?»).")


# ── el hueco combina gusto + cercanía (a ti, a tu siguiente plan, de paso) y parte de DONDE ESTARÁ ──
def _lugares_hueco(monkeypatch, lugares, plan_lugar=None, plan_hora="15:00"):
    plan = {"id": "p1", "fecha": HOY, "hora": plan_hora, "titulo": "Tour", "lugar": plan_lugar or "", "tipo": "actividad"}
    monkeypatch.setattr(db, "get_plans", lambda tid: [plan])
    monkeypatch.setattr(db, "places_for_city", lambda c: lugares)
    return plan


def test_hueco_prefiere_lo_que_queda_junto_a_su_siguiente_plan(hueco, monkeypatch):
    # Origen (Zona G) lejos de ambos; «Cafe Junto» está pegado al siguiente plan, «Cafe Aislado» no.
    plaza = _lugar("Plaza Tour", "atraccion", 4.610, -74.075, 4.0)
    junto = _cafe("Cafe Junto", 4.6105, -74.0752, 4.4)
    aislado = _cafe("Cafe Aislado", 4.63, -74.10, 4.4)
    _lugares_hueco(monkeypatch, [junto, aislado, plaza], plan_lugar="Plaza Tour")
    assert huecos.revisar(hueco["trip"], en(10, 30)) == ["hueco_1"]
    titulo, gancho, abrir, dest = hueco["push"][0]
    assert dest == "Cafe Junto" and "junto a tu Tour" in gancho


def test_hueco_cuenta_lo_que_queda_de_paso(hueco, monkeypatch):
    plaza = _lugar("Plaza Tour", "atraccion", 4.610, -74.075, 4.0)
    en_camino = _cafe("Cafe En Camino", 4.634, -74.066, 4.9, "El mejor tostador de la ciudad, imperdible.")
    _lugares_hueco(monkeypatch, [en_camino, plaza], plan_lugar="Plaza Tour")
    assert huecos.revisar(hueco["trip"], en(10, 30)) == ["hueco_1"]
    assert "de paso a tu Tour" in hueco["push"][0][1]
    assert "Te queda DE PASO para tu Tour" in hueco["chat"][0]["content"]


def test_hueco_parte_del_lugar_donde_acaba_de_terminar_no_del_hotel(hueco, monkeypatch):
    # Terminó «Tour a Monserrate» (10:30 + 2 h → 12:30); a las 12:40 está ALLÁ (cerca de «Cafe Monserrate»), no en la Zona G.
    cerro = {**MONSERRATE, "name": "Cerro Test"}
    cafe_cerro = _cafe("Cafe Monserrate", 4.6058, -74.0552, 4.5)
    cafe_hotel = _cafe("Cafe Hotel", 4.6585, -74.0572, 4.8)           # pegado al hotel: más lejos de donde está de verdad
    planes = [{"id": "m1", "fecha": HOY, "hora": "10:30", "titulo": "Tour a Monserrate", "lugar": "Cerro Test", "tipo": "actividad"},
              {"id": "m2", "fecha": HOY, "hora": "15:30", "titulo": "Cena", "lugar": "", "tipo": "restaurante"}]
    monkeypatch.setattr(db, "get_plans", lambda tid: planes)
    monkeypatch.setattr(db, "places_for_city", lambda c: [cerro, cafe_cerro, cafe_hotel])
    assert huecos.revisar(hueco["trip"], en(12, 40)) == ["hueco_1"]
    assert hueco["push"][0][3] == "Cafe Monserrate"


def test_hueco_no_propone_si_no_calza_con_nada_que_le_guste(hueco, monkeypatch):
    bar = _lugar("Bar X", "bar", 4.659, -74.056, 4.9)
    _lugares_hueco(monkeypatch, [bar], plan_hora="21:00")
    assert huecos.revisar({**hueco["trip"], "gustos": ["Playas"]}, en(18, 0)) == []
