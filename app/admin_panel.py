"""Panel de administración dentro de la app: resumen, usuarios y sus límites, códigos de acceso, ajustes y feedback.

Entra el dueño con su propio usuario y contraseña (su correo va en ADMIN_EMAILS), o con el token de entorno. Todo se
guarda en la misma base, que es Supabase en producción: lo que se cambia aquí se ve en el Table Editor y viceversa.
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import accesos, auth, config, db, feedback_limite, limits, net, waitlist

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
        {"key": "signup_max_ip", "grupo": "Registro", "etiqueta": "Registros por hora desde la misma conexión (0 = sin límite)", "tipo": "int", "default": int(os.environ.get("SIGNUP_MAX_PER_IP", "0") or 0)},
        {"key": "maps_global", "grupo": "Gasto", "etiqueta": "Tope de búsquedas de Maps al día para todo el sistema", "tipo": "int", "default": limits.GLOBAL_MAPS_PER_DAY},
        {"key": "tavily_pct", "grupo": "Gasto", "etiqueta": "% del plan mensual de Tavily en que se apagan las búsquedas automáticas", "tipo": "float", "default": float(os.environ.get("TAVILY_BLOQUEO_PCT", "90"))},
        {"key": "require_access_code", "grupo": "Registro", "etiqueta": "Exigir código de acceso para registrarse (1 = sí, 0 = no)", "tipo": "bool", "default": 1 if accesos.env_requerido() else 0},
    ]


class LimitesIn(BaseModel):
    limit_chat: int | None = Field(None, ge=0, le=100000)
    limit_maps: int | None = Field(None, ge=0, le=100000)
    limit_document: int | None = Field(None, ge=0, le=100000)
    limit_scan: int | None = Field(None, ge=0, le=100000)
    limit_trips: int | None = Field(None, ge=0, le=1000)


class DatosIn(BaseModel):
    nombre: str | None = Field(None, max_length=120)
    telefono: str | None = Field(None, max_length=40)   # "" = quitar
    pais: str | None = Field(None, max_length=80)
    plan: str | None = Field(None, max_length=20)


class ClaveIn(BaseModel):
    password: str = Field(..., min_length=8, max_length=200)


PLANES = {"free", "tester", "premium"}


class SuspenderIn(BaseModel):
    suspendido: bool


class ConfigIn(BaseModel):
    key: str = Field(..., max_length=40)
    value: str | None = Field(None, max_length=20)   # None = volver al default


def _tablas_con(columna: str, *esquemas: str) -> list[str]:
    """Tablas (de los esquemas dados) que tienen esa columna: así el borrado de una cuenta no se olvida de ninguna tabla nueva."""
    out = []
    for sql in esquemas:
        for nombre, cuerpo in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
            if nombre != "users" and re.search(r"\b" + columna + r"\b", cuerpo):
                out.append(nombre)
    return out


def borrar_cuenta(uid: str) -> bool:
    """Borra el usuario y TODO lo suyo: viajes con sus mensajes/planes/avisos, sesiones, uso y ampliaciones. Devuelve False si no existe.
    Se conservan la lista de espera (es por correo) y el feedback de límite (aprendizaje, sin datos de contacto)."""
    feedback_limite.asegurar_tablas()
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            return False
        trips = [f["id"] for f in c.execute("SELECT id FROM trips WHERE user_id=?", (uid,)).fetchall()]
        for tid in trips:
            for tabla in _tablas_con("trip_id", db.SCHEMA):
                c.execute(f"DELETE FROM {tabla} WHERE trip_id=?", (tid,))
        c.execute("DELETE FROM trips WHERE user_id=?", (uid,))
        for tabla in _tablas_con("user_id", auth.SCHEMA, limits.SCHEMA, db.SCHEMA):
            if tabla != "trips":
                c.execute(f"DELETE FROM {tabla} WHERE user_id=?", (uid,))
        c.execute("DELETE FROM limit_bonus WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return True


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


def registrar(app, current_user, admin_any, admin_quien):
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

    @r.post("/usuarios/{uid}/quitar-celular")
    def quitar_celular(uid: str):
        """Libera el número de celular de la cuenta (queda sin celular), para poder usarlo en otra."""
        with db.conn() as c:
            f = c.execute("SELECT profile FROM users WHERE id=?", (uid,)).fetchone()
            if not f:
                raise HTTPException(404, "Usuario no existe.")
            c.execute("UPDATE users SET telefono=NULL WHERE id=?", (uid,))
        perfil = auth.get_profile(uid)
        if perfil.get("telefono"):
            auth.set_profile(uid, {**perfil, "telefono": ""})
        return {"ok": True}

    @r.delete("/usuarios/{uid}")
    def eliminar(uid: str, quien: dict = Depends(admin_quien)):
        """Elimina la cuenta y todo lo suyo (no se puede borrar a un admin ni a sí mismo)."""
        with db.conn() as c:
            f = c.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
        if f and (auth.es_admin({"email": f["email"]}) or (quien.get("id") == uid)):
            raise HTTPException(403, "No se puede eliminar una cuenta de administrador.")
        if not borrar_cuenta(uid):
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

    @r.patch("/usuarios/{uid}/datos")
    def cambiar_datos(uid: str, b: DatosIn):
        """Edita nombre, celular, país y plan de una cuenta. El celular se normaliza y no puede repetirse."""
        cambios = {k: getattr(b, k) for k in b.model_fields_set}
        if not cambios:
            raise HTTPException(422, "Nada que cambiar.")
        sets, valores = [], []
        with db.conn() as c:
            if not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                raise HTTPException(404, "Usuario no existe.")
            if "telefono" in cambios:
                tel = (cambios["telefono"] or "").strip()
                if tel:
                    tel = auth.normalize_phone(tel)
                    if not tel:
                        raise HTTPException(422, "Celular inválido. Incluye el código de país (+57…).")
                    otro = c.execute("SELECT id FROM users WHERE telefono=? AND id<>?", (tel, uid)).fetchone()
                    if otro:
                        raise HTTPException(409, "Ese celular ya lo tiene otra cuenta.")
                sets.append("telefono=?"); valores.append(tel or None)
            if "nombre" in cambios:
                sets.append("nombre=?"); valores.append((cambios["nombre"] or "").strip())
            if "pais" in cambios:
                sets.append("pais=?"); valores.append((cambios["pais"] or "").strip())
            if "plan" in cambios:
                if cambios["plan"] not in PLANES:
                    raise HTTPException(422, "Plan no válido.")
                sets.append("plan=?"); valores.append(cambios["plan"])
            c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*valores, uid))
        return {"ok": True}

    @r.post("/usuarios/{uid}/clave")
    def poner_clave(uid: str, b: ClaveIn):
        """Pone una contraseña nueva y cierra todas las sesiones de la cuenta (la persona entra con la nueva)."""
        with db.conn() as c:
            cur = c.execute("UPDATE users SET password_hash=? WHERE id=?", (auth._hash_password(b.password), uid))
            if cur.rowcount != 1:
                raise HTTPException(404, "Usuario no existe.")
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        return {"ok": True}

    @r.get("/viajes")
    def viajes(q: str = Query("", max_length=80), limite: int = Query(50, ge=1, le=200)):
        with db.conn() as c:
            filas = c.execute("SELECT t.id, t.data, t.created_at, u.email FROM trips t LEFT JOIN users u ON u.id = t.user_id "
                              "ORDER BY t.created_at DESC LIMIT 500").fetchall()
        import json
        out = []
        for f in filas:
            try:
                d = json.loads(f["data"])
            except (TypeError, ValueError):
                d = {}
            fila = {"id": f["id"], "email": f["email"] or "", "ciudad": d.get("ciudad", ""), "hotel": d.get("hotel", ""),
                    "inicio": d.get("inicio", ""), "fin": d.get("fin", ""), "creado": f["created_at"]}
            if q.strip() and q.strip().lower() not in f"{fila['email']} {fila['ciudad']} {fila['hotel']}".lower():
                continue
            out.append(fila)
            if len(out) >= limite:
                break
        return {"viajes": out}

    @r.delete("/viajes/{tid}")
    def eliminar_viaje(tid: str):
        with db.conn() as c:
            if not c.execute("SELECT 1 FROM trips WHERE id=?", (tid,)).fetchone():
                raise HTTPException(404, "Viaje no existe.")
            for tabla in _tablas_con("trip_id", db.SCHEMA):
                c.execute(f"DELETE FROM {tabla} WHERE trip_id=?", (tid,))
            c.execute("DELETE FROM trips WHERE id=?", (tid,))
        return {"ok": True}

    @r.delete("/lista-espera/{email}")
    def quitar_de_lista(email: str):
        with db.conn() as c:
            cur = c.execute("DELETE FROM waitlist WHERE email=?", (email.strip().lower(),))
        waitlist.reset_count_cache()
        if cur.rowcount != 1:
            raise HTTPException(404, "Ese correo no está en la lista.")
        return {"ok": True}

    @r.get("/sistema")
    def sistema():
        """Estado del sistema: lo que antes tocaba mirar en Railway, en los logs o en los paneles de cada servicio."""
        from . import email_send, llm, places_live, push, scheduler, search, transporte
        t0 = time.time()
        try:
            with db.conn() as c:
                c.execute("SELECT 1").fetchone()
            base = {"ok": True, "motor": "postgres" if db.IS_POSTGRES else "sqlite", "ms": round((time.time() - t0) * 1000)}
        except Exception as e:
            base = {"ok": False, "motor": "postgres" if db.IS_POSTGRES else "sqlite", "error": type(e).__name__}
        tavily = None
        if search.MODE == "real":
            try:
                resp = net.request("GET", "https://api.tavily.com/usage", retries=0, timeout=8, headers={"Authorization": f"Bearer {search.TAVILY_API_KEY}"})
                cuenta = (resp.json() or {}).get("account") or {}
                tavily = {"usado": cuenta.get("plan_usage"), "limite": cuenta.get("plan_limit"), "plan": cuenta.get("current_plan")}
            except Exception:
                tavily = {"error": "no se pudo consultar"}
        llaves = ["GOOGLE_MAPS_API_KEY", "TAVILY_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "SUPABASE_URL",
                  "SUPABASE_SERVICE_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "RESEND_API_KEY", "EMAIL_FROM", "COMPANION_ADMIN_TOKEN", "ADMIN_EMAILS"]
        return {
            "base_de_datos": base, "planificador": scheduler.running(), "ia": llm.MODE, "busqueda": search.MODE,
            "transporte": "google" if transporte.disponible() else "demo", "notificaciones_push": push.enabled(),
            "lugares_en_vivo": places_live.MODE, "correo": email_send.mode(), "tavily": tavily,
            "maps_hoy": limits.global_used_today("maps"),
            "llaves": {k: bool(os.environ.get(k)) for k in llaves},   # solo si están puestas, nunca su valor
            "solo_en_railway": ["Las llaves y secretos (Google, DeepSeek, Tavily, Supabase, correo, push)", "La URL de la base de datos",
                                "ADMIN_EMAILS y COMPANION_ADMIN_TOKEN (quién es administrador)"],
        }

    app.include_router(r)
