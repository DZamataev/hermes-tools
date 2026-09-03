from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
OPENWEBUI_URL = os.environ.get("HERMES_WEBUI_URL", "http://localhost:11001").rstrip("/")


def _local_value(name: str) -> str:
    path = ROOT / ".env.local"
    if not path.is_file():
        return ""
    matches: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = raw_line.partition("=")
        if separator and key.strip() == name:
            matches.append(value.strip().strip('"'))
    return matches[-1] if matches else ""


def _request(client: httpx.Client, method: str, endpoint: str, **kwargs) -> httpx.Response:
    try:
        response = client.request(method, endpoint, **kwargs)
    except httpx.RequestError as error:
        raise AssertionError(
            f"OpenWebUI is unavailable at {OPENWEBUI_URL}; start the stack before contract testing"
        ) from error
    if response.status_code in {401, 403}:
        raise AssertionError(
            "The non-empty OPENWEBUI_API_KEY was rejected; enable API keys and rotate the first administrator key"
        )
    response.raise_for_status()
    return response


def test_pinned_openwebui_native_chat_contract() -> None:
    api_key = _local_value("OPENWEBUI_API_KEY")
    if not api_key:
        pytest.skip(
            "OPENWEBUI_API_KEY is empty in .env.local; enable API keys in OpenWebUI and add the first administrator key"
        )

    suffix = uuid4().hex
    folder_id: str | None = None
    chat_id: str | None = None
    user_id = f"contract-user-{suffix}"
    assistant_id = f"contract-assistant-{suffix}"
    folder_name = f"Hermes bridge contract {suffix}"
    headers = {"Authorization": f"Bearer {api_key}"}

    with httpx.Client(base_url=OPENWEBUI_URL, headers=headers, timeout=15.0) as client:
        try:
            folder = _request(
                client,
                "POST",
                "/api/v1/folders/",
                json={"name": folder_name, "parent_id": None},
            ).json()
            folder_id = folder["id"]

            history = {
                user_id: {
                    "id": user_id,
                    "parentId": None,
                    "childrenIds": [assistant_id],
                    "role": "user",
                    "content": "contract question",
                    "timestamp": 1,
                },
                assistant_id: {
                    "id": assistant_id,
                    "parentId": user_id,
                    "childrenIds": [],
                    "role": "assistant",
                    "content": "contract answer",
                    "timestamp": 2,
                },
            }
            created = _request(
                client,
                "POST",
                "/api/v1/chats/new",
                json={
                    "chat": {
                        "title": folder_name,
                        "models": ["hermes-live"],
                        "history": {"currentId": assistant_id, "messages": history},
                    },
                    "variables": {"hermes_contract_id": suffix},
                    "folder_id": folder_id,
                },
            ).json()
            chat_id = created["id"]

            history[assistant_id]["content"] = "contract answer updated"
            _request(
                client,
                "POST",
                f"/api/v1/chats/{chat_id}",
                json={
                    "chat": {
                        "title": folder_name,
                        "models": ["hermes-live"],
                        "history": {"currentId": assistant_id, "messages": history},
                    },
                    "variables": {"hermes_contract_id": suffix},
                    "folder_id": folder_id,
                },
            )
            event = _request(
                client,
                "POST",
                f"/api/v1/chats/{chat_id}/messages/{assistant_id}/event",
                json={"type": "chat:reload", "data": {}},
            ).json()
            assert event in {True, False, None}

            restored = _request(client, "GET", f"/api/v1/chats/{chat_id}").json()
            assert restored["id"] == chat_id
            assert restored["folder_id"] == folder_id
            assert restored["chat"]["models"] == ["hermes-live"]
            assert restored["chat"]["history"]["currentId"] == assistant_id
            assert restored["chat"]["history"]["messages"][assistant_id]["content"] == (
                "contract answer updated"
            )
            assert restored["variables"]["hermes_contract_id"] == suffix
        finally:
            if chat_id is not None:
                assert _request(
                    client, "DELETE", f"/api/v1/chats/{chat_id}"
                ).json() is True
            if folder_id is not None:
                assert _request(
                    client,
                    "DELETE",
                    f"/api/v1/folders/{folder_id}",
                    params={"delete_contents": "false"},
                ).json() is True
