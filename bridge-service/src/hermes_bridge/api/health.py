"""Secret-free process, dependency, and synchronization health."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live(request: Request):
    """Report process and bridge-owned database availability (ledger R1)."""
    database = getattr(request.app.state, "database", None)
    if database is None or not database.is_open:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "components": {"database": "unavailable"}},
        )
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    database = getattr(request.app.state, "database", None)
    sync = getattr(request.app.state, "sync_service", None)
    hub = request.app.state.connector_hub
    components = {
        "database": "ready" if database is not None and database.is_open else "unavailable",
        "openwebui": "unavailable",
        "hermes_read_api": "unavailable",
        "desktop_connector": "ready" if hub.connected else "unavailable",
    }
    if sync is not None:
        components.update(sync.ready_components)
    available = all(value == "ready" for value in components.values())
    content = {"status": "ready" if available else "not_ready", "components": components}
    return content if available else JSONResponse(status_code=503, content=content)


@router.get("/status")
async def status(request: Request) -> dict:
    database = getattr(request.app.state, "database", None)
    sync = getattr(request.app.state, "sync_service", None)
    snapshot = await sync.current_status_snapshot() if sync is not None else {
        "last_scan": None,
        "last_successful_reconciliation": None,
        "connector": {
            "connected": False,
            "version": None,
            "epoch": None,
            "profile_count": 0,
        },
        "components": {"openwebui": "unavailable", "hermes_read_api": "unavailable"},
        "queue": {"pending": 0, "active": 0, "uncertain": 0, "failed": 0},
        "dropped_live_events": 0,
        "failures": [],
    }
    return {
        "status": "ok",
        "database": "ready" if database is not None and database.is_open else "unavailable",
        **snapshot,
    }
