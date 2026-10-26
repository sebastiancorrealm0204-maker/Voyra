"""Ventanas de texto y filtros de ampliar_evidencia.py (sin llamar a Tavily)."""
import ampliar_evidencia as a


def test_ventana_alrededor_del_nombre_sin_tildes_ni_mayusculas():
    texto = "Intro larga " * 40 + "En SAUVAGE sirven un cordero al curry y pasta fresca. " + "Cierre " * 40
    v = a.ventana(texto, "Sauvage")
    assert "cordero al curry" in v and len(v) <= a.VENTANA
    assert a.ventana(texto, "Otro Lugar") == ""


def test_nombre_de_busqueda_sin_slogan_ni_parentesis():
    assert a.nombre_busqueda("Qun Bogotá | Best Restaurant in Bogotá") == "Qun Bogotá"
    assert a.nombre_busqueda("El Ovejo (Zona T)") == "El Ovejo"
    assert a.nombre_busqueda("Pacha Peruana Gastrobar, parrilla") == "Pacha Peruana Gastrobar, parrilla"


def test_solo_paginas_que_nombran_el_lugar_y_bogota_y_no_son_redes():
    res = [
        {"url": "https://www.eltiempo.com/sauvage", "title": "Sauvage", "raw_content": "Sauvage, en Chapinero, Bogotá, sirve cordero al curry."},
        {"url": "https://www.instagram.com/sauvage", "title": "", "raw_content": "Sauvage Bogotá reel"},               # red social
        {"url": "https://otra.com/x", "title": "Sauvage", "raw_content": "Sauvage es un vino francés."},               # sin Bogotá
        {"url": "https://www.eltiempo.com/otra", "title": "", "raw_content": "Sauvage Bogotá otra vez"},              # mismo dominio
    ]
    out = a.fragmentos_amplios(res, "Sauvage", "Chapinero")
    assert [f["dominio"] for f in out] == ["eltiempo.com"] and "cordero" in out[0]["texto"]
