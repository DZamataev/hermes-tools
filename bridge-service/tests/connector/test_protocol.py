from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from hermes_bridge.connector.protocol import (
    AuthenticationError, Challenge, HelloFrame, MAX_FRAME_BYTES, ProtocolError,
    ReplayCompleteFrame, SubmitFrame, parse_incoming, sign_challenge, verify_hello,
)


def hello(secret: str, *, nonce: str = "nonce-1", timestamp: int = 1_788_400_000):
    return HelloFrame.model_validate({
        "protocol": 1, "kind": "hello", "id": "epoch-1", "correlation_id": "challenge-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {"timestamp": timestamp, "mac": sign_challenge(secret, nonce, timestamp),
                    "connector_version": "1.0.0", "routes": []},
    })


def test_challenge_signature_is_deterministic_and_url_safe():
    signature = sign_challenge("s" * 32, "nonce-1", 1_788_400_000)
    assert signature == sign_challenge("s" * 32, "nonce-1", 1_788_400_000)
    assert "+" not in signature and "/" not in signature and "=" not in signature


def test_hello_rejects_wrong_expired_and_replayed_challenges(settings):
    challenge = Challenge("nonce-1", issued_at=1_788_400_000)
    with pytest.raises(AuthenticationError, match="authentication"):
        verify_hello(hello("wrong" * 8), challenge, settings, now=1_788_400_000)
    expired = Challenge("nonce-1")
    with pytest.raises(AuthenticationError, match="expired"):
        verify_hello(hello(settings.bridge_secret), expired, settings, now=1_788_400_031)
    used = Challenge("nonce-1")
    verify_hello(hello(settings.bridge_secret), used, settings, now=1_788_400_000)
    with pytest.raises(AuthenticationError, match="consumed"):
        verify_hello(hello(settings.bridge_secret), used, settings, now=1_788_400_000)


def test_submit_rejects_queued_false_and_unknown_rpc_shape():
    base = {
        "protocol": 1, "kind": "submit", "id": "command", "correlation_id": "correlation",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"operation_id": "op", "route": {"connection_id": "local", "profile": "p", "target_profile": "p"},
                    "stored_session_id": "tip", "text": "hello", "queued": False},
    }
    with pytest.raises(ValidationError):
        SubmitFrame.model_validate(base)
    base["payload"]["queued"] = True
    base["payload"]["method"] = "dangerous.rpc"
    with pytest.raises(ValidationError):
        SubmitFrame.model_validate(base)


def test_parse_rejects_unknown_protocol_kind_and_oversized_payload():
    with pytest.raises(ProtocolError):
        parse_incoming(json.dumps({"protocol": 2, "kind": "hello"}))
    with pytest.raises(ProtocolError):
        parse_incoming(json.dumps({"protocol": 1, "kind": "rpc"}))
    with pytest.raises(ProtocolError, match="1 MiB"):
        parse_incoming(b"x" * (MAX_FRAME_BYTES + 1))


def test_replay_complete_is_a_typed_protocol_v1_frame():
    frame = parse_incoming(json.dumps({
        "protocol": 1,
        "kind": "replay_complete",
        "id": "complete-1",
        "correlation_id": "op-1",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "payload": {"operation_id": "op-1", "after_seq": 7},
    }))
    assert isinstance(frame, ReplayCompleteFrame)
    assert frame.payload.after_seq == 7
