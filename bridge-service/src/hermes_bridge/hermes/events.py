"""Validation and stable normalization of allowlisted Desktop events."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from hermes_bridge.connector.protocol import ConnectorEvent, HermesEventFrame
from hermes_bridge.domain.models import TurnEvent


class EventKind(StrEnum):
    MESSAGE_START = "message.start"
    MESSAGE_DELTA = "message.delta"
    REASONING_DELTA = "reasoning.delta"
    TOOL_START = "tool.start"
    TOOL_PROGRESS = "tool.progress"
    TOOL_COMPLETE = "tool.complete"
    MESSAGE_COMPLETE = "message.complete"
    SESSION_INFO = "session.info"
    ERROR = "error"


def normalize_event(
    frame: ConnectorEvent, *, connector_epoch: str | None = None
) -> TurnEvent | None:
    """Convert one typed connector event without reflecting arbitrary payload data."""
    if not isinstance(frame, HermesEventFrame):
        return None
    payload = frame.payload
    kind = EventKind(payload.event_type)
    source_id = _nonempty(payload.data.get("source_event_id")) or _nonempty(
        payload.data.get("event_id")
    )
    if source_id is not None:
        event_id = (
            f"source:{payload.connection_id}:{payload.profile}:"
            f"{payload.session_id}:{source_id}"
        )
    elif payload.seq is not None:
        epoch = connector_epoch or frame.correlation_id
        event_id = (
            f"epoch:{epoch}:{payload.connection_id}:{payload.profile}:"
            f"{payload.session_id}:{payload.seq}"
        )
    else:
        event_id = (
            f"frame:{payload.connection_id}:{payload.profile}:"
            f"{payload.session_id}:{frame.id}"
        )

    operation_id = None
    if payload.operation_id is not None:
        operation_id = UUID(payload.operation_id)
    text = _nonempty(payload.data.get("text"))
    stored_session_id = _nonempty(payload.data.get("stored_session_id"))
    return TurnEvent(
        event_id=event_id,
        lineage_key=(
            f"{payload.connection_id}:{payload.profile}:runtime:{payload.session_id}"
        ),
        sequence=payload.seq or 0,
        kind=kind,
        text=text,
        operation_id=operation_id,
        stored_session_id=stored_session_id,
        occurred_at=frame.sent_at,
        connector_epoch=connector_epoch,
    )


def _nonempty(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
