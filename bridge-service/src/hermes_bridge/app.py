"""FastAPI application composition root."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from hermes_bridge.api.health import router as health_router
from hermes_bridge.api.openai import create_openai_router
from hermes_bridge.connector.hub import ConnectorHub
from hermes_bridge.connector.router import create_connector_router
from hermes_bridge.config import Settings
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import MappingRepository, OperationRepository
from hermes_bridge.services.queue import LineageQueue


def create_app(settings: Settings) -> FastAPI:
    """Compose an application from explicit settings."""
    hub = ConnectorHub(settings.connector_heartbeat_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = await Database.open(settings.database_path)
        operations = OperationRepository(database)
        queue = LineageQueue(operations, hub)
        app.state.database = database
        app.state.mapping_repository = MappingRepository(database)
        app.state.operation_repository = operations
        app.state.lineage_queue = queue
        try:
            yield
        finally:
            await queue.close()
            await database.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(health_router)
    app.state.settings = settings
    app.state.connector_hub = hub
    app.include_router(create_connector_router(settings, hub))
    app.include_router(create_openai_router(settings.bridge_secret))
    return app


def create_app_from_env() -> FastAPI:
    """Create the production application from process environment variables."""
    return create_app(Settings.from_env())
