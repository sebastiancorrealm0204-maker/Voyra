"""Lógica pura de reunir_local.py: resumen de horario, campo `practico` y selección de fragmentos."""
import reunir_local as r

SEMANA = ["lunes: 12:00–22:00", "martes: 12:00–22:00", "miércoles: 12:00–22:00", "jueves: 12:00–22:00",
          "viernes: 12:00–23:00", "sábado: 12:00–23:00", "domingo: Cerrado"]


def test_horario_agrupa_dias_contiguos_iguales():
    assert r.resumir_horario(SEMANA) == "lun–jue 12:00–22:00; vie–sáb 12:00–23:00; dom cerrado"


def test_horario_todos_los_dias_y_un_solo_dia():
    todos = [f"{d}: 8:00–19:00" for d in r.DIAS]
    assert r.resumir_horario(todos) == "lun–dom 8:00–19:00"
    assert r.resumir_horario(["lunes: Cerrado"]) == "lun cerrado"


def test_horario_vacio_o_raro():
    assert r.resumir_horario([]) == "" and r.resumir_horario(None) == ""
    assert r.resumir_horario(["algo sin formato"]) == ""


def test_horario_con_espacios_finos_de_google():
    assert r.resumir_horario(["lunes: 8:00 a. m.–7:00 p. m."]) == "lun 8:00 a. m.–7:00 p. m."


def test_practico_solo_hechos_de_google():
    assert r.practico_de({"precio": "precio medio", "horario": "lun–dom 8:00–19:00"}) == \
        "Precio medio. horario según Google: lun–dom 8:00–19:00."
    assert r.practico_de({}) == ""
    assert r.practico_de({"horario": "lun cerrado"}) == "horario según Google: lun cerrado."


def test_fragmentos_exigen_nombre_y_bogota_y_priorizan_pistas():
    res = [
        {"title": "Pino Negro", "url": "https://a.com/1", "content": "Pino Negro en Bogotá, un sitio más."},
        {"title": "Pino Negro Bogotá", "url": "https://b.com/2", "content": "En Pino Negro pide el buffet; reserva y llega temprano. Bogotá"},
        {"title": "Pino Negro", "url": "https://c.com/3", "content": "Un árbol llamado pino negro en Canadá"},
        {"title": "Otro", "url": "https://d.com/4", "content": "Café Bogotá sin relación"},
    ]
    f = r.mejores_fragmentos("Pino Negro", res)
    assert [x["url"] for x in f] == ["https://b.com/2", "https://a.com/1"]
    assert f[0]["pistas"] > f[1]["pistas"]
