"""Deterministic projection of Hermes histories into OpenWebUI message graphs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.hermes.client import HermesMessage


OPENWEBUI_MESSAGE_NAMESPACE = UUID("a65797e2-48df-58c4-9022-bc8e556249c2")


@dataclass(frozen=True)
class ChatProjection:
    """The bridge-owned portion of an OpenWebUI chat transcript."""

    title: str
    history: dict[str, dict[str, Any]]
    current_id: str | None
    ordered_message_ids: tuple[str, ...]
    source_messages: tuple[HermesMessage, ...]
    bridge_owned_message_ids: frozenset[str]


def openwebui_message_id(identity: SessionIdentity, hermes_message_id: str) -> str:
    """Return the stable OpenWebUI ID for one physical Hermes message."""
    key = (
        f"{identity.connection_id}:{identity.profile}:{identity.lineage_root_id}:"
        f"{hermes_message_id}"
    )
    return str(uuid5(OPENWEBUI_MESSAGE_NAMESPACE, key))


def project_history(
    identity: SessionIdentity, messages: Sequence[HermesMessage]
) -> ChatProjection:
    """Project oldest-first Hermes messages into one deterministic linear graph."""
    source_messages = tuple(messages)
    history: dict[str, dict[str, Any]] = {}
    ordered_message_ids: list[str] = []
    parent_id: str | None = None

    for message in source_messages:
        message_id = openwebui_message_id(identity, message.id)
        if message_id in history:
            raise ValueError(f"duplicate Hermes message ID in history: {message.id}")
        history[message_id] = {
            "id": message_id,
            "parentId": parent_id,
            "childrenIds": [],
            "role": message.role,
            "content": message.content,
            "timestamp": message.created_at,
        }
        if parent_id is not None:
            history[parent_id]["childrenIds"] = [message_id]
        ordered_message_ids.append(message_id)
        parent_id = message_id

    return ChatProjection(
        title=identity.title,
        history=history,
        current_id=parent_id,
        ordered_message_ids=tuple(ordered_message_ids),
        source_messages=source_messages,
        bridge_owned_message_ids=frozenset(ordered_message_ids),
    )
