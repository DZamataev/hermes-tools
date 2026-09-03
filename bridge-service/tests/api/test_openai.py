from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI

from hermes_bridge.app import create_app
from hermes_bridge.api import openai
from hermes_bridge.api.openai import create_openai_router
from hermes_bridge.connector.protocol import AcceptedFrame, HermesEventFrame, ProfileRoute
from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import MappingRepository, OperationRepository
from hermes_bridge.services.queue import LineageQueue


SECRET = "bridge-secret-that-is-at-least-32-characters"
HEADERS = {"Authorization": f"Bearer {SECRET}", "X-Hermes-Chat-Id": "chat-1",
           "X-Hermes-User-Message-Id": "user-1", "X-Hermes-Assistant-Message-Id": "assistant-1"}


def request_body(text="hello", **overrides):
    value = {"model": "hermes-live", "stream": True, "messages": [{"role": "user", "content": text}]}
    value.update(overrides)
    return value


class ImmediateConnector:
    connected = True
    routes = (ProfileRoute(connection_id="local", profile="default", target_profile="default"),)

    def __init__(self): self.submit_count = 0

    async def dispatch(self, command):
        self.submit_count += 1
        common = {"protocol": 1, "correlation_id": command.correlation_id, "sent_at": datetime.now(timezone.utc)}
        yield AcceptedFrame.model_validate({**common, "kind": "accepted", "id": "accepted", "payload": {
            "operation_id": command.payload.operation_id, "runtime_session_id": "runtime-1"}})
        for seq, event_type in ((1, "message.delta"), (2, "message.complete")):
            yield HermesEventFrame.model_validate({**common, "kind": "hermes_event", "id": f"event-{seq}", "payload": {
                "operation_id": command.payload.operation_id, "connection_id": "local", "profile": "default",
                "session_id": "runtime-1", "seq": seq, "event_type": event_type, "data": {"text": "answer"}}})


@pytest.fixture
async def api(tmp_path):
    database = await Database.open(tmp_path / "api.sqlite3")
    mappings, operations = MappingRepository(database), OperationRepository(database)
    identity = await mappings.upsert_session(SessionIdentity("local", "default", "root-1", "tip-1", "One"))
    await mappings.attach_chat(identity.lineage_key, "chat-1")
    connector = ImmediateConnector(); queue = LineageQueue(operations, connector)
    app = FastAPI()
    app.state.mapping_repository, app.state.operation_repository = mappings, operations
    app.state.lineage_queue, app.state.connector_hub = queue, connector
    app.include_router(create_openai_router(SECRET))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, connector
    await queue.close(); await database.close()


@pytest.mark.parametrize("token", [None, "", "owner-openwebui-key", "hermes-api-key", "wrong"])
async def test_both_openai_endpoints_require_only_bridge_bearer(api, token):
    client, _ = api
    auth = {} if token is None else {"Authorization": f"Bearer {token}"}
    assert (await client.get("/v1/models", headers=auth)).status_code == 401
    headers = {key: value for key, value in HEADERS.items() if key != "Authorization"} | auth
    assert (await client.post("/v1/chat/completions", headers=headers, json=request_body())).status_code == 401


async def test_models_exposes_only_hermes_live(api):
    response = await api[0].get("/v1/models", headers={"Authorization": f"Bearer {SECRET}"})
    assert response.status_code == 200
    assert [model["id"] for model in response.json()["data"]] == ["hermes-live"]


async def test_stream_has_role_delta_stop_done_and_duplicate_replays(api):
    client, connector = api
    first = await client.post("/v1/chat/completions", headers=HEADERS, json=request_body())
    second = await client.post("/v1/chat/completions", headers=HEADERS, json=request_body())
    assert first.status_code == second.status_code == 200 and connector.submit_count == 1
    lines = [line for line in first.text.splitlines() if line.startswith("data: ")]
    payloads = [json.loads(line[6:]) for line in lines[:-1]]
    assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert payloads[1]["choices"][0]["delta"] == {"content": "answer"}
    assert payloads[-1]["choices"][0]["finish_reason"] == "stop"
    assert lines[-1] == "data: [DONE]"
    assert "operation_id" not in first.text and "runtime-1" not in first.text


@pytest.mark.parametrize("missing", ["X-Hermes-Chat-Id", "X-Hermes-User-Message-Id", "X-Hermes-Assistant-Message-Id"])
async def test_completion_requires_all_identity_headers(api, missing):
    response = await api[0].post("/v1/chat/completions", headers={k: v for k, v in HEADERS.items() if k != missing}, json=request_body())
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("body", [request_body(stream=False), request_body(model="other"), request_body(" "),
    request_body([{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "x"}}]),
    request_body(tools=[]), request_body(tool_choice="none"),
    request_body(messages=[{"role": "tool", "content": "result"}, {"role": "user", "content": "continue"}])])
async def test_completion_rejects_invalid_text_only_stream_requests(api, body):
    response = await api[0].post("/v1/chat/completions", headers=HEADERS, json=body)
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


async def test_unmapped_chat_returns_conflict(api):
    response = await api[0].post("/v1/chat/completions", headers={**HEADERS, "X-Hermes-Chat-Id": "new-chat"}, json=request_body())
    assert response.status_code == 409 and response.json()["error"]["code"] == "hermes_session_not_mapped"


async def test_offline_desktop_returns_503_without_dispatch(api):
    client, connector = api; connector.connected = False
    response = await client.post("/v1/chat/completions", headers=HEADERS, json=request_body())
    assert response.status_code == 503 and response.json()["error"]["code"] == "hermes_desktop_offline"


async def test_rejects_multimodal_content_anywhere_in_request(api):
    body = request_body()
    body["messages"].insert(0, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]})
    response = await api[0].post("/v1/chat/completions", headers=HEADERS, json=body)
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


async def test_stream_heartbeats_while_queued(monkeypatch, api):
    class DelayedQueue:
        async def submit(self, mapping, operation):
            await __import__("asyncio").sleep(0.03)
            from hermes_bridge.domain.models import TurnEvent
            yield TurnEvent("done", mapping.lineage_key, 1, "message.complete", text="later", operation_id=operation.id)

    monkeypatch.setattr(openai, "SSE_HEARTBEAT_SECONDS", 0.005)
    api[0]._transport.app.state.lineage_queue = DelayedQueue()
    response = await api[0].post("/v1/chat/completions", headers={**HEADERS, "X-Hermes-User-Message-Id": "queued"}, json=request_body())
    assert ": keep-alive\n\n" in response.text


async def test_create_app_wires_openai_dependencies_and_closes_them(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert app.state.mapping_repository is not None
        assert app.state.operation_repository is not None
        assert app.state.lineage_queue is not None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/models", headers={"Authorization": f"Bearer {settings.bridge_secret}"})
        assert response.status_code == 200


@pytest.mark.parametrize("unsupported", [
    {"functions": [{"name": "legacy"}]},
    {"function_call": "auto"},
    {"modalities": ["text", "audio"]},
    {"audio": {"voice": "alloy", "format": "wav"}},
    {"messages": [{"role": "user", "content": "hello", "files": [{"id": "file-1"}]}]},
    {"messages": [{"role": "user", "content": "hello", "attachments": [{"id": "file-1"}]}]},
    {"messages": [{"role": "alien", "content": "ignored"}, {"role": "user", "content": "hello"}]},
    {"messages": [{"role": "user", "content": [{"type": "text", "text": "hello", "image_url": {"url": "x"}}]}]},
])
async def test_text_only_parser_fails_closed_for_unsupported_capabilities(api, unsupported):
    body = request_body()
    body.update(unsupported)
    response = await api[0].post("/v1/chat/completions", headers={**HEADERS, "X-Hermes-User-Message-Id": str(unsupported)}, json=body)
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


async def test_standard_openwebui_text_sampling_fields_remain_supported(api):
    body = request_body(temperature=0.7, top_p=0.9, max_tokens=200, stream_options={"include_usage": True})
    response = await api[0].post("/v1/chat/completions", headers={**HEADERS, "X-Hermes-User-Message-Id": "sampling"}, json=body)
    assert response.status_code == 200 and "data: [DONE]" in response.text
