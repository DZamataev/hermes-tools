"""Immutable domain records for the Hermes/OpenWebUI bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC value."""
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class OperationState(StrEnum):
    PENDING = "pending"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    STREAMING = "streaming"
    COMPLETED = "completed"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    REJECTED = "rejected"


class InvalidTransition(ValueError):
    """Raised when an operation is asked to take an invalid state edge."""


VALID_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.PENDING: frozenset({OperationState.OFFERED, OperationState.REJECTED}),
    OperationState.OFFERED: frozenset(
        {
            OperationState.ACCEPTED,
            OperationState.DELIVERY_UNCERTAIN,
            OperationState.REJECTED,
        }
    ),
    OperationState.ACCEPTED: frozenset(
        {
            OperationState.STREAMING,
            OperationState.COMPLETED,
            OperationState.DELIVERY_UNCERTAIN,
        }
    ),
    OperationState.STREAMING: frozenset(
        {OperationState.COMPLETED, OperationState.DELIVERY_UNCERTAIN}
    ),
    OperationState.DELIVERY_UNCERTAIN: frozenset(
        {OperationState.ACCEPTED, OperationState.COMPLETED, OperationState.REJECTED}
    ),
    OperationState.COMPLETED: frozenset(),
    OperationState.REJECTED: frozenset(),
}


def transition_is_valid(current: OperationState, target: OperationState) -> bool:
    return target in VALID_TRANSITIONS[current]


@dataclass(frozen=True)
class SessionIdentity:
    connection_id: str
    profile: str
    lineage_root_id: str
    stored_session_id: str
    title: str

    @property
    def lineage_key(self) -> str:
        return f"{self.connection_id}:{self.profile}:{self.lineage_root_id}"


@dataclass(frozen=True)
class SessionMapping:
    connection_id: str
    profile: str
    lineage_root_id: str
    stored_session_id: str
    title: str
    openwebui_chat_id: str | None
    last_hermes_message_id: str | None
    last_event_seq: int | None
    last_snapshot_hash: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))

    @property
    def lineage_key(self) -> str:
        return f"{self.connection_id}:{self.profile}:{self.lineage_root_id}"

    @property
    def watermark(self) -> int | None:
        """Compatibility name for the persisted per-lineage event position."""
        return self.last_event_seq


@dataclass(frozen=True)
class TurnRequest:
    chat_id: str
    user_message_id: str
    assistant_message_id: str
    text: str
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _as_utc(self.created_at))


@dataclass(frozen=True)
class Operation:
    id: UUID
    chat_id: str
    lineage_key: str | None
    user_message_id: str
    assistant_message_id: str
    text: str
    state: OperationState
    result_text: str | None
    error_code: str | None
    error_message: str | None
    last_event_seq: int | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _as_utc(self.created_at))
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))


@dataclass(frozen=True)
class TurnEvent:
    event_id: str
    lineage_key: str
    sequence: int
    kind: str
    text: str | None = None
    operation_id: UUID | None = None
    stored_session_id: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at", _as_utc(self.occurred_at))
