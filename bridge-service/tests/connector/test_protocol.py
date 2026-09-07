from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from hermes_bridge.connector.protocol import (
    APPROVALS_CAPABILITY, ApprovalRequestFrame, ApprovalResolvedFrame,
    ApprovalScanFrame, ApprovalSnapshotFrame, AuthenticationError, Challenge,
    ConnectorCompatibilityError, HelloFrame, MAX_FRAME_BYTES, ProtocolError, ReplayCompleteFrame,
    ResolveApprovalFrame, SubmitFrame, parse_incoming, sign_challenge, verify_hello,
)


NOW = "2026-09-07T18:00:00+00:00"
ROUTE = {"connection_id": "local", "profile": "default", "target_profile": "backend-default"}


def hello(secret: str, *, nonce: str = "nonce-1", timestamp: int = 1_788_400_000):
    return HelloFrame.model_validate({
        "protocol": 1, "kind": "hello", "id": "epoch-1", "correlation_id": "challenge-1",
        "sent_at": datetime.now(timezone.utc),
        "payload": {"timestamp": timestamp, "mac": sign_challenge(secret, nonce, timestamp),
                    "connector_version": "1.1.0",
                    "capabilities": ["replay_complete", APPROVALS_CAPABILITY], "routes": []},
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


def test_approval_frames_parse_to_their_strict_protocol_types():
    snapshot = parse_incoming(json.dumps({
        "protocol": 1, "kind": "approval_snapshot", "id": "event-1",
        "correlation_id": "scan-1", "sent_at": NOW,
        "payload": {
            "operation_id": "scan-operation-1", "route": ROUTE,
            "sessions": [{"runtime_session_id": "runtime-1", "stored_session_id": "stored-1", "status": "waiting"}],
            "approvals": [{
                "runtime_session_id": "runtime-1", "stored_session_id": "stored-1",
                "request_id": "request-1", "description": "Run a command", "command": "git status",
                "allow_permanent": True, "smart_denied": False,
                "choices": ["once", "session", "always", "deny"],
            }],
        },
    }))
    request = parse_incoming(json.dumps({
        "protocol": 1, "kind": "approval_request", "id": "event-2",
        "correlation_id": "event-2", "sent_at": NOW,
        "payload": {
            "route": ROUTE, "runtime_session_id": "runtime-1", "stored_session_id": "stored-1",
            "request_id": "request-1", "description": "Run a command", "command": "git status",
            "allow_permanent": False, "smart_denied": False, "choices": ["once", "deny"], "seq": 3,
        },
    }))
    resolved = parse_incoming(json.dumps({
        "protocol": 1, "kind": "approval_resolved", "id": "event-3",
        "correlation_id": "resolve-1", "sent_at": NOW,
        "payload": {"operation_id": "resolve-operation-1", "approval_id": "approval-1", "choice": "session", "accepted": True},
    }))
    scan = ApprovalScanFrame.model_validate({
        "protocol": 1, "kind": "approval_scan", "id": "command-1",
        "correlation_id": "scan-1", "sent_at": NOW,
        "payload": {"operation_id": "scan-operation-1", "route": ROUTE},
    })
    resolve = ResolveApprovalFrame.model_validate({
        "protocol": 1, "kind": "resolve_approval", "id": "command-2",
        "correlation_id": "resolve-1", "sent_at": NOW,
        "payload": {
            "operation_id": "resolve-operation-1", "route": ROUTE, "approval_id": "approval-1",
            "runtime_session_id": "runtime-1", "stored_session_id": "stored-1", "request_id": "request-1",
            "choice": "session",
        },
    })

    assert isinstance(snapshot, ApprovalSnapshotFrame)
    assert isinstance(request, ApprovalRequestFrame)
    assert isinstance(resolved, ApprovalResolvedFrame)
    assert isinstance(scan, ApprovalScanFrame)
    assert isinstance(resolve, ResolveApprovalFrame)


@pytest.mark.parametrize("choices", [["reject"], ["once", "once"], ["always"]])
def test_approval_record_rejects_unknown_duplicate_and_disallowed_permanent_choices(choices):
    frame = {
        "protocol": 1, "kind": "approval_request", "id": "event-4",
        "correlation_id": "event-4", "sent_at": NOW,
        "payload": {
            "route": ROUTE, "runtime_session_id": "runtime-1", "stored_session_id": "stored-1",
            "request_id": "request-1", "description": "Run a command", "command": "git status",
            "allow_permanent": False, "smart_denied": False, "choices": choices,
        },
    }
    with pytest.raises(ProtocolError):
        parse_incoming(json.dumps(frame))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("route", {"connection_id": "local", "profile": "default"}),
        ("seq", -1),
        ("unexpected", "field"),
    ],
)
def test_approval_request_rejects_incomplete_or_unsanitized_payloads(field, value):
    payload = {
        "route": ROUTE, "runtime_session_id": "runtime-1", "stored_session_id": "stored-1",
        "request_id": "request-1", "description": "Run a command", "command": "git status",
        "allow_permanent": False, "smart_denied": False, "choices": ["once", "deny"],
    }
    payload[field] = value
    with pytest.raises(ProtocolError):
        parse_incoming(json.dumps({
            "protocol": 1, "kind": "approval_request", "id": "event-5",
            "correlation_id": "event-5", "sent_at": NOW, "payload": payload,
        }))


def test_approval_request_rejects_a_missing_stored_session_identity():
    payload = {
        "route": ROUTE, "runtime_session_id": "runtime-1", "request_id": "request-1",
        "description": "Run a command", "command": "git status", "allow_permanent": False,
        "smart_denied": False, "choices": ["once", "deny"],
    }
    with pytest.raises(ProtocolError):
        parse_incoming(json.dumps({
            "protocol": 1, "kind": "approval_request", "id": "event-6",
            "correlation_id": "event-6", "sent_at": NOW, "payload": payload,
        }))


def test_oversized_approval_command_is_rejected_before_frame_parsing():
    raw = json.dumps({
        "protocol": 1, "kind": "approval_scan", "id": "command-3",
        "correlation_id": "scan-1", "sent_at": NOW,
        "payload": {"operation_id": "scan-operation-1", "route": ROUTE, "padding": "x" * MAX_FRAME_BYTES},
    })
    with pytest.raises(ProtocolError, match="1 MiB"):
        parse_incoming(raw)


def test_hello_requires_the_approvals_capability(settings):
    challenge = Challenge("nonce-1")
    incompatible = hello(settings.bridge_secret)
    incompatible.payload.capabilities = ["replay_complete"]
    with pytest.raises(ConnectorCompatibilityError, match="capability"):
        verify_hello(incompatible, challenge, settings, now=1_788_400_000)
