"""Sentido del tiempo: lectura de horas, cuentas de llegada y el pedido «agrégalo al itinerario»."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app import db, itinerario_chat as ic, reloj

TZ = ZoneInfo("America/Bogota")
AHORA = datetime(2026, 10, 26, 15, 0, tzinfo=TZ)      # lunes


def test_lee_la_llegada_y_su_ambiguedad():
    assert reloj.leer_llegada("llego a Bogotá a las 6 pm") == {"hora": "18:00", "ambigua": False, "nacional": False}
    assert reloj.leer_llegada("mi vuelo aterriza a las 18:30")["hora"] == "18:30"
    assert reloj.leer_llegada("aterrizo hoy a las 6")["ambigua"] is True          # ¿mañana o tarde?
    assert reloj.leer_llegada("llego a las 7 de la mañana")["hora"] == "07:00"
    assert reloj.leer_llegada("llego a las 9 pm en un vuelo desde Medellín")["nacional"] is True
    assert reloj.leer_llegada("quiero cenar a las 8 pm") is None                  # no es una llegada


def test_hora_de_cita():
    assert reloj.leer_hora_cita("agrégalo mañana a las 8 pm") == "20:00"
    assert reloj.leer_hora_cita("a las 14:30") == "14:30"
    assert reloj.leer_hora_cita("agrégalo al itinerario") is None


def test_hora_pico_bogota():
    assert reloj.hora_pico(datetime(2026, 10, 26, 18, 0, tzinfo=TZ))              # lunes 18:00
    assert not reloj.hora_pico(datetime(2026, 10, 26, 11, 0, tzinfo=TZ))
    assert not reloj.hora_pico(datetime(2026, 10, 24, 18, 0, tzinfo=TZ))          # sábado


def test_caso_real_llega_18_no_puede_salir_del_hotel_a_las_19(monkeypatch):
    """El caso que motivó el módulo: aterriza 18:00 → NO se puede estar saliendo del hotel a las 19:00."""
    monkeypatch.setattr(reloj, "taxi_min", lambda o, d, salida, ahora: (60, "estimado"))
    trip = {"ciudad": "Bogotá", "inicio": "2026-10-26", "lat_hotel": 4.66, "lng_hotel": -74.05}
    p = reloj.plan_de_llegada(trip, {"hora": "18:00", "ambigua": False, "nacional": False}, AHORA)
    assert p["sale_terminal"].strftime("%H:%M") == "19:15"        # 18:00 + 75 min
    assert p["en_hotel"].strftime("%H:%M") == "20:15"             # + 60 min de trancón
    assert p["listo"].strftime("%H:%M") == "20:45"                # + 30 min para alistarse
    texto = reloj.texto_llegada(p, {"hora": "18:00", "ambigua": False, "nacional": False})
    assert "20:45" in texto and "NO propongas salir del hotel antes de las 20:45" in texto


def test_llegada_nacional_ahorra_tiempo(monkeypatch):
    monkeypatch.setattr(reloj, "taxi_min", lambda o, d, salida, ahora: (45, "estimado"))
    trip = {"ciudad": "Bogotá", "inicio": "2026-10-26"}
    nac = reloj.plan_de_llegada(trip, {"hora": "18:00", "nacional": True}, AHORA)
    intl = reloj.plan_de_llegada(trip, {"hora": "18:00", "nacional": False}, AHORA)
    assert (intl["listo"] - nac["listo"]).total_seconds() == 40 * 60


def test_bloque_nunca_lanza_y_trae_la_hora():
    b = reloj.bloque({"ciudad": "Bogotá", "planes": []}, "hola", None, AHORA)
    assert "lunes 26/10 15:00" in b and "RELOJ Y LOGÍSTICA" in b


def test_pide_agregar():
    for ok in ("agrégalo a mi itinerario", "agrega ese al itinerario", "ponlo", "anótalo", "añade el segundo a mi plan"):
        assert ic.pide_agregar(ok), ok
    for no in ("qué hay en mi itinerario", "recomiéndame un plan", "cómo llego al museo", "dónde como"):
        assert not ic.pide_agregar(no), no


def test_agrega_el_lugar_que_acaba_de_recomendar():
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-10-26", "fin": "2026-10-30", "idioma": "es"})
    trip = db.get_trip(tid)
    ofrecidos = [p["name"] for p in db.places_for_city("Bogotá") if p.get("category") == "restaurante"][:3]
    ultimo = "Te propongo: 1. " + ofrecidos[0] + " 2. " + ofrecidos[1] + " 3. " + ofrecidos[2] + ". ¿Cuál?"
    r = ic.agregar_desde_chat(trip, "agrega el segundo al itinerario mañana a las 8 pm", ultimo)
    assert r and ofrecidos[1] in r["reply"] and "agregué" in r["reply"]
    plan = db.get_plans(tid)[-1]
    assert plan["lugar"] == ofrecidos[1] and plan["hora"] == "20:00" and plan["fecha"]
    # repetirlo no duplica
    again = ic.agregar_desde_chat(db.get_trip(tid), "agrega el segundo al itinerario mañana a las 8 pm", ultimo)
    assert again and "ya estaba" in again["reply"] and len(db.get_plans(tid)) == 1
    # sin saber de qué lugar habla → None (el chat normal pregunta cuál)
    assert ic.agregar_desde_chat(trip, "agrégalo al itinerario", "no te recomendé nada") is None
