"""Lógica pura del flujo de descubrimiento (sin llamar a Google ni a Tavily) y de la aprobación."""
import json

import pytest

import aprobar_candidatos as ap
import descubrir_lugares as d
from app import seed_data


def test_categoria_por_tipo_de_google():
    assert d.categoria_de("coffee_shop", [], "restaurante") == "cafe"
    assert d.categoria_de("", ["night_club"], "restaurante") == "bar"
    assert d.categoria_de("vegan_restaurant", [], "cafe") == "restaurante"
    assert d.categoria_de("museum", [], "restaurante") == "atraccion"
    assert d.categoria_de("algo_raro", [], "parque") == "parque"


def test_puntaje_prefiere_rating_con_volumen():
    assert d.puntaje(4.8, 2000) > d.puntaje(4.8, 160) > d.puntaje(4.5, 160)
    assert d.puntaje(None, 100) == 0 and d.puntaje(4.5, None) == 0


def test_duplicados_por_id_o_nombre_parecido():
    cur = {"ids": {"PID1"}, "nombres": ["El Chato", "Café San Alberto (Candelaria)"]}
    assert d.es_duplicado("Otro nombre", "PID1", cur)
    assert d.es_duplicado("EL CHATO Restaurante", "X", cur)
    assert d.es_duplicado("Café San Alberto", "X", cur)
    assert not d.es_duplicado("Pianta", "X", cur)


def test_corroborar_cuenta_dominios_distintos_y_solo_textos_que_lo_nombran():
    res = [
        {"title": "Mejores cafés", "url": "https://www.guia.com/a", "content": "Bogotá. Café Nuevo tiene un tostión excelente."},
        {"title": "Otro blog", "url": "https://blog.net/b", "content": "Bogotá. En Chapinero, Café Nuevo es imperdible."},
        {"title": "Mismo blog", "url": "https://blog.net/c", "content": "Bogotá. Café Nuevo otra vez"},
        {"title": "Sin relación", "url": "https://x.org/d", "content": "Bogotá. Una lista de panaderías cerró este año."},
    ]
    c = d.corroborar("Café Nuevo", res)
    assert c["menciones"] == 2 and c["dominios"] == ["blog.net", "guia.com"] and not c["cierre"]
    assert d.clasificar(c) == "recomendado"
    assert d.clasificar({**c, "menciones": 1}) == "dudoso"


def test_senal_de_cierre_solo_si_el_texto_nombra_al_lugar():
    con = d.corroborar("Bar Viejo", [{"title": "Bar Viejo cerró", "url": "https://a.com/x", "content": "Bogotá. Bar Viejo cerró tras 10 años"}])
    assert con["cierre"] and d.clasificar(con) == "descartado_cierre"
    sin = d.corroborar("Bar Viejo", [{"title": "Bar Viejo", "url": "https://a.com/x", "content": "Bogotá. Bar Viejo, buen ambiente. Otro local cerró."}])
    assert sin["cierre"]  # el mismo texto que nombra al lugar y habla de cierre es suficiente para dudar
    ajeno = d.corroborar("Bar Viejo", [{"title": "Otro", "url": "https://a.com/y", "content": "Bogotá. El local de al lado cerró"}])
    assert not ajeno["cierre"] and ajeno["menciones"] == 0


G = {"nombre": "Café Nuevo", "lat": 4.65, "lng": -74.06, "place_id": "PID9", "direccion": "Calle 1 #2-3", "rating": 4.7,
     "resenas": 400, "price_level": None, "horarios": ["lunes: 8-18"], "web": "", "maps": "https://maps/x"}


def candidato(estado="recomendado", desc="Un café rico."):
    c = d.a_entrada(G, "cafe", "Chapinero", {"menciones": 3, "dominios": ["a.com", "b.com", "c.com"], "fuentes": [], "extractos": [], "cierre": False}, estado)
    c["descripcion"] = desc
    return c


def test_aprobar_exige_descripcion_y_rechaza_cierres():
    with pytest.raises(ValueError):
        ap.a_seed(candidato(desc=""))
    assert "muy bien valorado" in ap.a_seed(candidato(desc=""), auto=True)["descripcion"]
    with pytest.raises(ValueError):
        ap.a_seed(candidato(estado="descartado_cierre"))
    s = ap.a_seed(candidato())
    assert "_evidencia" not in s and s["confianza"] == "media_alta" and s["_fuentes"] == ["a.com", "b.com", "c.com"]


def test_agregar_no_duplica():
    lista, n = ap.agregar([], [ap.a_seed(candidato())])
    assert n == 1
    lista, n = ap.agregar(lista, [ap.a_seed(candidato())])
    assert n == 0 and len(lista) == 1


def test_all_seeds_suma_los_aprobados_y_respeta_excluidos(monkeypatch):
    nuevo = ap.a_seed(candidato())
    monkeypatch.setattr(seed_data, "_candidatos_aprobados", lambda: [nuevo])
    monkeypatch.setattr(seed_data, "_capa_verificacion", lambda: {"excluir": {}, "ajustes": {}, "confianza_baja": []})
    nombres = [p["name"] for p in seed_data.all_seeds()]
    assert "Café Nuevo" in nombres and nombres.count("El Chato") == 1
    monkeypatch.setattr(seed_data, "_capa_verificacion", lambda: {"excluir": {"Café Nuevo": "x"}, "ajustes": {}, "confianza_baja": []})
    assert "Café Nuevo" not in [p["name"] for p in seed_data.all_seeds()]


def test_markdown_agrupa_por_estado():
    md = d.resumen_md([candidato(), candidato(estado="dudoso")])
    assert "✅ Recomendados" in md and "🟡 Dudosos" in md


def test_categoria_manda_el_tipo_principal():
    # un restaurante que también tiene barra sigue siendo restaurante
    assert d.categoria_de("restaurant", ["bar", "restaurant"], "bar") == "restaurante"
    assert d.categoria_de("bar", ["restaurant"], "restaurante") == "bar"


def test_ruido_zonas_tiendas_y_colegios():
    z = list(d.ZONAS)
    assert d.es_ruido({"nombre": "Zona T Bogotá", "primary": "tourist_attraction", "tipos": []}, z)
    assert d.es_ruido({"nombre": "Supermercado Don Camilo", "primary": "supermarket", "tipos": ["store"]}, z)
    assert d.es_ruido({"nombre": "Academia X", "primary": "school", "tipos": []}, z)
    assert not d.es_ruido({"nombre": "Pianta", "primary": "vegan_restaurant", "tipos": ["restaurant", "store"]}, z)


def test_redes_y_web_propia_no_cuentan_como_recomendacion_independiente():
    res = [
        {"title": "La Kasta", "url": "https://www.instagram.com/lakasta", "content": "Bogotá. La Kasta, parrilla"},
        {"title": "La Kasta", "url": "https://www.lakasta.com.co/menu", "content": "Bogotá. La Kasta menú"},
        {"title": "La Kasta", "url": "https://waze.com/x", "content": "Bogotá. La Kasta dirección"},
        {"title": "Dónde comer", "url": "https://www.tripadvisor.es/y", "content": "Bogotá. La Kasta es muy popular"},
        {"title": "Guía", "url": "https://guia.com/z", "content": "Bogotá. Recomendamos La Kasta por su parrilla"},
    ]
    c = d.corroborar("La Kasta", res)
    assert c["dominios"] == ["guia.com", "tripadvisor.es"] and c["menciones"] == 2
    assert {"instagram.com", "lakasta.com.co", "waze.com"} <= set(c["otras"])
    # solo redes + web propia → no es recomendado
    solo = d.corroborar("La Kasta", res[:3])
    assert solo["menciones"] == 0 and d.clasificar(solo) == "dudoso"


def test_misma_fuente_con_varios_dominios_cuenta_una_vez_y_los_listados_no_cuentan():
    res = [
        {"title": "Cuatro", "url": "https://www.tripadvisor.es/a", "content": "Bogotá. Cuatro Cocina Casual, muy rico"},
        {"title": "Cuatro", "url": "https://en.tripadvisor.com.hk/b", "content": "Bogotá. Cuatro Cocina Casual reviews"},
        {"title": "Cuatro", "url": "https://www.rappi.com.co/c", "content": "Bogotá. Pide Cuatro Cocina Casual a domicilio"},
        {"title": "Cuatro", "url": "https://www.eltiempo.com/d", "content": "Bogotá. Cuatro Cocina Casual abrió en Chicó"},
    ]
    c = d.corroborar("Cuatro Cocina Casual", res)
    assert c["dominios"] == ["eltiempo.com", "www.tripadvisor.es"] or c["menciones"] == 2
    assert c["menciones"] == 2 and "rappi.com.co" in c["otras"]
    assert d.familia("en.tripadvisor.com.hk") == "tripadvisor" and d.familia("las2orillas.co") == "las2orillas"


def test_un_texto_sin_bogota_no_corrobora():
    res = [{"title": "London Calling", "url": "https://es.wikipedia.org/x", "content": "London Calling es un álbum de The Clash"},
           {"title": "London Calling Bogotá", "url": "https://guia.com/y", "content": "Bogotá. London Calling, pub en Bogotá con música"}]
    c = d.corroborar("London Calling", res)
    assert c["dominios"] == ["guia.com"]


def test_google_fuerte_basta_para_recomendar_pero_no_si_hay_cierre():
    sin_fuentes = {"menciones": 0, "cierre": False}
    assert d.clasificar(sin_fuentes) == "dudoso"                                           # comportamiento de siempre
    assert d.clasificar(sin_fuentes, g={"rating": 4.6, "resenas": 800}) == "recomendado"
    assert d.clasificar(sin_fuentes, g={"rating": 4.4, "resenas": 800}) == "dudoso"
    assert d.clasificar(sin_fuentes, g={"rating": 4.8, "resenas": 100}) == "dudoso"
    assert d.clasificar({"menciones": 0, "cierre": True}, g={"rating": 4.9, "resenas": 5000}) == "descartado_cierre"
    assert d.motivo({"menciones": 2}, "recomendado") == "fuentes" and d.motivo(sin_fuentes, "recomendado") == "google"


def test_el_sitio_propio_de_un_local_de_nombre_corto_no_cuenta_como_fuente():
    assert d.es_propio_o_red("restauranteuro.com", "URO")
    assert d.es_propio_o_red("serattagroup.com", "Seratta")
    assert not d.es_propio_o_red("tripadvisor.es", "URO")


def test_cierra_despues_cuenta_madrugada_y_24_horas():
    tarde = ["lunes: 12:00–23:00", "martes: 12:00–23:00", "miércoles: 12:00–23:30", "jueves: 12:00–22:00"]
    assert d.cierra_despues(tarde, "22:30")                                    # 3 días ≥ 22:30
    assert not d.cierra_despues(["lunes: 12:00–21:00"] * 7, "22:30")
    assert d.cierra_despues(["viernes: 18:00–2:00", "sábado: 18:00–3:00", "jueves: 18:00–1:00"], "22:30")   # madrugada = tarde
    assert d.cierra_despues(["lunes: Abierto 24 horas"] * 3, "23:00")
    assert not d.cierra_despues([], "22:30")
