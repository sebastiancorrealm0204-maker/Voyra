"""Códigos de acceso por hotel: controlan quién puede crear una cuenta y de qué hotel viene cada usuario.

Cada hotel recibe un código (por ejemplo «DANN-7K4P») con un máximo de usos opcional. Con REQUIRE_ACCESS_CODE=1 nadie
puede registrarse sin un código válido; con 0 (por defecto) el código es opcional y, si lo traen, igual se anota de qué
hotel vienen. Los códigos se crean y se apagan desde los endpoints /admin/codigos (token de administrador).
"""
import os
import re
import secrets
import time

from . import config, db

_ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin 0/O ni 1/I: se leen bien en una tarjeta
_lista: set[str] = set()


def env_requerido() -> bool:
    return os.environ.get("REQUIRE_ACCESS_CODE", "0") == "1"


def requerido() -> bool:
    """El ajuste del panel manda; si no hay, la variable de entorno REQUIRE_ACCESS_CODE."""
    v = config.get("require_access_code")
    return env_requerido() if v is None else str(v).strip() in ("1", "1.0", "true")


def normalizar(codigo: str) -> str:
    """Solo letras y números en mayúscula: «dan-7k4p», «DAN 7K4P» y «DAN7K4P» son el mismo código."""
    return re.sub(r"[^A-Z0-9]", "", (codigo or "").upper())[:40]


def asegurar_tabla() -> None:
    clave = "pg" if db.IS_POSTGRES else db.DB_PATH
    if clave in _lista:
        return
    with db.conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS access_codes (code TEXT PRIMARY KEY, hotel TEXT NOT NULL, max_usos INTEGER NOT NULL, "
                  "usos INTEGER NOT NULL, activo INTEGER NOT NULL, created_at REAL NOT NULL)")
    _lista.add(clave)


def _nuevo_codigo(hotel: str) -> str:
    base = re.sub(r"[^A-Z]", "", hotel.upper())[:3] or "COM"
    return f"{base}-" + "".join(secrets.choice(_ALFABETO) for _ in range(4))   # ej. DAN-7K4P


def crear(hotel: str, max_usos: int = 0, codigo: str | None = None) -> dict:
    """max_usos 0 = sin tope. Devuelve la fila creada."""
    asegurar_tabla()
    hotel = (hotel or "").strip()[:80]
    if not hotel:
        raise ValueError("hotel")
    for _ in range(10):
        cod = (re.sub(r"[^A-Z0-9-]", "", codigo.upper())[:40] if codigo else _nuevo_codigo(hotel))
        if not normalizar(cod):
            raise ValueError("codigo")
        if codigo and any(normalizar(x["codigo"]) == normalizar(cod) for x in listar()):
            raise ValueError("codigo_existe")
        try:
            with db.conn() as c:
                c.execute("INSERT INTO access_codes (code, hotel, max_usos, usos, activo, created_at) VALUES (?,?,?,?,?,?)",
                          (cod, hotel, max(0, int(max_usos)), 0, 1, time.time()))
            return {"codigo": cod, "hotel": hotel, "max_usos": max(0, int(max_usos)), "usos": 0, "activo": True}
        except Exception:
            if codigo:
                raise ValueError("codigo_existe")
    raise ValueError("codigo_existe")


def crear_lote(hotel: str, cantidad: int, max_usos: int = 1) -> list[dict]:
    """Muchos códigos de un solo uso (por defecto) para repartir uno por huésped."""
    return [crear(hotel, max_usos) for _ in range(max(1, min(int(cantidad), 500)))]


def validar(codigo: str) -> dict | None:
    """{'codigo', 'hotel'} si el código existe, está activo y le quedan usos; None si no."""
    cod = normalizar(codigo)
    if not cod:
        return None
    asegurar_tabla()
    with db.conn() as c:
        r = c.execute("SELECT code, hotel, max_usos, usos, activo FROM access_codes WHERE REPLACE(code,'-','')=?", (cod,)).fetchone()
    if not r or not r["activo"] or (r["max_usos"] and r["usos"] >= r["max_usos"]):
        return None
    return {"codigo": r["code"], "hotel": r["hotel"]}


def consumir(codigo: str) -> dict | None:
    """Gasta un uso de forma atómica (dos registros a la vez no pasan del máximo). None si ya no se puede."""
    cod = normalizar(codigo)
    asegurar_tabla()
    with db.conn() as c:
        cur = c.execute("UPDATE access_codes SET usos=usos+1 WHERE REPLACE(code,'-','')=? AND activo=1 AND (max_usos=0 OR usos<max_usos)", (cod,))
        gastado = cur.rowcount == 1
        r = c.execute("SELECT code, hotel FROM access_codes WHERE REPLACE(code,'-','')=?", (cod,)).fetchone() if gastado else None
    return {"codigo": r["code"], "hotel": r["hotel"]} if gastado and r else None


def devolver(codigo: str) -> None:
    """Deshace un consumo (el registro falló después de gastarlo)."""
    with db.conn() as c:
        c.execute("UPDATE access_codes SET usos=usos-1 WHERE REPLACE(code,'-','')=? AND usos>0", (normalizar(codigo),))


def listar() -> list[dict]:
    asegurar_tabla()
    with db.conn() as c:
        filas = c.execute("SELECT code, hotel, max_usos, usos, activo, created_at FROM access_codes ORDER BY created_at DESC").fetchall()
    return [{"codigo": f["code"], "hotel": f["hotel"], "max_usos": f["max_usos"], "usos": f["usos"], "activo": bool(f["activo"])}
            for f in filas]


def desactivar(codigo: str) -> bool:
    asegurar_tabla()
    with db.conn() as c:
        cur = c.execute("UPDATE access_codes SET activo=0 WHERE REPLACE(code,'-','')=?", (normalizar(codigo),))
    return cur.rowcount == 1
