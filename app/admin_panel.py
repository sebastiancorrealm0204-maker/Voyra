"""Panel de administración dentro de la app: resumen, usuarios y sus límites, códigos de acceso, ajustes y feedback.

Entra el dueño con su propio usuario y contraseña (su correo va en ADMIN_EMAILS), o con el token de entorno. Todo se
guarda en la misma base, que es Supabase en producción: lo que se cambia aquí se ve en el Table Editor y viceversa.
"""
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import accesos, config, db, feedback_limite, limits, waitlist

# Costo aproximado por uso (USD), solo para dar una idea en el resumen. Ver el cálculo de costos en la conversación.
COSTO_UNITARIO = {"chat": 0.006, "maps": 0.035, "document": 0.02, "scan": 0.03}

# Ajustes editables: clave → (etiqueta, tipo, valor por defecto del código). El valor efectivo sale de config o del default.
def _ajustes() -> list[dict]:
    f = limits.FREE_LIMITS
    t = limits.TOTAL_LIMITS
    return [
        {"key": "limit.chat", "grupo": "Límite diario por cuenta", "etiqueta": "Mensajes de chat al día", "tipo": "int", "default": f["chat"]},
        {"key": "limit.maps", "grupo": "Límite diario por cuenta", "etiqueta": "Búsquedas de Maps al día (incluye Cómo llegar)", "tipo": "int", "default": f["maps"]},
        {"key": "limit.document", "grupo": "Límite diario por cuenta", "etiqueta": "Documentos al día", "tipo": "int", "default": f["document"]},
        {"key": "limit.scan", "grupo": "Límite diario por cuenta", "etiqueta": "Escaneos al día", "tipo": "int", "default": f["scan"]},
        {"key": "total.chat", "grupo": "Tope total por cuenta (etapa de pruebas)", "etiqueta": "Mensajes de chat en total", "tipo": "int", "default": t["chat"]},
        {"key": "total.maps", "grupo": "Tope total por cuenta (etapa de pruebas)", "etiqueta": "Búsquedas de Maps en total", "tipo": "int", "default": t["maps"]},
        {"key": "total.document", "grupo": "Tope total por cuenta (etapa de pruebas)", "etiqueta": "Documentos en total", "tipo": "int", "default": t["document"]},
        {"key": "total.scan", "grupo": "Tope total por cuenta (etapa de pruebas)", "etiqueta": "Escaneos en total", "tipo": "int", "default": t["scan"]},
        {"key": "trips_max", "grupo": "Viajes", "etiqueta": "Viajes que puede crear cada cuenta", "tipo": "int", "default": limits.MAX_TRIPS_PER_USER},
        {"key": "bonus_factor", "grupo": "Ampliación por feedback", "etiqueta": "Cuánto se amplía el recurso agotado (0.5 = la mitad del tope diario)", "tipo": "float", "default": feedback_limite.BONUS_FACTOR},
        {"key": "bonus_max_dias", "grupo": "Ampliación por feedback", "etiqueta": "Días máximos con ampliación por cuenta", "tipo": "int", "default": feedback_limite.BONUS_MAX_DIAS},
        {"key": "bonus_trips_max", "grupo": "Ampliación por feedback", "etiqueta": "Viajes extra máximos por feedback", "tipo": "int", "default": feedback_limite.BONUS_TRIPS_MAX},
        {"key": "require_access_code", "grupo": "Registro", "etiqueta": "Exigir código de acceso para registrarse (1 = sí, 0 = no)", "tipo": "bool", "default": 1 if accesos.env_requerido() else 0},
    ]


class LimitesIn(BaseModel):
    limit_chat: int | None = Field(None, ge=0, le=100000)
    limit_maps: int | None = Field(None, ge=0, le=100000)
    limit_document: int | None = Field(None, ge=0, le=100000)
    limit_scan: int | None = Field(None, ge=0, le=100000)
    limit_trips: int | None = Field(None, ge=0, le=1000)


class SuspenderIn(BaseModel):
    suspendido: bool


class ConfigIn(BaseModel):
    key: str = Field(..., max_length=40)
    value: str | None = Field(None, max_length=20)   # None = volver al default


def _hoy() -> str:
    return limits._today_key()


def _uso(c, user_id: str) -> dict:
    hoy = _hoy()
    out = {}
    for res in limits.FREE_LIMITS:
        usado_hoy = c.execute("SELECT COUNT(*) n FROM usage_log WHERE user_id=? AND resource=? AND day=?", (user_id, res, hoy)).fetchone()["n"]
        total = c.execute("SELECT COUNT(*) n FROM usage_log WHERE user_id=? AND resource=?", (user_id, res)).fetchone()["n"]
        out[res] = {"hoy": usado_hoy, "total": total}
    return out


def _fila_usuario(c, u: dict) -> dict:
    return {
        "id": u["id"], "email": u["email"], "nombre": u.get("nombre") or "", "telefono": u.get("telefono") or "",
        "pais": u.get("pais") or "", "plan": u.get("plan") or "free", "hotel": u.get("hotel_origen") or "",
        "codigo": u.get("codigo_acceso") or "", "suspendido": bool(u.get("suspendido")),
        "creado": u.get("created_at"),
        "viajes_creados": limits.trips_count(u["id"]),
    }


def registrar(app, current_user, admin_any):
    """Monta las rutas /admin/* (con `admin_any`: token de entorno o usuario admin)."""
    r = APIRouter(prefix="/admin", dependencies=[Depends(admin_any)])

    @r.get("/resumen")
    def resumen():
        feedback_limite.asegurar_tablas()
        hoy = _hoy()
        desde7 = time.time() - 7 * 86400
        with db.conn() as c:
            n_users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            nuevos7 = c.execute("SELECT COUNT(*) n FROM users WHERE created_at>?", (desde7,)).fetchone()["n"]
            n_trips = c.execute("SELECT COUNT(*) n FROM trips").fetchone()["n"]
            activos_hoy = c.execute("SELECT COUNT(DISTINCT user_id) n FROM usage_log WHERE day=?", (hoy,)).fetchone()["n"]
            uso_hoy = {res: c.execute("SELECT COUNT(*) n FROM usage_log WHERE resource=? AND day=?", (res, hoy)).fetchone()["n"] for res in limits.FREE_LIMITS}
            uso_total = {res: c.execute("SELECT COUNT(*) n FROM usage_log WHERE resource=?", (res,)).fetchone()["n"] for res in limits.FREE_LIMITS}
            fb = c.execute("SELECT gusta, COUNT(*) n FROM feedback_limite GROUP BY gusta").fetchall()
        return {
            "usuarios": n_users, "nuevos_7d": nuevos7, "viajes": n_trips, "activos_hoy": activos_hoy,
            "uso_hoy": uso_hoy, "uso_total": uso_total,
            "costo_estimado_hoy": round(sum(uso_hoy[k] * COSTO_UNITARIO[k] for k in uso_hoy), 2),
            "costo_estimado_total": round(sum(uso_total[k] * COSTO_UNITARIO[k] for k in uso_total), 2),
            "lista_espera": waitlist.count(fresh=True),
            "feedback": {"me_gusta": next((x["n"] for x in fb if x["gusta"]), 0), "no_me_gusta": next((x["n"] for x in fb if not x["gusta"]), 0)},
            "codigos": {"total": len(accesos.listar()), "usos": sum(x["usos"] for x in accesos.listar())},
            "maps_global_hoy": limits.global_used_today("maps"), "maps_global_tope": limits.GLOBAL_MAPS_PER_DAY,
        }

    @r.get("/usuarios")
    def usuarios(q: str = Query("", max_length=80), limite: int = Query(50, ge=1, le=200), desde: int = Query(0, ge=0)):
        with db.conn() as c:
            if q.strip():
                like = f"%{q.strip().lower()}%"
                filas = c.execute("SELECT * FROM users WHERE LOWER(email) LIKE ? OR LOWER(COALESCE(nombre,'')) LIKE ? OR COALESCE(telefono,'') LIKE ? "
                                  "OR LOWER(COALESCE(hotel_origen,'')) LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                  (like, like, like, like, limite, desde)).fetchall()
            else:
                filas = c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (limite, desde)).fetchall()
            out = []
            for f in filas:
                u = dict(f)
                fila = _fila_usuario(c, u)
                fila["uso"] = _uso(c, u["id"])
                out.append(fila)
        return {"usuarios": out}

    @r.get("/usuarios/{uid}")
    def usuario(uid: str):
        with db.conn() as c:
            f = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not f:
                raise HTTPException(404, "Usuario no existe.")
            u = dict(f)
            fila = _fila_usuario(c, u)
            fila["uso"] = _uso(c, uid)
            fila["overrides"] = {k: u.get(k) for k in ("limit_chat", "limit_maps", "limit_document", "limit_scan", "limit_trips")}
        fila["limites_efectivos"] = {res: limits.effective_limit(uid, res) for res in limits.FREE_LIMITS}
        fila["limite_viajes"] = limits.effective_trip_limit(uid)
        return fila

    @r.patch("/usuarios/{uid}/limites")
    def cambiar_limites(uid: str, b: LimitesIn):
        """Pone (o quita, con null) el límite propio de esta cuenta. Lo que no venga en el cuerpo no se toca."""
        cambios = {k: getattr(b, k) for k in b.model_fields_set}
        if not cambios:
            raise HTTPException(422, "Nada que cambiar.")
        with db.conn() as c:
            if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                raise HTTPException(404, "Usuario no existe.")
            for col, v in cambios.items():
                c.execute(f"UPDATE users SET {col}=? WHERE id=?", (v, uid))
        return {"ok": True}

    @r.post("/usuarios/{uid}/suspender")
    def suspender(uid: str, b: SuspenderIn):
        with db.conn() as c:
            cur = c.execute("UPDATE users SET suspendido=? WHERE id=?", (1 if b.suspendido else 0, uid))
            if b.suspendido:
                c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))   # cierra sus sesiones al instante
        if cur.rowcount != 1:
            raise HTTPException(404, "Usuario no existe.")
        return {"ok": True}

    @r.post("/usuarios/{uid}/reiniciar-hoy")
    def reiniciar_hoy(uid: str):
        """Borra el uso de HOY de la cuenta (le devuelve los cupos diarios). No toca el total de la etapa."""
        with db.conn() as c:
            c.execute("DELETE FROM usage_log WHERE user_id=? AND day=?", (uid, _hoy()))
        return {"ok": True}

    @r.get("/config")
    def ver_config():
        cfg = config._todo()
        out = []
        for a in _ajustes():
            override = a["key"] in cfg
            out.append({**a, "valor": cfg[a["key"]] if override else a["default"], "personalizado": override})
        return {"ajustes": out}

    @r.put("/config")
    def cambiar_config(b: ConfigIn):
        claves = {a["key"]: a for a in _ajustes()}
        if b.key not in claves:
            raise HTTPException(422, "Ajuste desconocido.")
        if b.value is not None:
            try:
                v = float(b.value)
            except ValueError:
                raise HTTPException(422, "El valor debe ser un número.")
            if v < 0:
                raise HTTPException(422, "El valor no puede ser negativo.")
            if claves[b.key]["tipo"] == "bool" and v not in (0, 1):
                raise HTTPException(422, "Usa 1 (sí) o 0 (no).")
        config.set_(b.key, None if b.value is None else b.value)
        return {"ok": True}

    @r.get("/feedback")
    def feedback(limite: int = Query(100, ge=1, le=500)):
        feedback_limite.asegurar_tablas()
        with db.conn() as c:
            filas = c.execute("SELECT f.recurso, f.gusta, f.motivo, f.day, f.created_at, u.email, u.hotel_origen FROM feedback_limite f "
                              "LEFT JOIN users u ON u.id = f.user_id ORDER BY f.created_at DESC LIMIT ?", (limite,)).fetchall()
            encuestas = c.execute("SELECT e.ciudad, e.nota_destino, e.nota_app, e.mejora_destino, e.mejora_app, e.feature, e.created_at, u.email "
                                  "FROM encuesta_cierre e LEFT JOIN users u ON u.id = e.user_id ORDER BY e.created_at DESC LIMIT ?", (limite,)).fetchall()
        return {"limite": [dict(f) | {"gusta": bool(f["gusta"])} for f in filas], "cierre": [dict(e) for e in encuestas]}

    @r.get("/lista-espera")
    def lista_espera(limite: int = Query(200, ge=1, le=1000)):
        with db.conn() as c:
            filas = c.execute("SELECT email, name, phone, source, city, created_at FROM waitlist ORDER BY created_at DESC LIMIT ?", (limite,)).fetchall()
        return {"total": waitlist.count(fresh=True), "filas": [dict(f) for f in filas]}

    app.include_router(r)
