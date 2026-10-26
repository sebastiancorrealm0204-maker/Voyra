# Companion — backend del agente del durante

> **Posicionamiento:** el Companion es un local con carácter y memoria, alimentado por una
> red local curada y verificado con IA encima. **Avisa, pero sin
> invadir:** máximo 3 avisos no urgentes al día, silencio de noche, y si el usuario está
> usando la app en ese momento lo no urgente queda en el feed en vez de sonarle el celular
> (`engine.EN_APP_MINUTOS`). Los check-ins no mandan push si habló con el Companion en las
> últimas 3 h. Lo operativo (vuelo, seguridad) llega siempre.
>
> **Verificación que corrige:** antes de guardar y mostrar cada respuesta del chat,
> `app/verificacion.py` revisa frase por frase (sin LLM, `food_guard.py`) que ningún plato se
> atribuya a un lugar curado cuya ficha no lo respalde. Si encuentra algo, una segunda llamada
> reescribe la respuesta con la ficha real; si la reescritura sigue mal o el LLM falla, recorta
> las frases culpables. Sin hallazgos no hay llamadas extra.
>
> **Memoria:** en cada turno del chat, `app/memoria.py` añade al prompt el perfil global del
> viajero (preferencias, alergias, notas) y sus viajes anteriores (ciudades, gustos, lugares).

El servicio que vive en el servidor: persiste el Trip Context, recibe señales de los
watchers (webhooks Duffel, geofences del teléfono, Destination Scanner, News watcher),
decide con el motor de relevancia (filtro determinístico + scoring con Claude), entrega
notificaciones con presupuesto anti-spam, conversa con contexto completo, y **guarda toda
decisión como dataset desde el día uno** (principio de arquitectura de datos acordado).

## Correr en 30 segundos (modo demo, sin keys)

```bash
pip install -r requirements.txt
python demo.py                      # simula un día completo del Companion
pytest tests/ -q                    # tests del motor y la API
uvicorn app.main:app --reload       # API en http://localhost:8000/docs
```

Sin `ANTHROPIC_API_KEY`, el LLM corre en modo mock determinístico: todo el pipeline
(filtro, presupuesto, feedback, dataset) funciona igual; solo los textos son de demo.

## Conectar el cerebro real — multi-proveedor con routing por tarea

El backend rutea cada tarea al proveedor que le configures (la arquitectura de costos de la spec):

| Tarea | Volumen | Recomendado | Por qué |
|---|---|---|---|
| Scoring del motor | ~120 llamadas/viaje | **DeepSeek** (o Groq) | Clasificación con JSON: ~$0.14/M tokens, 85% más barato |
| Extracción de documentos | 3-5/viaje | **DeepSeek** (o Groq) | Tarea mecánica, no necesita el mejor modelo |
| Conversación del Companion | ~25 turnos/viaje | **Claude Haiku** | La voz del agente ES el producto: tono, español LATAM, emergencias |

```bash
# Opción A — solo DeepSeek (lo más barato, todo en uno):
export DEEPSEEK_API_KEY=sk-...        # platform.deepseek.com

# Opción B — solo Groq (gratis para empezar, muy rápido):
export GROQ_API_KEY=gsk_...           # console.groq.com (tier gratis generoso)

# Opción C — la recomendada de la spec (routing mixto):
export DEEPSEEK_API_KEY=sk-...        # scoring + extracción (baratos)
export ANTHROPIC_API_KEY=sk-ant-...   # conversación (calidad)

uvicorn app.main:app --reload
```

El routing se decide solo: con varias keys, scoring/extracción van al más barato y el chat
a Claude. Puedes forzarlo con `SCORING_PROVIDER`, `CHAT_PROVIDER`, `EXTRACT_PROVIDER`
(valores: anthropic | deepseek | groq). Verifica en http://localhost:8000/health qué
proveedor está atendiendo cada tarea. Sin ninguna key, todo corre en modo mock.

## Endpoints principales

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/trips` | Crea el viaje con el input mínimo (ciudad, hotel, fechas, vuelos, país, gustos) |
| POST | `/trips/{id}/plans` | Registra un plan contado por el usuario |
| POST | `/trips/{id}/location` | Señal de geofence / ubicación significativa (actualiza contexto gratis) |
| POST | `/trips/{id}/signals` | Ingesta genérica de señales al motor |
| POST | `/webhooks/duffel/{id}` | Webhook de Duffel normalizado a señal operativa |
| POST | `/trips/{id}/checkin` | Check-in nocturno (cron 8pm): pregunta/confirma planes de mañana |
| POST | `/trips/{id}/chat` | Conversación con el Trip Context completo |
| POST | `/trips/{id}/documents` | Sube documento (texto extraído) → parser → Trip Context |
| GET | `/trips/{id}/notifications` | Push y feed entregados |
| POST | `/notifications/{id}/feedback` | tapped / dismissed / not_interested (2x → silencia categoría) |
| GET | `/trips/{id}/decisions` | **El dataset**: toda decisión del motor con filtro, score y razón |

## El motor de relevancia (app/engine.py)

```
señal → FILTRO DETERMINÍSTICO (costo cero)          → SCORING (Claude Haiku)
        · categoría bloqueada                          · score 0-100 con razón
        · silenciada por el usuario (feedback)         · ≥70 push
        · quiet hours 22:30–07:00 (operativo pasa)     · 40-69 feed in-app
        · presupuesto 3 push/día (operativo pasa)      · <40 silencio
        · tope 2/categoría/día
```

Los eventos operativos (vuelo, seguridad) tienen prioridad absoluta: ignoran
presupuesto, quiet hours y silenciamientos, y su score se eleva a >=90.

## Qué falta para producción (en orden)

1. **Push real**: conectar FCM/APNs en el punto marcado en `engine.ingest` (hoy queda persistido y consultable por la app vía polling/websocket).
2. **Duffel real**: registrar el webhook en el dashboard de Duffel apuntando a `/webhooks/duffel/{trip_id}` con verificación de firma.
3. **Multimodal en documentos**: hoy el endpoint recibe texto extraído; pasar a base64 de imagen/PDF con Gemini/DeepSeek Flash (el prototipo frontend ya lo hace con visión real).
4. **Destination Scanner como job**: cron por destino cada 24h (TikTok/IG/News) escribiendo a una tabla `destination_feed` compartida; `watchers.scanner_finding` pasa a leer de ahí.
5. **Timezone del destino** en el Trip Context para quiet hours correctas.
6. **Prompt caching** del Trip Context (header `cache_control` de la API de Anthropic) — el ahorro del 90% de la spec.
7. **Postgres** en lugar de SQLite al desplegar.

## Despliegue

- **Railway** (actual): `Procfile` → `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- **Render** (plan B): `render.yaml` en la raíz. No tengas los dos encendidos a la vez con la misma `DATABASE_URL` (el programador interno enviaría avisos duplicados).
- Verificación rápida tras desplegar: `GET /health` debe responder `status: ok` y `/openapi.json` debe incluir `/waitlist`.
