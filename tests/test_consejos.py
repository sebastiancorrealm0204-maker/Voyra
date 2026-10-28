"""Consejos de llegada y de noche: deterministas, una vez por día, siempre con taxi por app y «jamás uno de la calle»."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import consejos, db, push

TZ = ZoneInfo("America/Bogota")


@pytest.fixture
def enviados(monkeypatch):
    salidas = []
    monkeypatch.setattr(push, "send_to_trip_async", lambda tid, t, b, data=None: salidas.append((t, b, (data or {}).get("kind"))) or True)
    monkeypatch.setattr(db, "mark_proactive_sent", lambda tid, kind, d, _v=set(): (tid, kind, d) not in _v and not _v.add((tid, kind, d)))
    monkeypatch.setattr(db, "insert", lambda *a, **k: None)
    monkeypatch.setattr(db, "user_active_within", lambda *a, **k: False)
    return salidas


def trip(**k):
    return {"id": "t1", "ciudad": "Bogotá", "inicio": "2026-11-02", "fin": "2026-11-06", "idioma": "es", **k}


def en(h, m=0, dia=2):
    return datetime(2026, 11, dia, h, m, tzinfo=TZ)


def test_titulo_nunca_es_solo_companion():
    assert push.titulo_limpio("Companion", "Sal ya para llegar. Son 20 min.") == "Sal ya para llegar."
    assert push.titulo_limpio("Companion: Hora de salir", "x") == "Hora de salir"
    assert push.titulo_limpio("Hora de salir", "x") == "Hora de salir"


def test_llegada_de_noche_avisa_antes_y_al_aterrizar(enviados):
    t = trip(vuelo_ida="Avianca, llego a las 11 pm")
    assert consejos.revisar(t, en(22, 0), planes=[]) == ["llegada_pre_noche"]
    assert consejos.revisar(t, en(22, 0), planes=[]) == []          # una sola vez
    assert consejos.revisar(t, en(23, 30), planes=[]) == ["aterrizaje_noche"]
    for _, cuerpo, _k in enviados:
        assert "taxi de la calle" in cuerpo.lower() or "jamás" in cuerpo.lower()


def test_llegada_de_dia_es_bienvenida_sin_alarma(enviados):
    t = trip(vuelo_ida="llego a las 2 pm")
    assert consejos.revisar(t, en(13, 0), planes=[]) == ["llegada_pre_dia"]
    assert consejos.revisar(t, en(14, 30), planes=[]) == ["aterrizaje_dia"]


def test_sin_hora_de_llegada_no_inventa(enviados):
    assert consejos.revisar(trip(), en(13, 0), planes=[]) == []


def test_plan_nocturno_aconseja_taxi_por_app(enviados):
    planes = [{"tipo": "actividad", "titulo": "Rumba en Andrés", "fecha": "2026-11-03", "hora": "22:00"}]
    t = trip()
    assert consejos.revisar(t, en(20, 30, dia=3), planes=planes) == ["noche_plan"]
    assert "jamás uno de la calle" in enviados[0][1]  # el push corto conserva la advertencia de seguridad
    assert "Jamás tomes un taxi de la calle" in consejos._texto("noche_plan", trip())[1]  # y el chat, el texto completo
    assert consejos.revisar(t, en(23, 0, dia=3), planes=planes) == ["vuelta_tarde"]


def test_vuelo_de_regreso_no_cuenta_como_llegada(enviados):
    planes = [{"tipo": "vuelo", "titulo": "Vuelo de regreso", "fecha": "2026-11-02", "hora": "22:30"}]
    assert consejos.hora_llegada(trip(), planes) is None
    planes = [{"tipo": "vuelo", "titulo": "Vuelo a Bogotá", "fecha": "2026-11-02", "hora": "22:30"}]
    assert consejos.hora_llegada(trip(), planes) == "22:30"


def test_primera_noche_sin_planes_invita_a_preguntar(enviados):
    assert consejos.revisar(trip(), en(21, 15), planes=[]) == ["primera_noche"]


def test_idea_del_mediodia_rota_y_respeta_el_idioma(enviados):
    a = consejos.revisar(trip(idioma="en"), en(11, 30), planes=[])
    assert a == ["tip_dia"] and enviados[0][0] == "An idea for today"


def test_todos_los_textos_existen_en_los_6_idiomas():
    for kind, por_idioma in consejos.TXT.items():
        assert set(por_idioma) == {"es", "en", "pt", "fr", "de", "it"}, kind
    assert all(len(v) == 5 for v in consejos.TIPS.values()) and set(consejos.TIPS) == set(consejos.TXT["noche_plan"])
