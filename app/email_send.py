"""Envío de correos — verificación de email del Companion.

Soporta dos backends, en orden de preferencia:

1) RESEND (recomendado, gratis hasta 3k correos/mes): setea RESEND_API_KEY.
   Es una sola llamada HTTP, sin SMTP ni dependencias. https://resend.com
2) SMTP genérico (Gmail, etc.): setea SMTP_HOST, SMTP_PORT, SMTP_USER,
   SMTP_PASSWORD.

Si NINGUNO está configurado, degrada limpio a modo DEV: imprime el enlace de
verificación en los logs (y lo devuelve en la respuesta del endpoint) para que
puedas probar el flujo sin enviar correos reales todavía.

Variables de entorno:
  RESEND_API_KEY        — key de Resend
  EMAIL_FROM            — remitente, ej. "Voyra <hola@tudominio.com>"
  APP_BASE_URL          — URL pública del backend, ej. https://web-production-1b2f6.up.railway.app
  SMTP_HOST/PORT/USER/PASSWORD — alternativa SMTP
"""
import os

import httpx

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "Voyra <onboarding@resend.dev>").strip()
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip().rstrip("/")

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()


def mode() -> str:
    if RESEND_API_KEY:
        return "resend"
    if SMTP_HOST and SMTP_USER:
        return "smtp"
    return "dev"


def verification_url(token: str) -> str:
    base = APP_BASE_URL or "http://localhost:8000"
    return f"{base}/auth/verify?token={token}"


def _html(link: str) -> str:
    return f"""
    <div style="font-family:system-ui,Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px">
      <h2 style="color:#0f172a">Confirma tu email</h2>
      <p style="color:#475569;font-size:15px;line-height:1.5">
        ¡Hola! Soy tu Companion de viaje. Confirma tu correo para empezar a planear y
        que pueda acompañarte durante el viaje.
      </p>
      <p style="margin:28px 0">
        <a href="{link}" style="background:#0ea5e9;color:#fff;text-decoration:none;
           padding:12px 22px;border-radius:10px;font-weight:600;font-size:15px">
          Confirmar mi email
        </a>
      </p>
      <p style="color:#94a3b8;font-size:13px">
        Si el botón no funciona, copia este enlace:<br>{link}
      </p>
      <p style="color:#cbd5e1;font-size:12px">El enlace vence en 24 horas.</p>
    </div>
    """


def send_verification(email: str, token: str) -> dict:
    """Envía el correo de verificación. Devuelve {sent, mode, dev_link?}.

    En modo dev, no envía nada: devuelve el enlace para que lo uses a mano.
    Nunca lanza excepción que tumbe el registro; si falla el envío, lo reporta.
    """
    link = verification_url(token)
    m = mode()

    if m == "dev":
        print(f"[email:dev] Verificación para {email}: {link}")
        return {"sent": False, "mode": "dev", "dev_link": link}

    if m == "resend":
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                         "Content-Type": "application/json"},
                json={"from": EMAIL_FROM, "to": [email],
                      "subject": "Confirma tu email — Voyra",
                      "html": _html(link)},
                timeout=15,
            )
            ok = resp.status_code in (200, 201)
            if not ok:
                print(f"[email:resend] fallo {resp.status_code}: {resp.text[:200]}")
            return {"sent": ok, "mode": "resend"}
        except Exception as e:
            print(f"[email:resend] excepción: {e}")
            return {"sent": False, "mode": "resend", "error": str(e)}

    # SMTP
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(_html(link), "html", "utf-8")
        msg["Subject"] = "Confirma tu email — Voyra"
        msg["From"] = EMAIL_FROM
        msg["To"] = email
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASSWORD)
            s.sendmail(SMTP_USER, [email], msg.as_string())
        return {"sent": True, "mode": "smtp"}
    except Exception as e:
        print(f"[email:smtp] excepción: {e}")
        return {"sent": False, "mode": "smtp", "error": str(e)}
