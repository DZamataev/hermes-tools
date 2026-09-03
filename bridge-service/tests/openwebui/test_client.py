from __future__ import annotations

import json

import httpx
import pytest

from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.hermes.client import HermesMessage
from hermes_bridge.hermes.projection import project_history
from hermes_bridge.openwebui.client import OpenWebUIClient, OpenWebUIError


SESSION = SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
PROJECTION = project_history(
    SESSION, [HermesMessage("m1", "user", "hello", 1)]
)


class Recorder:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


async def test_client_creates_chat_as_api_key_owner():
    recorder = Recorder([httpx.Response(200, json={"id": "ow-chat-1"})])
    client = OpenWebUIClient(
        "http://open-webui", "sk-owner", transport=httpx.MockTransport(recorder)
    )
    chat_id = await client.create_chat(PROJECTION, "folder-hermes", lineage_key=SESSION.lineage_key)
    request = recorder.requests[0]
    payload = json.loads(request.content)
    assert chat_id == "ow-chat-1"
    assert request.url.path == "/api/v1/chats/new"
    assert request.headers["authorization"] == "Bearer sk-owner"
    assert payload["folder_id"] == "folder-hermes"
    assert payload["chat"]["models"] == ["hermes-live"]
    assert payload["variables"]["hermes_lineage_key"] == SESSION.lineage_key


async def test_ensure_folder_reuses_exact_root_and_ignores_nested_duplicate():
    recorder = Recorder(
        [
            httpx.Response(
                200,
                json=[
                    {"id": "nested", "name": "Hermes", "parent_id": "parent"},
                    {"id": "root", "name": "Hermes", "parent_id": None},
                ],
            )
        ]
    )
    client = OpenWebUIClient("http://ow", "key", transport=httpx.MockTransport(recorder))
    assert await client.ensure_folder("Hermes") == "root"
    assert len(recorder.requests) == 1


async def test_ensure_folder_creates_when_absent():
    recorder = Recorder(
        [httpx.Response(200, json=[]), httpx.Response(200, json={"id": "folder-1"})]
    )
    client = OpenWebUIClient("http://ow", "key", transport=httpx.MockTransport(recorder))
    assert await client.ensure_folder("Hermes") == "folder-1"
    assert json.loads(recorder.requests[1].content) == {"name": "Hermes", "parent_id": None}


async def test_iter_chats_uses_one_based_sixty_row_pages():
    page = [{"id": f"chat-{index}"} for index in range(60)]
    recorder = Recorder([httpx.Response(200, json=page), httpx.Response(200, json=[])])
    client = OpenWebUIClient("http://ow", "key", transport=httpx.MockTransport(recorder))
    assert len([chat async for chat in client.iter_chats()]) == 60
    assert [request.url.params["page"] for request in recorder.requests] == ["1", "2"]


async def test_auth_error_is_safe():
    recorder = Recorder([httpx.Response(401, json={"detail": "bad"})])
    client = OpenWebUIClient(
        "http://ow", "secret-owner-key", transport=httpx.MockTransport(recorder)
    )
    with pytest.raises(OpenWebUIError) as error:
        await client.ensure_folder("Hermes")
    assert "401" in str(error.value)
    assert "secret-owner-key" not in str(error.value)


async def test_reload_uses_supported_message_event_shape():
    recorder = Recorder([httpx.Response(200, json=True)])
    client = OpenWebUIClient("http://ow", "key", transport=httpx.MockTransport(recorder))
    assert await client.emit_reload("chat-1", "message-1") is True
    assert json.loads(recorder.requests[0].content) == {"type": "chat:reload", "data": {}}
