"""Parseo y aplicación de las descripciones redactadas (sin llamar al modelo)."""
import json

import redactar_descripciones as r

LOTE = [
    {"name": "Pasaje Rivas", "category": "mercado", "zona": "La Candelaria", "dir": "Cra. 10", "rating": 4.5, "tags": [],
     "descripcion": "", "_evidencia": {"estado": "recomendado", "resenas": 16500, "fuentes": [{"dominio": "visitbogota.co", "titulo": "Pasaje Rivas"}],
                                         "extractos": ["Inaugurado en 1893, el primer centro comercial de Bogotá."]}},
    {"name": "Otro", "category": "parque", "zona": "Bogotá", "dir": "", "rating": 4.6, "tags": ["Joyas escondidas"],
     "descripcion": "", "_evidencia": {"estado": "recomendado", "resenas": 100}},
]


def test_el_contexto_trae_solo_datos_reales():
    c = r.contexto(LOTE[0])
    assert "Pasaje Rivas" in c and "16500" in c and "1893" in c and "visitbogota.co" in c


def test_parsea_filtra_nombres_ajenos_tags_invalidos_y_textos_vacios():
    raw = "```json\n" + json.dumps({"lugares": [
        {"nombre": "Pasaje Rivas", "descripcion": "Primer centro comercial de Bogotá, inaugurado en 1893. Plan corto en La Candelaria.",
         "tags": ["Historia y cultura", "Inventado", "Mercados y artesanías", "Arquitectura", "Compras"], "poca_informacion": False},
        {"nombre": "No Está En El Lote", "descripcion": "Texto largo que debería ignorarse por completo."},
        {"nombre": "Otro", "descripcion": "corta"},                                   # demasiado corta
    ]}) + "\n```"
    out = r.parsear(raw, LOTE)
    assert list(out) == ["Pasaje Rivas"]
    assert out["Pasaje Rivas"]["tags"] == ["Historia y cultura", "Mercados y artesanías", "Arquitectura"]   # sin inventados, máx. 3
    assert r.parsear("no es json", LOTE) == {}


def test_aplicar_conserva_tags_previos_y_marca_el_origen():
    e = LOTE[1]
    r.aplicar(e, {"descripcion": "Parque tranquilo de barrio.", "tags": ["Planes tranquilos"], "poca_informacion": True})
    assert e["tags"] == ["Joyas escondidas", "Planes tranquilos"]
    assert e["_evidencia"]["descripcion_ia"] and e["_evidencia"]["poca_informacion"]
    assert "poca información" in r.resumen_md([e], "x")


def test_enriquecimiento_solo_usa_paginas_que_hablan_del_lugar():
    res = [
        {"url": "https://www.visitbogota.co/pasaje-rivas", "raw_content": "El Pasaje Rivas, en el centro de Bogotá, abrió en 1893 y hoy tiene 40 locales.", "title": "Pasaje Rivas"},
        {"url": "https://www.instagram.com/reel/x", "raw_content": "Pasaje Rivas Bogotá reel", "title": ""},          # red social: no
        {"url": "https://otra.com/rivas", "raw_content": "Un pasaje cualquiera en Lima.", "title": "Rivas"},                # otro lugar
        {"url": "https://visitbogota.co/otra-pagina", "raw_content": "Pasaje Rivas Bogotá repetido", "title": ""},        # mismo dominio: una sola
    ]
    out = r.texto_enriquecido(res, "Pasaje Rivas", "La Candelaria")
    assert [x["dominio"] for x in out] == ["visitbogota.co"] and "1893" in out[0]["texto"]
    e = {"name": "Pasaje Rivas", "category": "mercado", "zona": "La Candelaria", "dir": "", "rating": 4.5,
         "_evidencia": {"resenas": 1, "texto_fuentes": out}}
    assert "Texto de la página (visitbogota.co)" in r.contexto(e)
