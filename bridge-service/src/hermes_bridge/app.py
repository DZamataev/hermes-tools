"""FastAPI application composition root."""

from fastapi import FastAPI

from hermes_bridge.api.health import router as health_router
from hermes_bridge.connector.hub import ConnectorHub
from hermes_bridge.connector.router import create_connector_router
from hermes_bridge.config import Settings


def create_app(settings: Settings) -> FastAPI:
    """Compose an application from explicit settings."""
    app = FastAPI()
    app.include_router(health_router)
    app.state.settings = settings
    hub = ConnectorHub(settings.connector_heartbeat_timeout_seconds)
    app.state.connector_hub = hub
    app.include_router(create_connector_router(settings, hub))
    return app


def create_app_from_env() -> FastAPI:
    """Create the production application from process environment variables."""
    return create_app(Settings.from_env())
