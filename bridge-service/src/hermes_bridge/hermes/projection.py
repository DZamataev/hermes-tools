"""Deterministic projection of Hermes histories into OpenWebUI message graphs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.hermes.client import HermesMessage


OPENWEBUI_MESSAGE_NAMESPACE = UUID("a65797e2-48df-58c4-9022-bc8e556249c2")
PROJECTION_VERSION = 2


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
    active_assistant_id: str | None = None

    for message in source_messages:
        if message.role == "user":
            active_assistant_id = None
            message_id = openwebui_message_id(identity, message.id)
            parent_id = _append_node(
                history,
                ordered_message_ids,
                message_id=message_id,
                parent_id=parent_id,
                role="user",
                content=message.content,
                timestamp=message.created_at,
            )
            continue

        if message.role not in {"assistant", "tool"}:
            continue

        if active_assistant_id is None:
            active_assistant_id = openwebui_message_id(identity, message.id)
            parent_id = _append_node(
                history,
                ordered_message_ids,
                message_id=active_assistant_id,
                parent_id=parent_id,
                role="assistant",
                content="",
                timestamp=message.created_at,
                output=[],
            )

        assistant = history[active_assistant_id]
        output = assistant["output"]
        if message.role == "assistant":
            _append_assistant_output(output, message)
            if message.content:
                assistant["content"] = "\n\n".join(
                    part for part in (assistant["content"], message.content) if part
                )
        else:
            _append_tool_output(output, message)

    return ChatProjection(
        title=identity.title,
        history=history,
        current_id=parent_id,
        ordered_message_ids=tuple(ordered_message_ids),
        source_messages=source_messages,
        bridge_owned_message_ids=frozenset(ordered_message_ids),
    )


def _append_node(
    history: dict[str, dict[str, Any]],
    ordered_message_ids: list[str],
    *,
    message_id: str,
    parent_id: str | None,
    role: str,
    content: str,
    timestamp: int | float | str,
    output: list[dict[str, Any]] | None = None,
) -> str:
    if message_id in history:
        raise ValueError(f"duplicate projected message ID: {message_id}")
    node: dict[str, Any] = {
        "id": message_id,
        "parentId": parent_id,
        "childrenIds": [],
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "done": True,
    }
    if output is not None:
        node["output"] = output
    history[message_id] = node
    if parent_id is not None:
        history[parent_id]["childrenIds"] = [message_id]
    ordered_message_ids.append(message_id)
    return message_id


def _append_assistant_output(
    output: list[dict[str, Any]], message: HermesMessage
) -> None:
    if message.reasoning:
        output.append(
            {
                "type": "reasoning",
                "id": f"{message.id}-reasoning",
                "status": "completed",
                "summary": [{"type": "summary_text", "text": message.reasoning}],
            }
        )
    if message.content:
        output.append(
            {
                "type": "message",
                "id": message.id,
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": message.content}],
            }
        )
    for index, call in enumerate(message.tool_calls):
        call_id, name, arguments = _tool_call_fields(call, message.id, index)
        output.append(
            {
                "type": "function_call",
                "id": call_id,
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
                "status": "completed",
            }
        )


def _append_tool_output(
    output: list[dict[str, Any]], message: HermesMessage
) -> None:
    call_id = message.tool_call_id or f"tool-{message.id}"
    if not any(
        item.get("type") == "function_call" and item.get("call_id") == call_id
        for item in output
    ):
        output.append(
            {
                "type": "function_call",
                "id": call_id,
                "call_id": call_id,
                "name": message.tool_name or "tool",
                "arguments": "{}",
                "status": "completed",
            }
        )
    output.append(
        {
            "type": "function_call_output",
            "id": message.id,
            "call_id": call_id,
            "status": "completed",
            "output": [{"type": "output_text", "text": message.content}],
        }
    )


def _tool_call_fields(
    call: Mapping[str, Any], message_id: str, index: int
) -> tuple[str, str, str]:
    function = call.get("function")
    if not isinstance(function, Mapping):
        function = {}
    raw_id = call.get("id") or call.get("tool_call_id")
    call_id = raw_id if isinstance(raw_id, str) and raw_id else f"{message_id}-tool-{index}"
    raw_name = call.get("name") or call.get("tool_name") or function.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else "tool"
    arguments = function.get("arguments", call.get("arguments", call.get("args", {})))
    if isinstance(arguments, str):
        serialized_arguments = arguments
    else:
        serialized_arguments = json.dumps(
            arguments, separators=(",", ":"), ensure_ascii=False, sort_keys=True
        )
    return call_id, name, serialized_arguments
