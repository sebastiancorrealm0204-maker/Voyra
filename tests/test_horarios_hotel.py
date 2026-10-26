"""Horarios (¿abierto a esta hora?), hora de referencia del mensaje, bloque de lugares con marcas y confirmación del hotel."""
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app import context, db, horarios, places_live, visual

TZ = ZoneInfo("America/Bogota")


def _h(*periodos):
    return {"periods": [{"open": {"day": d, "hour": ho, "minute": 0}, "close": {"day": dc, "hour": hc, "minute": 0}}
                        for d, ho, dc, hc in periodos]}


def test_abierto_cerrado_y_proxima_apertura(monkeypatch):
    # Lunes (Google: 1) de 12:00 a 22:00
    monkeypatch.setattr(horarios, "datos", lambda: {"X": _h((1, 12, 1, 22))})
    lunes = lambda h, m=0: datetime(2026, 10, 26, h, m, tzinfo=TZ)          # 2026-10-26 es lunes
    assert horarios.estado("X", lunes(21, 30)) == {"abierto": True, "cierra": "22:00", "abre": None, "24h": False}
    assert horarios.etiqueta("X", lunes(21, 30)) == " [ABIERTO hasta 22:00]"
    assert horarios.estado("X", lunes(22, 30))["abierto"] is False
    assert "abre 12:00" in horarios.etiqueta("X", lunes(22, 30))
    assert horarios.esta_cerrado("X", datetime(2026, 10, 27, 13, 0, tzinfo=TZ))   # martes: no abre
    assert horarios.estado("SIN-DATO", lunes(10)) is None and horarios.etiqueta("SIN-DATO", lunes(10)) == ""


def test_periodo_que_cruza_la_medianoche_y_24_horas(monkeypatch):
    # Viernes (5) 18:00 → sábado (6) 02:00; y un lugar 24 h
    monkeypatch.setattr(horarios, "datos", lambda: {"BAR": _h((5, 18, 6, 2)),
                                                    "24": {"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]}})
    assert horarios.estado("BAR", datetime(2026, 10, 31, 1, 0, tzinfo=TZ))["abierto"]        # sábado 01:00
    assert horarios.estado("BAR", datetime(2026, 10, 30, 23, 0, tzinfo=TZ))["cierra"] == "02:00"
    assert not horarios.estado("BAR", datetime(2026, 10, 31, 3, 0, tzinfo=TZ))["abierto"]
    assert horarios.etiqueta("24", datetime(2026, 10, 28, 4, 0, tzinfo=TZ)) == " [ABIERTO 24 h]"


def test_hora_de_referencia_del_mensaje():
    ahora = datetime(2026, 10, 26, 15, 0, tzinfo=TZ)
    assert horarios.referencia({}, "dónde cenar a las 9 pm", ahora) == (datetime(2026, 10, 26, 21, 0, tzinfo=TZ), True)
    assert horarios.referencia({}, "algo colombiano mañana a las 8 pm", ahora)[0] == datetime(2026, 10, 27, 20, 0, tzinfo=TZ)
    assert horarios.referencia({}, "quiero cenar algo rico", ahora)[0].hour == 20      # «cenar» sin hora → 20:30
    assert horarios.referencia({}, "dónde almuerzo mañana", ahora)[0] == datetime(2026, 10, 27, 13, 0, tzinfo=TZ)
    assert horarios.referencia({}, "un desayuno rico", ahora)[0].hour == 8
    assert horarios.referencia({}, "qué hay de postre", ahora) == (ahora, False)


def test_los_cerrados_salen_de_las_opciones_y_quedan_marcados_en_el_indice(monkeypatch):
    lugares = [p for p in db.places_for_city("Bogotá") if p.get("place_id") and p.get("category") == "restaurante"][:40]
    cerrados = {p["place_id"] for p in lugares[:30]}                       # solo abren los lunes de 9 a 10
    monkeypatch.setattr(horarios, "datos", lambda: {pid: _h((1, 9, 1, 10)) for pid in cerrados})
    tid = db.create_trip({"ciudad": "Bogotá", "hotel": "H", "inicio": "2026-10-26", "fin": "2026-10-30", "zona_actual": "Zona T"})
    bloque = context._lugares_block(db.get_trip(tid), "dime qué hay para ver mañana a las 9 pm")
    lista = bloque.split("LUGARES CURADOS DE VOYRA")[1].split("OTROS LUGARES CURADOS")[0]
    nombres_cerrados = [p["name"] for p in lugares[:30]]
    assert not any(f"- {n} [" in lista for n in nombres_cerrados)             # ningún cerrado entre las opciones
    assert "[cerrado a esa hora]" in bloque                                     # sí quedan marcados en el índice de «otros lugares»


def test_hotel_confirmado_se_guarda_sin_adivinar(monkeypatch):
    from app.main import app
    import uuid
    client = TestClient(app)
    email = f"hotel{uuid.uuid4().hex[:8]}@example.com"
    link = client.post("/auth/signup", json={"email": email, "password": "supersecret"}).json()["dev_verification_link"]
    client.get("/auth/verify", params={"token": link.split("token=")[1]})
    token = client.post("/auth/login", json={"email": email, "password": "supersecret"}).json()["token"]
    H = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(places_live, "buscar_hoteles", lambda q, c, pais="", n=4: [
        {"place_id": "P1", "nombre": "Hotel Dann Carlton Bogotá", "direccion": "Ak 15 # 103-60", "lat": 4.69, "lng": -74.04, "rating": 4.6, "resenas": 100, "foto_name": "x"},
        {"place_id": "P2", "nombre": "Casa Dann Carlton Hotel & Spa", "direccion": "Cl 93b #19-44", "lat": 4.67, "lng": -74.05, "rating": 4.7, "resenas": 90, "foto_name": "y"}])
    monkeypatch.setattr(visual, "fotos", lambda pid, n=1, ancho=640: [{"uri": f"https://img/{pid}", "autor": "a"}])
    r = client.get("/hotels/search", params={"q": "Dann Carlton"}, headers=H).json()["hoteles"]
    assert [h["nombre"] for h in r] == ["Hotel Dann Carlton Bogotá", "Casa Dann Carlton Hotel & Spa"]
    assert r[0]["foto"]["uri"] == "https://img/P1" and "foto_name" not in r[0]
    assert client.get("/hotels/search", params={"q": "ab"}, headers=H).json() == {"hoteles": []}
    elegido = r[1]
    t = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "Dann Carlton", "inicio": "2099-07-12", "fin": "2099-07-18",
                                    "hotel_confirmado": {"place_id": elegido["place_id"], "nombre": elegido["nombre"],
                                                         "direccion": elegido["direccion"], "lat": elegido["lat"], "lng": elegido["lng"]}},
                    headers=H).json()
    trip = db.get_trip(t["trip_id"])
    assert trip["hotel"] == "Casa Dann Carlton Hotel & Spa" and trip["lat_hotel"] == 4.67 and trip["dir_hotel"] == "Cl 93b #19-44"
    assert trip["place_id_hotel"] == "P2" and "hotel_confirmado" not in trip
    assert "Cl 93b #19-44" in context.build(trip, mensaje="hola")             # el Companion ve la dirección confirmada


def _lugar(n, pid, cat="restaurante", desc="", tags=None):
    return {"name": n, "place_id": pid, "category": cat, "zona": "Zona T", "descripcion": desc, "tags": tags or [], "local": None,
            "lat": 4.668, "lng": -74.053}


def test_el_pedido_filtra_por_codigo_lo_cerrado_y_lo_que_no_encaja(monkeypatch):
    """«Cenar algo colombiano a las 9 pm»: solo entran como opción los colombianos ABIERTOS; el que cierra a las 16:00 va aparte,
    como alternativa para otro momento; y un italiano abierto no se cuela como si fuera colombiano."""
    lugares = [
        _lugar("Criolla Tarde", "A", desc="Cocina colombiana tradicional: bandeja paisa y ajiaco."),       # cierra 16:00
        _lugar("Fríjoles Noche", "B", desc="Fríjoles paisas, comida colombiana de casa."),                   # abierto hasta 02:00
        _lugar("Trattoria Roma", "C", desc="Pasta italiana artesanal."),                                     # abierto, pero italiano
    ]
    abre = lambda hasta: _h(*[(d, 12, d, hasta) for d in range(7)])
    monkeypatch.setattr(horarios, "datos", lambda: {"A": abre(16),
                                                    "B": _h(*[(d, 12, (d + 1) % 7, 2) for d in range(7)]), "C": abre(23)})
    monkeypatch.setattr(db, "places_for_city", lambda c: lugares)
    trip = {"ciudad": "Bogotá", "zona_actual": "Zona T", "planes": []}
    b = context._lugares_block(trip, "Quiero cenar algo colombiano a las 9 pm")
    cumplen, _, resto = b.partition("ENCAJAN CON EL PEDIDO PERO ESTÁN CERRADOS")
    assert "Fríjoles Noche" in cumplen.split("LUGARES CURADOS")[0] and "Trattoria Roma" not in cumplen.split("LUGARES CURADOS")[0]
    assert "Criolla Tarde" in resto.split("LUGARES CURADOS")[0] and "12:00–16:00" in resto
    lista = b.split("LUGARES CURADOS DE VOYRA")[1]
    assert "- Criolla Tarde" not in lista          # un lugar cerrado a esa hora no se ofrece como opción
    sin = context._lugares_block(trip, "Quiero cenar sushi a las 9 pm")
    assert "NINGÚN lugar curado que encaje" in sin


def test_detecta_cerrados_ofrecidos_para_ahora_pero_no_como_alternativa(monkeypatch):
    lugares = [_lugar("Criolla Tarde", "A", desc="colombiana"), _lugar("Fríjoles Noche", "B", desc="colombiana")]
    monkeypatch.setattr(horarios, "datos", lambda: {"A": _h(*[(d, 12, d, 16) for d in range(7)]),
                                                    "B": _h(*[(d, 12, d, 23) for d in range(7)])})
    ref = datetime(2026, 10, 28, 21, 0, tzinfo=TZ)
    malo = "Te propongo Criolla Tarde para cenar: bandeja paisa y ajiaco, muy cerca de ti."
    bueno = "Para cenar a las 21:00 ve a Fríjoles Noche. Criolla Tarde déjala para mañana al mediodía, a esa hora sí abre."
    assert [p["name"] for p in horarios.cerrados_mencionados(malo, lugares, ref)] == ["Criolla Tarde"]
    assert horarios.cerrados_mencionados(bueno, lugares, ref) == []


def test_si_el_modelo_ofrece_un_cerrado_se_reescribe_la_respuesta(monkeypatch):
    from app import llm
    from app.main import app
    lugares = [p for p in db.places_for_city("Bogotá") if p.get("place_id") and p.get("category") == "restaurante"][:2]
    cerrado, abierto = lugares
    monkeypatch.setattr(horarios, "datos", lambda: {cerrado["place_id"]: _h(*[(d, 12, d, 16) for d in range(7)]),
                                                    abierto["place_id"]: _h(*[(d, 12, d, 23) for d in range(7)])})
    llamadas = []

    def falso(system, history):
        llamadas.append("CORRECCIÓN OBLIGATORIA" in system)
        return (f"Ve a {abierto['name']}, abre hasta las 23:00." if llamadas[-1]
                else f"Te recomiendo {cerrado['name']} para cenar esta noche.")

    monkeypatch.setattr(llm, "chat", falso)
    import uuid
    client = TestClient(app)
    email = f"horas{uuid.uuid4().hex[:8]}@example.com"
    link = client.post("/auth/signup", json={"email": email, "password": "supersecret"}).json()["dev_verification_link"]
    client.get("/auth/verify", params={"token": link.split("token=")[1]})
    H = {"Authorization": f"Bearer {client.post('/auth/login', json={'email': email, 'password': 'supersecret'}).json()['token']}"}
    tid = client.post("/trips", json={"ciudad": "Bogotá", "hotel": "H", "inicio": "2099-07-12", "fin": "2099-07-18"}, headers=H).json()["trip_id"]
    r = client.post(f"/trips/{tid}/chat", json={"message": "Quiero cenar algo rico a las 9 pm"}, headers=H).json()
    assert llamadas == [False, True]                                   # primer borrador malo, segundo con la corrección
    assert abierto["name"] in r["reply"] and cerrado["name"] not in r["reply"]
