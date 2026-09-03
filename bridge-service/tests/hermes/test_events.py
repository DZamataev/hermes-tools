from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hermes_bridge.connector.protocol import AcceptedFrame, HermesEventFrame
from hermes_bridge.hermes.events import EventKind, normalize_event


def connector_event(
    event_type: str,
    *,
    seq: int | None = 44,
    data: dict | None = None,
    operation_id: str | None = None,
    frame_id: str = "frame-1",
) -> HermesEventFrame:
    return HermesEventFrame.model_validate(
        {
            "protocol": 1,
            "kind": "hermes_event",
            "id": frame_id,
            "correlation_id": "correlation-1",
            "sent_at": datetime.now(timezone.utc),
            "payload": {
                "operation_id": operation_id,
                "connection_id": "local",
                "profile": "default",
                "session_id": "runtime-1",
                "seq": seq,
                "event_type": event_type,
                "data": data or {},
            },
        }
    )


def test_session_info_rotates_stored_tip():
    event = normalize_event(
        connector_event(
            "session.info",
            data={"session_id": "runtime-1", "stored_session_id": "tip-2"},
        ),
        connector_epoch="epoch-1",
    )

    assert event is not None
    assert event.kind is EventKind.SESSION_INFO
    assert event.stored_session_id == "tip-2"


def test_event_identity_prefers_stable_source_id_then_epoch_session_sequence():
    stable = normalize_event(
        connector_event("message.delta", data={"event_id": "source-7", "text": "hi"}),
        connector_epoch="epoch-1",
    )
    fallback = normalize_event(
        connector_event("message.delta", data={"text": "hi"}),
        connector_epoch="epoch-1",
    )

    assert stable is not None
    assert stable.event_id == "source:local:default:runtime-1:source-7"
    assert fallback is not None
    assert fallback.event_id == "epoch:epoch-1:local:default:runtime-1:44"
    assert fallback.text == "hi"


def test_non_hermes_connector_frames_are_ignored():
    frame = AcceptedFrame.model_validate(
        {
            "protocol": 1,
            "kind": "accepted",
            "id": "accepted-1",
            "correlation_id": "op-1",
            "sent_at": datetime.now(timezone.utc),
            "payload": {"operation_id": "op-1", "runtime_session_id": "runtime-1"},
        }
    )
    assert normalize_event(frame, connector_epoch="epoch-1") is None


def test_invalid_event_payload_is_not_promoted_to_text_or_tip():
    event = normalize_event(
        connector_event(
            "reasoning.delta",
            seq=None,
            data={"text": ["not", "text"], "stored_session_id": 7},
        ),
        connector_epoch="epoch-1",
    )
    assert event is not None
    assert event.text is None
    assert event.stored_session_id is None
    assert event.sequence == 0


def test_fallback_identity_is_scoped_by_route():
    first = connector_event("message.delta", data={"text": "one"})
    second = first.model_copy(
        update={
            "payload": first.payload.model_copy(
                update={"connection_id": "remote", "profile": "other"}
            )
        }
    )
    first_event = normalize_event(first, connector_epoch="epoch-1")
    second_event = normalize_event(second, connector_epoch="epoch-1")
    assert first_event is not None and second_event is not None
    assert first_event.event_id != second_event.event_id


@pytest.mark.parametrize("event_type", [kind.value for kind in EventKind])
def test_every_protocol_allowlist_event_normalizes(event_type):
    event = normalize_event(connector_event(event_type), connector_epoch="epoch-1")
    assert event is not None and event.kind.value == event_type
