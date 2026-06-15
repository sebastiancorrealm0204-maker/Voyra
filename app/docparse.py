"""Extracción de texto de PDFs en el backend.

Las reservas de vuelos, hoteles y tours casi siempre llegan como PDF con texto
real (no escaneado), así que pypdf basta y es gratis. Si el PDF es escaneado
(sin capa de texto), devolvemos cadena vacía y el caller puede tratarlo como
imagen (visión) en su lugar.
"""
import base64
import io


def pdf_b64_to_text(data_url_or_b64: str) -> str:
    """Acepta un data URL ('data:application/pdf;base64,...') o base64 puro."""
    b64 = data_url_or_b64
    if "," in b64 and b64.strip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return ""
    return pdf_bytes_to_text(raw)


def pdf_bytes_to_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(raw))
        partes = []
        for page in reader.pages[:15]:  # tope defensivo
            try:
                partes.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(partes).strip()
    except Exception:
        return ""
