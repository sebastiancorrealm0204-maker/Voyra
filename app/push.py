"""Web Push (VAPID) — notificaciones reales al celular.

Para PWAs instaladas (el caso de Companion en iPhone iOS 16.4+ / Android). No usa
FCM ni APNs directo: usa el estándar Web Push. El navegador del usuario crea una
"suscripción" (un endpoint + claves) cuando da permiso; la guardamos y le
mandamos notificaciones a ese endpoint. En iPhone, Safari la entrega vía APNs
por debajo, sin que nosotros toquemos APNs.

Claves VAPID: se generan UNA vez y se ponen como variables de entorno en Railway
(VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT). Sin ellas, el push real
queda desactivado y la app sigue funcionando igual (las notificaciones quedan en
el feed in-app, como antes). Esto hace que el sistema degrade limpio.

Depende de pywebpush (en requirements). El envío nunca debe tumbar el proceso:
si falla, se loguea y se sigue.
"""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import db, net

log = logging.getLogger("companion.push")

# Timeout por envío: pywebpush no pone ninguno por defecto, así que un endpoint
# de push colgado (FCM/APNs/Mozilla) podía bloquear el thread indefinidamente.
_PUSH_TIMEOUT_S = float(os.environ.get("PUSH_TIMEOUT_S", "10"))
# Pool pequeño para despachar pushes SIN bloquear el request que los originó.
_executor = ThreadPoolExecutor(max_workers=int(os.environ.get("PUSH_WORKERS", "4")),
                               thread_name_prefix="companion-push")

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
# 'sub' del JWT VAPID: un mailto o URL que identifica al emisor.
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:soporte@voyra.app").strip()


def enabled() -> bool:
    """¿Hay claves VAPID configuradas? Si no, el push real está apagado."""
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def public_key() -> str:
    """Clave pública que el frontend necesita para suscribirse."""
    return VAPID_PUBLIC_KEY


def save_subscription(trip_id: str, subscription: dict) -> None:
    """Guarda (o actualiza) la suscripción push del navegador del usuario.

    `subscription` es el objeto que devuelve PushManager.subscribe() en el
    front: {endpoint, keys: {p256dh, auth}}. La identificamos por su endpoint
    (único por dispositivo/navegador), para no duplicar.
    """
    endpoint = subscription.get("endpoint")
    if not endpoint:
        return
    db.upsert_push_subscription(trip_id, endpoint, json.dumps(subscription))


def delete_subscription(endpoint: str) -> None:
    db.delete_push_subscription(endpoint)


def send_to_trip(trip_id: str, title: str, body: str, data: dict | None = None) -> dict:
    """Envía una notificación push a todos los dispositivos suscritos de un viaje.

    Devuelve un resumen {enviadas, fallidas, sin_suscripciones}. Si VAPID no
    está configurado, no hace nada (enabled=False). Las suscripciones expiradas
    (404/410) se borran solas.
    """
    if not enabled():
        return {"enabled": False, "enviadas": 0, "fallidas": 0}

    subs = db.list_push_subscriptions(trip_id)
    if not subs:
        return {"enabled": True, "enviadas": 0, "fallidas": 0, "sin_suscripciones": True}

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("push pywebpush no instalado — push real desactivado")
        return {"enabled": False, "enviadas": 0, "fallidas": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {},
    })
    claims = {"sub": VAPID_SUBJECT}

    enviadas = 0
    fallidas = 0
    for s in subs:
        try:
            sub_info = json.loads(s["subscription"])
        except (ValueError, TypeError):
            fallidas += 1
            continue
        for intento in range(2):  # 1 reintento ante 429/5xx o red
            try:
                webpush(
                    subscription_info=sub_info,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=dict(claims),  # copia: webpush muta el dict
                    ttl=86400,  # guardar hasta 24h si el dispositivo está offline
                    timeout=_PUSH_TIMEOUT_S,
                )
                enviadas += 1
                break
            except WebPushException as e:
                # 404/410 = suscripción muerta (app desinstalada, permiso revocado).
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    db.delete_push_subscription(s["endpoint"])
                    fallidas += 1
                    break
                if intento == 0 and (status in (429,) or (status or 0) >= 500):
                    time.sleep(net.backoff(0))
                    continue
                log.warning("push_fallo status=%s trip=%s", status, trip_id)
                fallidas += 1
                break
            except Exception as e:
                if intento == 0:
                    time.sleep(net.backoff(0))
                    continue
                log.warning("push_excepcion error=%s trip=%s", type(e).__name__, trip_id)
                fallidas += 1
    return {"enabled": True, "enviadas": enviadas, "fallidas": fallidas}


def send_to_trip_async(trip_id: str, title: str, body: str, data: dict | None = None) -> bool:
    """Como send_to_trip pero en background: el request que originó el push
    (ingest de una señal, check-in) no espera a FCM/APNs. Devuelve True si se
    encoló. Sin VAPID no encola nada (barato: no gasta un thread)."""
    if not enabled():
        return False

    def _run():
        try:
            send_to_trip(trip_id, title, body, data=data)
        except Exception:
            log.exception("push_async_error trip=%s", trip_id)

    try:
        _executor.submit(_run)
        return True
    except RuntimeError:  # executor cerrado (shutdown en curso)
        return False


def shutdown() -> None:
    """Espera a los pushes en vuelo al apagar la app (no deja pushes a medias)."""
    _executor.shutdown(wait=True, cancel_futures=False)
