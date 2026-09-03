"""FastAPI application composition root."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from hermes_bridge.api.health import router as health_router
from hermes_bridge.api.openai import create_openai_router
from hermes_bridge.connector.hub import ConnectorHub
from hermes_bridge.connector.router import create_connector_router
from hermes_bridge.config import Settings
from hermes_bridge.hermes.client import HermesReadClient
from hermes_bridge.openwebui.client import OpenWebUIClient
from hermes_bridge.openwebui.mirror import MirrorService
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import (
    EventRepository,
    MappingRepository,
    OperationRepository,
)
from hermes_bridge.services.queue import LineageQueue
from hermes_bridge.services.sync import SyncService


def create_app(settings: Settings) -> FastAPI:
    """Compose an application from explicit settings."""
    hub = ConnectorHub(settings.connector_heartbeat_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = await Database.open(settings.database_path)
        mappings = MappingRepository(database)
        operations = OperationRepository(database)
        events = EventRepository(database)
        queue = LineageQueue(operations, hub)
        hermes = HermesReadClient(settings.hermes_base_url, settings.api_server_key)
        openwebui = OpenWebUIClient(settings.openwebui_base_url, settings.openwebui_api_key)
        mirror = MirrorService(openwebui, mappings)
        sync = SyncService(
            hermes,
            mirror,
            mappings,
            operations,
            events,
            hub,
            queue,
            interval_seconds=settings.sync_interval_seconds,
        )
        app.state.database = database
        app.state.mapping_repository = mappings
        app.state.operation_repository = operations
        app.state.lineage_queue = queue
        app.state.sync_service = sync
        await sync.start()
        try:
            yield
        finally:
            await sync.close()
            await queue.close()
            await hermes.aclose()
            await openwebui.aclose()
            await database.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(health_router)
    app.state.settings = settings
    app.state.connector_hub = hub
    app.state.database = None
    app.state.sync_service = None
    app.include_router(create_connector_router(settings, hub))
    app.include_router(create_openai_router(settings.bridge_secret))
    return app


def create_app_from_env() -> FastAPI:
    """Create the production application from process environment variables."""
    return create_app(Settings.from_env())
