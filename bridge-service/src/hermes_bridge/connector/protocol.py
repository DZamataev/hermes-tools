"""Version-one typed frames and challenge authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from hermes_bridge.config import Settings


PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1024 * 1024


class ProtocolError(ValueError):
    pass


class AuthenticationError(ProtocolError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileRoute(StrictModel):
    connection_id: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    target_profile: str = Field(min_length=1)


class BaseFrame(StrictModel):
    protocol: Literal[1] = 1
    kind: str
    id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    sent_at: datetime

    @field_validator("sent_at")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("sent_at must include a timezone")
        return value


class ChallengePayload(StrictModel):
    nonce: str = Field(min_length=1)


class ChallengeFrame(BaseFrame):
    kind: Literal["challenge"] = "challenge"
    payload: ChallengePayload


class HelloPayload(StrictModel):
    timestamp: int
    mac: str = Field(min_length=1)
    connector_version: str = Field(min_length=1)
    routes: list[ProfileRoute]


class HelloFrame(BaseFrame):
    kind: Literal["hello"] = "hello"
    payload: HelloPayload


class HeartbeatPayload(StrictModel):
    epoch: str = Field(min_length=1)


class HeartbeatFrame(BaseFrame):
    kind: Literal["heartbeat"] = "heartbeat"
    payload: HeartbeatPayload


class SubmitPayload(StrictModel):
    operation_id: str = Field(min_length=1)
    route: ProfileRoute
    stored_session_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    queued: Literal[True]


class SubmitFrame(BaseFrame):
    kind: Literal["submit"] = "submit"
    payload: SubmitPayload


class ReplayPayload(StrictModel):
    operation_id: str = Field(min_length=1)
    route: ProfileRoute
    runtime_session_id: str = Field(min_length=1)
    after_seq: int = Field(ge=0)


class ReplayFrame(BaseFrame):
    kind: Literal["replay"] = "replay"
    payload: ReplayPayload


class ReplayGapPayload(StrictModel):
    operation_id: str = Field(min_length=1)
    after_seq: int = Field(ge=0)
    oldest_available: int = Field(ge=0)


class ReplayGapFrame(BaseFrame):
    kind: Literal["replay_gap"] = "replay_gap"
    payload: ReplayGapPayload


class ReplayCompletePayload(StrictModel):
    operation_id: str = Field(min_length=1)
    after_seq: int = Field(ge=0)


class ReplayCompleteFrame(BaseFrame):
    kind: Literal["replay_complete"] = "replay_complete"
    payload: ReplayCompletePayload


class AcceptedPayload(StrictModel):
    operation_id: str = Field(min_length=1)
    runtime_session_id: str = Field(min_length=1)


class AcceptedFrame(BaseFrame):
    kind: Literal["accepted"] = "accepted"
    payload: AcceptedPayload


class HermesEventPayload(StrictModel):
    operation_id: str | None = None
    connection_id: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    seq: int | None = Field(default=None, ge=0)
    event_type: Literal[
        "message.start", "message.delta", "reasoning.delta", "tool.start",
        "tool.progress", "tool.complete", "message.complete", "session.info", "error"
    ]
    data: dict[str, Any]


class HermesEventFrame(BaseFrame):
    kind: Literal["hermes_event"] = "hermes_event"
    payload: HermesEventPayload


class CommandErrorPayload(StrictModel):
    operation_id: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    acceptance_unknown: bool


class CommandErrorFrame(BaseFrame):
    kind: Literal["command_error"] = "command_error"
    payload: CommandErrorPayload


ConnectorCommand = Annotated[Union[SubmitFrame, ReplayFrame], Field(discriminator="kind")]
ConnectorEvent = Annotated[
    Union[
        AcceptedFrame,
        ReplayGapFrame,
        ReplayCompleteFrame,
        HermesEventFrame,
        CommandErrorFrame,
    ],
    Field(discriminator="kind"),
]
IncomingFrame = Annotated[
    Union[
        HelloFrame,
        HeartbeatFrame,
        AcceptedFrame,
        ReplayGapFrame,
        ReplayCompleteFrame,
        HermesEventFrame,
        CommandErrorFrame,
    ],
    Field(discriminator="kind"),
]

_incoming_adapter = TypeAdapter(IncomingFrame)


class Challenge:
    def __init__(self, nonce: str, *, issued_at: int | None = None) -> None:
        self.nonce = nonce
        self.issued_at = int(time.time()) if issued_at is None else issued_at
        self.used = False


def sign_challenge(secret: str, nonce: str, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode(), f"{nonce}\n{timestamp}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verify_hello(
    frame: HelloFrame,
    challenge: Challenge,
    settings: Settings,
    *,
    now: int | None = None,
) -> None:
    current = int(time.time()) if now is None else now
    if challenge.used:
        raise AuthenticationError("challenge already consumed")
    challenge.used = True
    if abs(current - frame.payload.timestamp) > 30:
        raise AuthenticationError("hello timestamp expired")
    expected = sign_challenge(settings.bridge_secret, challenge.nonce, frame.payload.timestamp)
    if not hmac.compare_digest(frame.payload.mac, expected):
        raise AuthenticationError("authentication failed")


def parse_incoming(raw: str | bytes) -> IncomingFrame:
    encoded = raw.encode() if isinstance(raw, str) else raw
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProtocolError("frame exceeds 1 MiB")
    try:
        return _incoming_adapter.validate_json(encoded)
    except Exception as error:
        raise ProtocolError("invalid connector frame") from error


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
