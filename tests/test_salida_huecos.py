"""Avisos de salida (tiempos realistas, texto con horas reales) y recomendaciones por hueco cruzadas con los gustos."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import db, geo, huecos, scheduler, watchers

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
    monkeypatch.setattr(db, "mark_proactive_sent", lambda *a: True)
    monkeypatch.setattr(db, "insert", lambda t, row: salidas["chat"].append(row))
    monkeypatch.setattr(db, "mark_recommended", lambda tid, n: salidas["rec"].append(n))
    monkeypatch.setattr(geo, "resolve_origin", lambda trip: (4.658, -74.057))
    monkeypatch.setattr(huecos.horarios, "estado", lambda pid, cuando: None)
    monkeypatch.setattr(watchers, "_push_checkin", lambda tid, t, txt, abrir="chat", dest=None, gancho=None: salidas["push"].append((t, gancho, abrir, dest)))
    salidas["trip"] = {"id": "t1", "ciudad": "Bogotá", "idioma": "es", "gustos": ["Café de especialidad", "Vida nocturna"]}
    return salidas


def test_hueco_recomienda_segun_el_gusto_del_onboarding(hueco):
    assert huecos.revisar(hueco["trip"], en(11, 0)) == ["hueco_1"]
    titulo, gancho, abrir, dest = hueco["push"][0]
    assert titulo == "⏳ Libre hasta las 15:00"
    assert "Café de especialidad → Tropicalia Coffee" in gancho and len(gancho) <= 72
    assert (abrir, dest) == ("como_llegar", "Tropicalia Coffee")
    assert "marcaste que te gusta «Café de especialidad»" in hueco["chat"][0]["content"] and hueco["rec"] == ["Tropicalia Coffee"]


def test_hueco_no_sale_si_no_cabe_o_esta_en_un_plan_o_esta_cerrado(hueco, monkeypatch):
    assert huecos.revisar(hueco["trip"], en(13, 45)) == []            # solo 75 min libres: no alcanza
    assert huecos.revisar(hueco["trip"], en(15, 20)) == []            # ya está dentro del plan de las 15:00
    monkeypatch.setattr(huecos.horarios, "estado", lambda pid, cuando: {"abierto": False, "cierra": None, "abre": "12:00", "24h": False})
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
