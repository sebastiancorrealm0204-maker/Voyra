"""Las notificaciones son un gancho corto que lleva directo a la pantalla útil (el texto completo vive en el chat)."""
from app import consejos, engine, push


def test_acortar_respeta_frases_y_palabras():
    assert push.acortar("Sal ya al tour. Monserrate empieza en 60 min y hay fila.", 40) == "Sal ya al tour."
    largo = "Ya debes estar en tierra, te ayudo a llegar al hotel con taxi o transporte público ahora mismo"
    r = push.acortar(largo, push.MAX_CUERPO)
    assert len(r) <= push.MAX_CUERPO and r.endswith("…") and not r[:-1].endswith(" ")
    assert push.acortar("Corto", 72) == "Corto"


def test_todos_los_consejos_tienen_push_corto_en_todos_los_idiomas():
    for kind in consejos.ABRIR:
        if kind == "tip_dia":
            continue
        assert set(consejos.PUSH[kind]) == {"es", "en", "pt", "fr", "de", "it"}
        for t, b in consejos.PUSH[kind].values():
            assert len(t) <= push.MAX_TITULO and len(b) <= push.MAX_CUERPO, (kind, t, b)


def test_consejo_envia_gancho_y_destino(monkeypatch):
    enviados = []
    monkeypatch.setattr(consejos.db, "mark_proactive_sent", lambda *a: True)
    monkeypatch.setattr(consejos.db, "insert", lambda *a, **k: None)
    monkeypatch.setattr(push, "send_to_trip_async", lambda tid, t, b, data=None: enviados.append((t, b, data)))
    trip = {"id": "t1", "ciudad": "Bogotá", "idioma": "es"}
    assert consejos._enviar(trip, "consejo_aterrizaje_dia", "2026-01-01", *consejos._texto("aterrizaje_dia", trip), corto="aterrizaje_dia")
    t, b, data = enviados[0]
    assert t == "👋 Bienvenido a Bogotá" and len(b) <= push.MAX_CUERPO
    assert data["open"] == "como_llegar" and data["dest"] == "hotel"


def test_toque_de_la_notificacion():
    assert engine._destino_toque("vuelo", None, None) == ("planes", None)
    assert engine._destino_toque("recomendacion", "chat", {"place_name": "Museo del Oro"}) == ("como_llegar", "Museo del Oro")
    assert engine._destino_toque("seguridad", "como_llegar", None) == ("chat", None)  # el modelo no puede inventar un destino
    assert engine._destino_toque("clima", "planes", None) == ("planes", None)
