from __future__ import annotations

from dataclasses import replace

import pytest

from hermes_bridge.domain.models import SessionIdentity, SessionMapping, utc_now
from hermes_bridge.hermes.client import HermesMessage
from hermes_bridge.openwebui.mirror import MirrorService


SESSION = SessionIdentity("local", "default", "root-1", "tip-1", "Hermes title")
MESSAGES = [HermesMessage("m1", "user", "hello", 1), HermesMessage("m2", "assistant", "hi", 2)]


def mapping(chat_id: str | None = None) -> SessionMapping:
    now = utc_now()
    return SessionMapping(
        SESSION.connection_id, SESSION.profile, SESSION.lineage_root_id,
        SESSION.stored_session_id, SESSION.title, chat_id, None, None, None, now, now
    )


class FakeMappings:
    def __init__(self, chat_id: str | None = None) -> None:
        self.value = mapping(chat_id)
        self.snapshots: list[tuple[str | None, str]] = []

    async def upsert_session(self, identity):
        self.value = replace(
            self.value, stored_session_id=identity.stored_session_id, title=identity.title
        )
        return self.value

    async def attach_chat(self, lineage_key, chat_id):
        self.value = replace(self.value, openwebui_chat_id=chat_id)
        return self.value

    async def by_chat_id(self, chat_id):
        return self.value if self.value.openwebui_chat_id == chat_id else None

    async def by_lineage_key(self, lineage_key):
        return self.value if self.value.lineage_key == lineage_key else None

    async def update_snapshot(self, lineage_key, *, last_hermes_message_id, snapshot_hash):
        self.snapshots.append((last_hermes_message_id, snapshot_hash))
        self.value = replace(
            self.value,
            last_hermes_message_id=last_hermes_message_id,
            last_snapshot_hash=snapshot_hash,
        )
        return self.value


class FakeClient:
    def __init__(self) -> None:
        self.created_chat_count = 0
        self.reload_events: list[dict] = []
        self.updated: list[dict] = []
        self.chats: dict[str, dict] = {}
        self.fail_update = False
        self.reload_result = True

    async def ensure_folder(self, name):
        assert name == "Hermes"
        return "folder-hermes"

    async def iter_chats(self, include_folders=True):
        for chat in self.chats.values():
            yield {"id": chat["id"], "folder_id": chat.get("folder_id")}

    async def create_chat(self, projection, folder_id, *, lineage_key=""):
        self.created_chat_count += 1
        chat_id = f"chat-{self.created_chat_count}"
        self.chats[chat_id] = {
            "id": chat_id,
            "folder_id": folder_id,
            "variables": {"hermes_lineage_key": lineage_key},
            "chat": {"title": projection.title, "models": ["hermes-live"], "history": {"currentId": projection.current_id, "messages": dict(projection.history)}},
        }
        return chat_id

    async def read_chat(self, chat_id):
        return self.chats[chat_id]

    async def update_chat(self, chat_id, payload):
        if self.fail_update:
            raise RuntimeError("lost update")
        self.updated.append(payload)
        self.chats[chat_id] = {"id": chat_id, **payload}

    async def emit_reload(self, chat_id, message_id):
        self.reload_events.append({"chat_id": chat_id, "message_id": message_id, "type": "chat:reload"})
        return self.reload_result


async def test_reconcile_existing_chat_is_idempotent():
    mappings, client = FakeMappings(), FakeClient()
    mirror = MirrorService(client, mappings)
    first = await mirror.reconcile(SESSION, MESSAGES)
    second = await mirror.reconcile(SESSION, MESSAGES)
    assert first.chat_id == second.chat_id
    assert client.created_chat_count == 1
    assert client.reload_events[-1]["type"] == "chat:reload"
    assert len(mappings.snapshots) == 2


async def test_recovers_lost_create_response_by_lineage_marker():
    mappings, client = FakeMappings(), FakeClient()
    client.chats["orphan"] = {
        "id": "orphan", "folder_id": "folder-hermes",
        "variables": {"hermes_lineage_key": SESSION.lineage_key},
        "chat": {"history": {"messages": {}}},
    }
    result = await MirrorService(client, mappings).reconcile(SESSION, MESSAGES)
    assert result.chat_id == "orphan"
    assert client.created_chat_count == 0
    assert mappings.value.openwebui_chat_id == "orphan"


async def test_existing_title_and_bridge_message_are_repaired_but_unknown_node_survives():
    mappings, client = FakeMappings("chat-1"), FakeClient()
    client.chats["chat-1"] = {
        "id": "chat-1", "folder_id": "folder-hermes", "variables": {},
        "chat": {"title": "local title", "history": {"currentId": "local", "messages": {"local": {"id": "local", "role": "user", "content": "keep"}}}},
    }
    result = await MirrorService(client, mappings).reconcile(SESSION, MESSAGES)
    payload = client.updated[-1]
    assert payload["chat"]["title"] == SESSION.title
    assert payload["chat"]["history"]["messages"]["local"]["content"] == "keep"
    assert result.drift_message_ids == ("local",)


async def test_reconcile_removes_only_recorded_stale_bridge_nodes():
    mappings, client = FakeMappings("chat-1"), FakeClient()
    client.chats["chat-1"] = {
        "id": "chat-1",
        "folder_id": "folder-hermes",
        "variables": {
            "hermes_lineage_key": SESSION.lineage_key,
            "hermes_bridge_message_ids": ["old-bridge"],
        },
        "chat": {
            "history": {
                "currentId": "old-bridge",
                "messages": {
                    "old-bridge": {
                        "id": "old-bridge",
                        "role": "assistant",
                        "content": "stale",
                    },
                    "local": {"id": "local", "role": "user", "content": "keep"},
                },
            }
        },
    }

    result = await MirrorService(client, mappings).reconcile(SESSION, MESSAGES)

    payload = client.updated[-1]
    message_ids = payload["chat"]["history"]["messages"]
    assert "old-bridge" not in message_ids
    assert message_ids["local"]["content"] == "keep"
    assert result.drift_message_ids == ("local",)
    assert set(payload["variables"]["hermes_bridge_message_ids"]) == (
        set(message_ids) - {"local"}
    )


async def test_snapshot_watermark_changes_only_after_durable_update():
    mappings, client = FakeMappings("chat-1"), FakeClient()
    client.chats["chat-1"] = {"id": "chat-1", "folder_id": "folder-hermes", "variables": {}, "chat": {}}
    client.fail_update = True
    with pytest.raises(RuntimeError, match="lost update"):
        await MirrorService(client, mappings).reconcile(SESSION, MESSAGES)
    assert mappings.snapshots == []


async def test_reload_failure_is_reported_without_rolling_back_snapshot():
    mappings, client = FakeMappings(), FakeClient()
    client.reload_result = False
    result = await MirrorService(client, mappings).reconcile(SESSION, [])
    assert result.reload_sent is False
    assert mappings.snapshots
    assert client.reload_events[-1]["message_id"] == "bridge-sync"
