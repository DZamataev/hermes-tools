from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from hermes_bridge.connector.hub import ConnectorHub
from hermes_bridge.connector.protocol import (
    ApprovalRequestFrame, ApprovalResolvedFrame, ApprovalScanFrame, ApprovalSnapshotFrame,
    CommandErrorFrame, HelloFrame,
    ResolveApprovalFrame,
)


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, value):
        self.sent.append(value)

    async def close(self, **kwargs):
        pass


def hello():
    return HelloFrame.model_validate({
        "protocol": 1, "kind": "hello", "id": "epoch-1", "correlation_id": "challenge-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {
            "timestamp": 1, "mac": "mac", "connector_version": "1.2.0",
            "capabilities": ["replay_complete", "approvals_v1"], "routes": [],
        },
    })


def approval_scan():
    return ApprovalScanFrame.model_validate({
        "protocol": 1, "kind": "approval_scan", "id": "scan-command-1", "correlation_id": "scan-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {
            "operation_id": "scan-operation-1",
            "route": {"connection_id": "local", "profile": "default", "target_profile": "backend-default"},
        },
    })


async def test_approval_snapshot_ends_its_dispatch_and_clears_its_pending_queue():
    hub, socket = ConnectorHub(20), FakeSocket()
    await hub.connect(socket, hello())
    task = asyncio.create_task(_collect(hub.dispatch(approval_scan())))
    await asyncio.sleep(0)
    snapshot = ApprovalSnapshotFrame.model_validate({
        "protocol": 1, "kind": "approval_snapshot", "id": "snapshot-1", "correlation_id": "scan-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {
            "operation_id": "scan-operation-1",
            "route": {"connection_id": "local", "profile": "default", "target_profile": "backend-default"},
            "sessions": [], "approvals": [],
        },
    })

    await hub.receive(snapshot, socket)

    assert await asyncio.wait_for(task, 0.1) == [snapshot]
    assert hub._pending == {}


async def test_approval_resolution_ends_its_dispatch_and_clears_its_pending_queue():
    hub, socket = ConnectorHub(20), FakeSocket()
    await hub.connect(socket, hello())
    command = ResolveApprovalFrame.model_validate({
        "protocol": 1, "kind": "resolve_approval", "id": "resolve-command-1", "correlation_id": "resolve-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {
            "operation_id": "resolve-operation-1",
            "route": {"connection_id": "local", "profile": "default", "target_profile": "backend-default"},
            "approval_id": "approval-1", "runtime_session_id": "runtime-1", "stored_session_id": "stored-1",
            "request_id": "request-1", "choice": "deny",
        },
    })
    task = asyncio.create_task(_collect(hub.dispatch(command)))
    await asyncio.sleep(0)
    resolved = ApprovalResolvedFrame.model_validate({
        "protocol": 1, "kind": "approval_resolved", "id": "resolved-1", "correlation_id": "resolve-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {"operation_id": "resolve-operation-1", "approval_id": "approval-1", "choice": "deny", "accepted": True},
    })

    await hub.receive(resolved, socket)

    assert await asyncio.wait_for(task, 0.1) == [resolved]
    assert hub._pending == {}


async def test_approval_command_error_ends_its_correlated_dispatch_and_clears_its_pending_queue():
    hub, socket = ConnectorHub(20), FakeSocket()
    await hub.connect(socket, hello())
    task = asyncio.create_task(_collect(hub.dispatch(approval_scan())))
    await asyncio.sleep(0)
    failed = CommandErrorFrame.model_validate({
        "protocol": 1, "kind": "command_error", "id": "error-1", "correlation_id": "scan-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {
            "operation_id": "scan-operation-1", "code": "approval_scan_failed",
            "message": "Hermes approval scan failed", "acceptance_unknown": False,
        },
    })

    await hub.receive(failed, socket)

    assert await asyncio.wait_for(task, 0.1) == [failed]
    assert hub._pending == {}


async def test_unsolicited_approval_request_reaches_observer_without_creating_a_dispatch():
    hub, socket = ConnectorHub(20), FakeSocket()
    observed = []
    hub.set_observer(on_connect=None, on_event=observed.append)
    await hub.connect(socket, hello())
    request = ApprovalRequestFrame.model_validate({
        "protocol": 1, "kind": "approval_request", "id": "request-event-1", "correlation_id": "request-event-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {
            "route": {"connection_id": "local", "profile": "default", "target_profile": "backend-default"},
            "runtime_session_id": "runtime-1", "stored_session_id": "stored-1", "request_id": "request-1",
            "description": "Run a command", "command": "git status", "allow_permanent": False,
            "smart_denied": False, "choices": ["once", "deny"], "seq": 2,
        },
    })

    await hub.receive(request, socket)

    assert observed == [request]
    assert hub._pending == {}


async def test_authenticated_capabilities_are_exposed_then_cleared_on_disconnect():
    hub, socket = ConnectorHub(20), FakeSocket()
    await hub.connect(socket, hello())

    assert hub.capabilities == frozenset({"replay_complete", "approvals_v1"})

    await hub.disconnect(socket)
    assert hub.capabilities == frozenset()


async def _collect(events):
    return [event async for event in events]
