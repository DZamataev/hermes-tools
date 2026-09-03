from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from hermes_bridge.app import create_app
from hermes_bridge.connector.hub import ConnectorDisconnected, ConnectorHub
from hermes_bridge.connector.protocol import (
    AcceptedFrame, HeartbeatFrame, HelloFrame, HermesEventFrame, SubmitFrame, sign_challenge,
)


def hello_for(challenge, secret):
    timestamp = int(datetime.now(timezone.utc).timestamp())
    return {
        "protocol": 1, "kind": "hello", "id": "epoch-1", "correlation_id": challenge["id"],
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"timestamp": timestamp, "mac": sign_challenge(secret, challenge["payload"]["nonce"], timestamp),
                    "connector_version": "1.0.0", "routes": []},
    }


def test_connector_rejects_wrong_secret(settings):
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/connector") as socket:
            challenge = socket.receive_json()
            socket.send_json(hello_for(challenge, "wrong"))
            error = socket.receive_json()
            assert error["kind"] == "error"
            assert error["payload"]["code"] == "authentication_failed"
            assert settings.bridge_secret not in str(error)


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.closed = []

    async def send_json(self, value):
        self.sent.append(value)

    async def close(self, **kwargs):
        self.closed.append(kwargs)


def hello(epoch="epoch-1"):
    return HelloFrame.model_validate({
        "protocol": 1, "kind": "hello", "id": epoch, "correlation_id": "challenge",
        "sent_at": datetime.now(timezone.utc),
        "payload": {"timestamp": 1, "mac": "mac", "connector_version": "1.0.0",
                    "routes": [{"connection_id": "local", "profile": "default", "target_profile": "default"}]},
    })


def submit(correlation="c1"):
    return SubmitFrame.model_validate({
        "protocol": 1, "kind": "submit", "id": "command", "correlation_id": correlation,
        "sent_at": datetime.now(timezone.utc),
        "payload": {"operation_id": "op", "route": {"connection_id": "local", "profile": "default", "target_profile": "default"},
                    "stored_session_id": "tip", "text": "hello", "queued": True},
    })


async def test_second_connector_replaces_first_and_updates_inventory():
    hub, first, second = ConnectorHub(20), FakeSocket(), FakeSocket()
    await hub.connect(first, hello("epoch-1"))
    await hub.connect(second, hello("epoch-2"))
    assert first.closed and hub.connected and hub.epoch == "epoch-2"
    assert hub.routes[0].target_profile == "default"


async def test_dispatch_correlates_until_terminal_event():
    hub, socket = ConnectorHub(20), FakeSocket()
    await hub.connect(socket, hello())
    command = submit()

    async def collect():
        return [item async for item in hub.dispatch(command)]

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    accepted = AcceptedFrame.model_validate({
        "protocol": 1, "kind": "accepted", "id": "a", "correlation_id": "c1",
        "sent_at": datetime.now(timezone.utc), "payload": {"operation_id": "op", "runtime_session_id": "runtime"},
    })
    terminal = HermesEventFrame.model_validate({
        "protocol": 1, "kind": "hermes_event", "id": "e", "correlation_id": "c1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {"operation_id": "op", "connection_id": "local", "profile": "default",
                    "session_id": "runtime", "seq": 1, "event_type": "message.complete", "data": {"text": "done"}},
    })
    await hub.receive(accepted)
    await hub.receive(terminal)
    assert [event.kind for event in await task] == ["accepted", "hermes_event"]
    assert socket.sent[0]["kind"] == "submit"


async def test_disconnect_fails_pending_dispatch():
    hub, socket = ConnectorHub(20), FakeSocket()
    await hub.connect(socket, hello())
    task = asyncio.create_task(anext(hub.dispatch(submit())))
    await asyncio.sleep(0)
    await hub.disconnect(socket)
    with pytest.raises(ConnectorDisconnected):
        await task


async def test_stale_heartbeat_marks_hub_offline():
    hub, socket = ConnectorHub(0.001), FakeSocket()
    await hub.connect(socket, hello())
    await asyncio.sleep(0.005)
    assert hub.connected is False


async def test_unsolicited_event_reaches_observer_without_affecting_dispatch():
    hub, socket = ConnectorHub(20), FakeSocket()
    observed = []
    hub.set_observer(on_connect=lambda frame: None, on_event=observed.append)
    await hub.connect(socket, hello())
    event = HermesEventFrame.model_validate({
        "protocol": 1, "kind": "hermes_event", "id": "desktop-1",
        "correlation_id": "not-a-dispatch", "sent_at": datetime.now(timezone.utc),
        "payload": {"operation_id": None, "connection_id": "local", "profile": "default",
                    "session_id": "runtime", "seq": 1, "event_type": "message.complete",
                    "data": {"text": "desktop answer"}},
    })
    await hub.receive(event, socket)
    assert observed == [event]
