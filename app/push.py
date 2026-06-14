"""Web Push (VAPID) — notificaciones reales al celular.

Para PWAs instaladas (el caso de Voyra en iPhone iOS 16.4+ / Android). No usa
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
import os

from . import db

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
        print("[push] pywebpush no instalado — push real desactivado")
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
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=dict(claims),  # copia: webpush muta el dict
                ttl=86400,  # guardar hasta 24h si el dispositivo está offline
            )
            enviadas += 1
        except WebPushException as e:
            # 404/410 = suscripción muerta (app desinstalada, permiso revocado).
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):
                db.delete_push_subscription(s["endpoint"])
            fallidas += 1
        except Exception:
            fallidas += 1
    return {"enabled": True, "enviadas": enviadas, "fallidas": fallidas}
