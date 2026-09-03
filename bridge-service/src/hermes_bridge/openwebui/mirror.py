"""Idempotent projection of authoritative Hermes history into native chats."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.domain.ports import MappingStore
from hermes_bridge.hermes.client import HermesMessage
from hermes_bridge.hermes.projection import ChatProjection, project_history
from hermes_bridge.openwebui.client import OpenWebUIClient


@dataclass(frozen=True)
class MirrorResult:
    chat_id: str
    created: bool
    drift_message_ids: tuple[str, ...]
    reload_sent: bool


class MirrorService:
    def __init__(self, client: OpenWebUIClient, mappings: MappingStore) -> None:
        self._client = client
        self._mappings = mappings

    async def verify(self) -> None:
        """Verify owner-scoped credentials and ensure the bridge folder exists."""
        await self._client.ensure_folder("Hermes")

    async def reconcile(
        self, identity: SessionIdentity, messages: Sequence[HermesMessage]
    ) -> MirrorResult:
        projection = project_history(identity, messages)
        mapping = await self._mappings.upsert_session(identity)
        folder_id = await self._client.ensure_folder("Hermes")
        created = False

        if mapping.openwebui_chat_id is None:
            recovered_id = await self._find_existing(identity.lineage_key, folder_id)
            if recovered_id is None:
                chat_id = await self._client.create_chat(
                    projection, folder_id, lineage_key=identity.lineage_key
                )
                created = True
            else:
                chat_id = recovered_id
            mapping = await self._mappings.attach_chat(identity.lineage_key, chat_id)
        else:
            chat_id = mapping.openwebui_chat_id

        if created:
            drift: tuple[str, ...] = ()
        else:
            existing = await self._client.read_chat(chat_id)
            payload, drift = _reconciled_payload(
                existing, projection, folder_id, identity.lineage_key
            )
            await self._client.update_chat(chat_id, payload)

        snapshot_hash = _snapshot_hash(projection)
        last_message_id = messages[-1].id if messages else None
        await self._mappings.update_snapshot(
            identity.lineage_key,
            last_hermes_message_id=last_message_id,
            snapshot_hash=snapshot_hash,
        )
        reload_sent = await self._client.emit_reload(
            chat_id, projection.current_id or "bridge-sync"
        )
        return MirrorResult(chat_id, created, drift, reload_sent)

    async def _find_existing(self, lineage_key: str, folder_id: str) -> str | None:
        for summary in [chat async for chat in self._client.iter_chats(include_folders=True)]:
            if summary.get("folder_id") != folder_id:
                continue
            chat_id = summary["id"]
            chat = await self._client.read_chat(chat_id)
            if (chat.get("variables") or {}).get("hermes_lineage_key") == lineage_key:
                return chat_id
        return None


def _reconciled_payload(
    existing: dict[str, Any],
    projection: ChatProjection,
    folder_id: str,
    lineage_key: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    existing_chat = existing.get("chat")
    if not isinstance(existing_chat, dict):
        existing_chat = {}
    current_history = existing_chat.get("history")
    current_messages = (
        current_history.get("messages", {}) if isinstance(current_history, dict) else {}
    )
    if not isinstance(current_messages, dict):
        current_messages = {}

    drift_ids = tuple(
        sorted(message_id for message_id in current_messages if message_id not in projection.history)
    )
    merged_messages = dict(current_messages)
    merged_messages.update(projection.history)
    chat = dict(existing_chat)
    chat.update(
        {
            "title": projection.title,
            "models": ["hermes-live"],
            "history": {
                **(current_history if isinstance(current_history, dict) else {}),
                "currentId": projection.current_id,
                "messages": merged_messages,
            },
        }
    )
    variables = dict(existing.get("variables") or {})
    variables["hermes_lineage_key"] = lineage_key
    return {"chat": chat, "variables": variables, "folder_id": folder_id}, drift_ids


def _snapshot_hash(projection: ChatProjection) -> str:
    canonical = json.dumps(
        {
            "title": projection.title,
            "current_id": projection.current_id,
            "history": projection.history,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()
