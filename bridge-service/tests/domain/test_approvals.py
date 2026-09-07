from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from hermes_bridge.domain.models import (
    ApprovalState,
    PendingApproval,
    approval_transition_is_valid,
    stable_approval_id,
)


def test_approval_identity_is_stable_and_excludes_sensitive_text():
    identity = ("local", "default", "backend-default", "lineage-1", "request-1")
    first = stable_approval_id(*identity)
    assert first == stable_approval_id(*identity)
    assert first.startswith("ha_") and len(first) == 35
    assert "request-1" not in first
    assert "git status" not in first
    for index in range(len(identity)):
        changed = list(identity)
        changed[index] += "-other"
        assert stable_approval_id(*changed) != first


def test_only_declared_approval_transitions_are_valid():
    allowed = {
        ("pending", "resolving"),
        ("pending", "resolved_external"),
        ("pending", "expired"),
        ("resolving", "resolved"),
        ("resolving", "pending"),
        ("resolving", "delivery_uncertain"),
        ("delivery_uncertain", "pending"),
        ("delivery_uncertain", "resolved"),
        ("delivery_uncertain", "resolved_external"),
    }
    for current in ApprovalState:
        for target in ApprovalState:
            assert approval_transition_is_valid(current, target) == (
                (current.value, target.value) in allowed
            )


def test_pending_approval_is_immutable_and_normalizes_aware_timestamps():
    timestamp = datetime(2026, 9, 7, 15, tzinfo=timezone(timedelta(hours=3)))
    approval = PendingApproval(
        id="ha_fixture",
        connection_id="local",
        profile="default",
        target_profile="backend-default",
        lineage_key="local:default:lineage-1",
        chat_id="chat-1",
        message_id="message-1",
        stored_session_id="stored-1",
        runtime_session_id="runtime-1",
        request_id="request-1",
        command="git status",
        description="Run a command",
        choices=("once", "session", "always", "deny"),
        state=ApprovalState.PENDING,
        created_at=timestamp,
        updated_at=timestamp,
    )
    assert approval.created_at == datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    assert approval.updated_at.tzinfo is timezone.utc
    assert approval.resolved_choice is None
    with pytest.raises(FrozenInstanceError):
        approval.state = ApprovalState.RESOLVED
    for field in ("created_at", "updated_at"):
        with pytest.raises(ValueError, match="timezone-aware"):
            replace(approval, **{field: datetime(2026, 9, 7)})
    choices = ["once", "deny"]
    copied = replace(approval, choices=choices)
    choices.append("always")
    assert copied.choices == ("once", "deny")


@pytest.mark.parametrize("choices", [(), ("other",), ("once", "once")])
def test_pending_approval_rejects_invalid_choice_sets(choices):
    timestamp = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="choices"):
        PendingApproval(
            "ha_fixture", "local", "default", "backend-default",
            "local:default:lineage-1", "chat-1", "message-1", "stored-1",
            "runtime-1", "request-1", "git status", "Run a command", choices,
            ApprovalState.PENDING, timestamp, timestamp,
        )
