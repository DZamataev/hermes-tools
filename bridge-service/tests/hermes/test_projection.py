from __future__ import annotations

from hermes_bridge.domain.models import SessionIdentity
from hermes_bridge.hermes.client import HermesMessage
from hermes_bridge.hermes.projection import openwebui_message_id, project_history


SESSION = SessionIdentity(
    connection_id="connection-1",
    profile="default",
    lineage_root_id="root-1",
    stored_session_id="tip-2",
    title="Chat",
)


def test_projection_is_stable_and_linear():
    projection = project_history(
        SESSION,
        [
            HermesMessage(id="11", role="user", content="hello", created_at=100),
            HermesMessage(id="12", role="assistant", content="hi", created_at=101),
        ],
    )

    user_id, assistant_id = projection.ordered_message_ids
    assert projection.history[assistant_id]["parentId"] == user_id
    assert projection.history[user_id]["childrenIds"] == [assistant_id]
    assert project_history(SESSION, projection.source_messages) == projection


def test_projection_records_owned_ids_and_preserves_roles_content_and_timestamps():
    messages = [
        HermesMessage(id="system-1", role="system", content="instructions", created_at=1),
        HermesMessage(id="tool-1", role="tool", content="result", created_at=2),
        HermesMessage(id="future-role", role="future", content="unchanged", created_at=3),
    ]

    projection = project_history(SESSION, messages)

    assert projection.title == "Chat"
    assert projection.current_id == projection.ordered_message_ids[-1]
    assert projection.bridge_owned_message_ids == frozenset(projection.ordered_message_ids)
    assert [projection.history[item_id]["role"] for item_id in projection.ordered_message_ids] == [
        "system",
        "tool",
        "future",
    ]
    assert [projection.history[item_id]["content"] for item_id in projection.ordered_message_ids] == [
        "instructions",
        "result",
        "unchanged",
    ]
    assert [projection.history[item_id]["timestamp"] for item_id in projection.ordered_message_ids] == [
        1,
        2,
        3,
    ]


def test_projection_uses_lineage_root_not_durable_tip_for_stable_ids():
    rotated_tip = SessionIdentity(
        connection_id=SESSION.connection_id,
        profile=SESSION.profile,
        lineage_root_id=SESSION.lineage_root_id,
        stored_session_id="tip-3",
        title=SESSION.title,
    )

    assert openwebui_message_id(SESSION, "message-1") == openwebui_message_id(
        rotated_tip, "message-1"
    )


def test_empty_projection_has_no_current_message():
    projection = project_history(SESSION, [])

    assert projection.history == {}
    assert projection.ordered_message_ids == ()
    assert projection.bridge_owned_message_ids == frozenset()
    assert projection.current_id is None
