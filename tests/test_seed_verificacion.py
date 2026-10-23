"""La capa app/seed_verificacion.json (generada por aplicar_verificacion.py) sobre la curación."""
import json
import os

from app import seed_data


def test_la_capa_excluye_y_ajusta(monkeypatch):
    capa = {"excluir": {"Criterión": "x"}, "confianza_baja": ["Di Lucca"],
            "ajustes": {"El Chato": {"lat": 1.5, "lng": -2.5, "place_id": "PID", "rating": 4.4}}}
    monkeypatch.setattr(seed_data, "_capa_verificacion", lambda: capa)
    seeds = {p["name"]: p for p in seed_data.all_seeds()}
    assert "Criterión" not in seeds
    assert seeds["El Chato"]["lat"] == 1.5 and seeds["El Chato"]["place_id"] == "PID" and seeds["El Chato"]["rating"] == 4.4
    assert seeds["Di Lucca"]["confianza"] == "baja"


def test_sin_archivo_la_curacion_queda_igual(monkeypatch):
    monkeypatch.setattr(seed_data, "_capa_verificacion", lambda: {"excluir": {}, "ajustes": {}, "confianza_baja": []})
    monkeypatch.setattr(seed_data, "_candidatos_aprobados", lambda: [])
    assert len(seed_data.all_seeds()) == len(seed_data.BOGOTA) + len(seed_data.GUADALAJARA)


def test_la_capa_real_es_coherente():
    ruta = os.path.join(os.path.dirname(seed_data.__file__), "seed_verificacion.json")
    capa = json.load(open(ruta, encoding="utf-8"))
    nombres = {p["name"] for p in seed_data.all_seeds()}
    assert not (set(capa["excluir"]) & nombres), "un lugar excluido sigue en la curación"
    for n, a in capa["ajustes"].items():
        assert a.get("place_id", "x"), n  # si trae place_id, no puede venir vacío (las entradas «varias sedes» no lo llevan)
        assert 4.4 < a["lat"] < 5.4 and -74.4 < a["lng"] < -73.5, f"{n}: coordenadas fuera de Bogotá y la Sabana"


def test_los_lugares_aprobados_traen_bloque_local_coherente():
    """Cada lugar de la capa de aprobados debe llevar `local` con clave y practico (lo que usa el Companion)."""
    import json
    import os
    ruta = os.path.join(os.path.dirname(seed_data.__file__), "seed_candidatos.json")
    permitidos = {"clave", "pedir", "ver", "momento", "ojo", "dato", "practico"}
    for p in json.load(open(ruta, encoding="utf-8")):
        loc = p.get("local")
        assert loc and loc.get("clave") and loc.get("practico"), p["name"]
        assert set(loc) <= permitidos, (p["name"], set(loc) - permitidos)
        assert all(isinstance(v, str) and v.strip() for v in loc.values()), p["name"]
