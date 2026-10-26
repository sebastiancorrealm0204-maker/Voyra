"""Plan y categorías de descubrir_cultura.py (sin llamar a Google ni a Tavily)."""
import descubrir_cultura as c


def test_el_plan_cubre_ciudad_y_pueblos_o_solo_un_grupo():
    todo = c.plan(None)
    grupos = {g for g, *_ in todo}
    assert grupos == {"museos", "cultura", "naturaleza", "mercados", "escapadas"}
    assert {x[5] for x in todo if x[0] == "escapadas"} == set(c.PUEBLOS)
    assert all(x[3] == "excursion" for x in todo if x[0] == "escapadas")
    solo = c.plan("mercados")
    assert {g for g, *_ in solo} == {"mercados"} and all(x[3] == "mercado" for x in solo)


def test_categoria_por_tipo_nombre_y_consulta():
    assert c.categoria({"nombre": "Mirador de La Calera", "primary": "tourist_attraction", "tipos": []}, "parque") == "mirador"
    assert c.categoria({"nombre": "Museo X", "primary": "museum", "tipos": []}, "parque") == "atraccion"
    assert c.categoria({"nombre": "Reserva Y", "primary": "nature_preserve", "tipos": ["nature_preserve"]}, "atraccion") == "parque"
    assert c.categoria({"nombre": "Algo", "primary": "", "tipos": []}, "mercado") == "mercado"
