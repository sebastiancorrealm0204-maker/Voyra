import pytest

# Los scripts de curación (descubrir_*, ampliar_evidencia…) cargan el .env al importarse (verificar_lugares.cargar_env).
# Si un test de esos se recolecta ANTES que test_companion.py, las keys reales (Tavily, LLM) quedan en el entorno cuando la app
# se importa y sus módulos arrancan en modo «real» en vez de «mock». Importamos la app primero, siempre, para fijar el modo.
import app.main  # noqa: F401,E402

@pytest.fixture(autouse=True)
def _verificacion_activa(monkeypatch):
    """Los tests de API existentes cubren el flujo con verificación de email; en
    producción está apagada por defecto (REQUIRE_EMAIL_VERIFICATION=0)."""
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "1")
