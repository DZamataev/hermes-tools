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


def test_projection_groups_tools_and_reasoning_into_native_assistant_output():
    messages = [
        HermesMessage(id="system-1", role="system", content="instructions", created_at=1),
        HermesMessage(id="user-1", role="user", content="check", created_at=2),
        HermesMessage(
            id="assistant-1",
            role="assistant",
            content="Checking.",
            created_at=3,
            reasoning="I should inspect the repository.",
            tool_calls=(
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": "{\"command\":\"git status --short\"}",
                    },
                },
            ),
        ),
        HermesMessage(
            id="tool-1",
            role="tool",
            content="{\"output\":\"\",\"exit_code\":0}",
            created_at=4,
            tool_call_id="call-1",
            tool_name="terminal",
        ),
        HermesMessage(id="assistant-2", role="assistant", content="Done.", created_at=5),
        HermesMessage(id="future-role", role="future", content="unchanged", created_at=6),
    ]

    projection = project_history(SESSION, messages)

    assert projection.title == "Chat"
    assert projection.ordered_message_ids == (
        openwebui_message_id(SESSION, "user-1"),
        openwebui_message_id(SESSION, "assistant-1"),
    )
    assert projection.current_id == openwebui_message_id(SESSION, "assistant-1")
    assert projection.bridge_owned_message_ids == frozenset(projection.ordered_message_ids)
    assert [projection.history[item_id]["role"] for item_id in projection.ordered_message_ids] == [
        "user",
        "assistant",
    ]
    assistant = projection.history[projection.current_id]
    assert assistant["content"] == "Checking.\n\nDone."
    assert assistant["timestamp"] == 3
    assert assistant["done"] is True
    assert assistant["output"] == [
        {
            "type": "reasoning",
            "id": "assistant-1-reasoning",
            "status": "completed",
            "summary": [
                {"type": "summary_text", "text": "I should inspect the repository."}
            ],
        },
        {
            "type": "message",
            "id": "assistant-1",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Checking."}],
        },
        {
            "type": "function_call",
            "id": "call-1",
            "call_id": "call-1",
            "name": "terminal",
            "arguments": "{\"command\":\"git status --short\"}",
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "id": "tool-1",
            "call_id": "call-1",
            "status": "completed",
            "output": [
                {
                    "type": "output_text",
                    "text": "{\"output\":\"\",\"exit_code\":0}",
                }
            ],
        },
        {
            "type": "message",
            "id": "assistant-2",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Done."}],
        },
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
