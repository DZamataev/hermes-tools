from fastapi.testclient import TestClient

from hermes_bridge.app import create_app


def test_liveness_does_not_expose_configuration(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert settings.openwebui_api_key not in response.text


def test_liveness_fails_when_database_is_unavailable_without_secrets(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.database = None
        response = client.get("/health/live")
        assert response.status_code == 503
        assert response.json()["components"] == {"database": "unavailable"}
        assert settings.openwebui_api_key not in response.text
        assert settings.bridge_secret not in response.text


def test_readiness_and_status_are_redacted(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["components"]["desktop_connector"] == "unavailable"
        status = client.get("/health/status")
        assert status.status_code == 200
        assert set(status.json()["queue"]) == {"pending", "active", "uncertain", "failed"}
        assert settings.openwebui_api_key not in status.text
        assert settings.bridge_secret not in status.text
        assert settings.api_server_key not in status.text
