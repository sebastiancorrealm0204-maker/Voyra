"""Demo end-to-end: un día completo del Companion contra el backend real.

Correr:  python demo.py        (modo mock, sin keys)
         ANTHROPIC_API_KEY=sk-... python demo.py   (motor con Claude real)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("VOYRA_DEMO", "1")

from app import db, engine, llm, watchers  # noqa: E402

db.DB_PATH = "voyra_demo.db"
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.init_db()

# El filtro de quiet hours depende de la hora real; para el demo lo abrimos.
engine.QUIET_START, engine.QUIET_END = 24.0, 0.0

P = lambda s: print(s, flush=True)
P(f"\n=== VOYRA COMPANION · demo end-to-end · LLM: {llm.MODE} ===\n")

# 1. Crear viaje (input mínimo del usuario)
tid = db.create_trip({
    "ciudad": "Cartagena", "hotel": "Hotel Casa San Agustín",
    "inicio": "2026-07-12", "fin": "2026-07-18",
    "vuelo_ida": "AV9532 BOG→CTG, llega 12 jul 15:40",
    "vuelo_regreso": "AV9533 CTG→BOG, sale 18 jul 14:20",
    "pais": "Colombia", "gustos": ["Gastronomía local", "Historia y cultura"],
})
P(f"[setup] Viaje creado: {tid}")

# 2. Usuario cuenta sus planes (check-in inicial)
db.update_trip(tid, {"planes": ["Mañana: tour Islas del Rosario 9am"]})
P("[contexto] Plan registrado: tour Islas del Rosario mañana 9am")

# 3. Usuario sube documento (texto ya extraído por el cliente)
r = llm.extract_document("Confirmación tour Islas del Rosario - 13 julio 9:00am - Muelle de la Bodeguita - código XK29", "tour.pdf")
db.insert("documents", {"trip_id": tid, "filename": "tour.pdf", "doc_type": r["tipo"], "summary": r["resumen"]})
P(f"[documento] {r['confirmacion']}")

# 4. Señales del día
def señal(nombre, res):
    n = res["notification"]
    P(f"\n[señal] {nombre}")
    P(f"  filtro={res['filter']}  score={res['score']}  decisión={res['decision'].upper()}")
    if n:
        P(f"  → {n['kind'].upper()}: {n['title']} — {n['body'][:90]}")

señal("Webhook Duffel: retraso de vuelo", watchers.duffel_webhook(tid, {
    "type": "order.airline_initiated_change",
    "data": {"description": "AV9533 retrasado 2h, nueva salida 16:20"}}))

señal("Geofence: entra a Getsemaní", watchers.update_location(tid, "Getsemaní", disparar_geofence=True)["geofence_event"])

señal("Scanner: lugar viral cercano", watchers.scanner_finding(
    tid, "Restaurante con terraza 4.9★ a 200m, aparece en 8 TikToks esta semana"))

señal("News watcher: manifestación", watchers.news_alert(
    tid, "Manifestación 4pm en el centro con cierres viales; videos en TikTok confirman concentración"))

señal("Promo no solicitada (debe morir en el filtro)", engine.ingest(
    tid, "Señal de baja calidad", "promo_no_solicitada", False, "Promo genérica de puntos"))

# 5. Presupuesto: saturar la categoría recomendación
señal("Scanner: 2ª recomendación", watchers.scanner_finding(tid, "Café de especialidad trending a 300m"))
señal("Scanner: 3ª recomendación (debe morir: tope de categoría)", watchers.scanner_finding(tid, "Bar en azotea trending"))

# 6. Feedback: dos 'no me interesa' silencian la categoría
notifs = [n for n in db.rows("notifications", tid) if n["category"] == "recomendacion"]
for n in notifs[:2]:
    fb = engine.feedback(n["id"], "not_interested")
P(f"\n[feedback] 2x 'no me interesa' en recomendaciones → categoría silenciada: {fb['category_silenced']}")
señal("Scanner: 4ª recomendación (debe morir: silenciada por usuario)",
      watchers.scanner_finding(tid, "Heladería viral"))

# 7. Chat con contexto
P("\n[chat] usuario: 'estoy perdido'")
db.insert("messages", {"trip_id": tid, "role": "user", "content": "estoy perdido"})
from app import context  # noqa: E402
hist = [{"role": m["role"], "content": m["content"]} for m in db.rows("messages", tid)]
P(f"  companion: {llm.chat(context.build(db.get_trip(tid)), hist)[:200]}")

# 8. Check-in nocturno
P(f"\n[check-in 8pm] {watchers.check_in(tid)['message'][:200]}")


# 10. News watcher + Destination Scanner real (Tavily)
scan = watchers.scan_destination(tid)
P(f"\n[scan] modo búsqueda: {scan['search_mode']} — encontrados: {scan['encontrados']}, procesados: {scan['procesados']}")
if scan['search_mode'] == 'mock':
    P("       (sin TAVILY_API_KEY no busca nada — exporta la key para ver resultados reales)")
for r in scan['resultados']:
    n = r['notification']
    P(f"  [{r['tipo']}] {r['fuente'][:60]} → score={r['score']} decisión={r['decision'].upper()}")
    if n:
        P(f"      → {n['title']}: {n['body'][:90]}")


# 11. Nearby recommendations: base curada + distancia real + link de Maps
P("\n=== Bonus: viaje a Bogotá, recomendaciones por distancia real ===")
from app import seed_data as _sd
db.seed_destination_places(_sd.all_seeds())
tid_bog = db.create_trip({
    "ciudad": "Bogotá", "hotel": "Hotel Zona G", "inicio": "2026-08-01", "fin": "2026-08-05",
    "pais": "Colombia", "gustos": ["Gastronomía local", "Historia y cultura"],
})
watchers.update_location(tid_bog, "Usaquén", disparar_geofence=False)
near = watchers.nearby_recommendations(tid_bog)
P(f"[nearby] zona_actual=Usaquén · candidatos curados: {near['candidatos']}")
for r in near["resultados"]:
    n = r["notification"]
    P(f"  {r['lugar']} — {r['distancia_km']} km → score={r['score']} decisión={r['decision'].upper()}")
    if n:
        P(f"      maps_link: {n['maps_link']}")

# 9. El dataset que guardamos desde el día uno
ds = db.rows("decisions", tid)
P(f"\n[dataset] {len(ds)} decisiones registradas (filtro/score/decisión) — listas para entrenar el scorer propio a futuro.")
P("\n=== demo OK ===\n")
