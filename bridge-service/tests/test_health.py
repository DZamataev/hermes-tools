from fastapi.testclient import TestClient

from hermes_bridge.app import create_app


def test_liveness_does_not_expose_configuration(settings):
    response = TestClient(create_app(settings)).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert settings.openwebui_api_key not in response.text
