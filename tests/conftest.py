import pytest


@pytest.fixture(autouse=True)
def _verificacion_activa(monkeypatch):
    """Los tests de API existentes cubren el flujo con verificación de email; en
    producción está apagada por defecto (REQUIRE_EMAIL_VERIFICATION=0)."""
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "1")
