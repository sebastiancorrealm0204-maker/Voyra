"""Ciudades donde el Companion está abierto.

El lanzamiento es solo en Bogotá: es donde está la red local curada completa.
Abrir otra ciudad es una variable de entorno, sin tocar código:
    CIUDADES_ACTIVAS="Bogotá,Guadalajara"
La comparación usa db.norm_city, así que 'bogota', 'Bogotá, Colombia' o 'BOG'
cuentan como Bogotá.
"""
import os

from . import db

ACTIVAS = [c.strip() for c in os.environ.get("CIUDADES_ACTIVAS", "Bogotá").split(",") if c.strip()]


def activa(ciudad: str | None) -> bool:
    if not ciudad or not ciudad.strip():
        return False
    n = db.norm_city(ciudad)
    return any(db.norm_city(a) == n for a in ACTIVAS)


def mensaje_no_disponible(ciudad: str) -> str:
    lista = " y ".join(ACTIVAS)
    return (f"Por ahora Companion solo está disponible en {lista}. "
            f"Todavía no llegamos a {ciudad.strip()}, pero estamos en ello.")
