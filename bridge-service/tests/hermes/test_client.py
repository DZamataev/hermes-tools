from __future__ import annotations

import httpx
import pytest

from hermes_bridge.hermes.client import HermesReadClient, HermesReadError


def hermes_messages_handler(
    total: int, requests: list[httpx.Request]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        messages = [
            {
                "id": str(index),
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message {index}",
                "created_at": index,
            }
            for index in range(offset, min(offset + 500, total))
        ]
        return httpx.Response(200, json={"messages": messages})

    return httpx.MockTransport(handler)


async def test_read_messages_pages_oldest_first_without_loss():
    requests: list[httpx.Request] = []
    transport = hermes_messages_handler(total=501, requests=requests)
    client = HermesReadClient("http://hermes", "secret", transport=transport)

    messages = await client.read_messages("session-1", "default")

    assert len(messages) == 501
    assert [message.content for message in messages[:2]] == ["message 0", "message 1"]
    assert [int(request.url.params["offset"]) for request in requests] == [0, 500]
    assert all(request.headers["authorization"] == "Bearer secret" for request in requests)
    assert all(request.url.params["order"] == "oldest" for request in requests)


async def test_iter_sessions_pages_and_preserves_durable_tip_and_lineage_root():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        sessions = [
            {
                "id": f"tip-{index}",
                "lineage_root_id": "root-1" if index == 0 else f"root-{index}",
                "title": f"Chat {index}",
            }
            for index in range(offset, min(offset + 100, 101))
        ]
        return httpx.Response(200, json={"sessions": sessions})

    client = HermesReadClient("http://hermes", "secret", transport=httpx.MockTransport(handler))

    sessions = [session async for session in client.iter_sessions("default")]

    assert len(sessions) == 101
    assert sessions[0].id == "tip-0"
    assert sessions[0].lineage_root_id == "root-1"
    assert [int(request.url.params["offset"]) for request in requests] == [0, 100]
    assert all(request.url.params["archived"] == "exclude" for request in requests)
    assert all(request.url.params["order"] == "recent" for request in requests)


async def test_iter_sessions_accepts_empty_inventory():
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"sessions": []})),
    )

    assert [session async for session in client.iter_sessions("default")] == []


async def test_reads_installed_hermes_data_envelope_and_field_names():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sessions":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "tip-2",
                            "_lineage_root_id": "root-1",
                            "title": "Real shape",
                            "message_count": 2,
                            "last_active": 42.5,
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "1", "role": "user", "content": "hello", "timestamp": 41},
                    {"id": "2", "role": "assistant", "content": "hi", "timestamp": 42},
                ]
            },
        )

    client = HermesReadClient(
        "http://hermes", "secret", transport=httpx.MockTransport(handler)
    )
    sessions = [session async for session in client.iter_sessions("default")]
    messages = await client.read_messages("tip-2", "default")

    assert sessions[0].lineage_root_id == "root-1"
    assert sessions[0].revision == "2:42.5"
    assert [message.created_at for message in messages] == [41, 42]


async def test_read_messages_preserves_structured_tool_and_reasoning_metadata():
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "assistant-1",
                            "role": "assistant",
                            "content": "Checking the repository.",
                            "reasoning_content": "I need to inspect the current state.",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "terminal",
                                        "arguments": "{\"command\":\"git status --short\"}",
                                    },
                                }
                            ],
                            "timestamp": 10,
                        },
                        {
                            "id": "tool-1",
                            "role": "tool",
                            "content": "{\"output\":\"\",\"exit_code\":0}",
                            "tool_call_id": "call-1",
                            "tool_name": "terminal",
                            "timestamp": 11,
                        },
                    ]
                },
            )
        ),
    )

    messages = await client.read_messages("session-1", "default")

    assert messages[0].reasoning == "I need to inspect the current state."
    assert messages[0].tool_calls == (
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "terminal",
                "arguments": "{\"command\":\"git status --short\"}",
            },
        },
    )
    assert messages[1].tool_call_id == "call-1"
    assert messages[1].tool_name == "terminal"


async def test_read_messages_normalizes_installed_numeric_ids_in_order():
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 41, "role": "user", "content": "hello", "timestamp": 41},
                        {"id": 42, "role": "assistant", "content": "hi", "timestamp": 42},
                    ]
                },
            )
        ),
    )

    messages = await client.read_messages("tip-2", "default")

    assert [message.id for message in messages] == ["41", "42"]


async def test_null_installed_lineage_root_falls_back_to_tip():
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"data": [{"id": "tip-1", "_lineage_root_id": None, "title": "Chat"}]},
            )
        ),
    )
    sessions = [session async for session in client.iter_sessions("default")]
    assert sessions[0].lineage_root_id == "tip-1"


@pytest.mark.parametrize(
    ("status_code", "endpoint"),
    [(401, "/api/sessions"), (404, "/api/sessions/session-1/messages")],
)
async def test_http_errors_include_status_and_endpoint_without_bearer(
    status_code: int, endpoint: str
):
    client = HermesReadClient(
        "http://hermes",
        "secret-that-must-not-leak",
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code)),
    )

    with pytest.raises(HermesReadError) as error:
        if status_code == 401:
            _ = [session async for session in client.iter_sessions("default")]
        else:
            await client.read_messages("session-1", "default")

    assert str(status_code) in str(error.value)
    assert endpoint in str(error.value)
    assert "secret-that-must-not-leak" not in str(error.value)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/sessions", {"sessions": {}}),
        ("/api/sessions/session-1/messages", {"messages": {}}),
    ],
)
async def test_invalid_json_shape_is_rejected(path: str, payload: object):
    client = HermesReadClient(
        "http://hermes", "secret", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )

    with pytest.raises(HermesReadError, match="invalid response shape"):
        if path.endswith("messages"):
            await client.read_messages("session-1", "default")
        else:
            _ = [session async for session in client.iter_sessions("default")]


async def test_read_messages_skips_hidden_compaction_carrier_and_uses_display_content():
    payload = {
        "messages": [
            {
                "id": "compaction-carrier",
                "role": "system",
                "content": "internal summary",
                "display_kind": "hidden",
                "created_at": 1,
            },
            {
                "id": "visible-1",
                "role": "assistant",
                "content": "internal answer",
                "display_content": "visible answer",
                "created_at": 2,
            },
            {"id": "visible-2", "role": "tool", "content": "tool output", "created_at": 3},
        ]
    }
    client = HermesReadClient(
        "http://hermes", "secret", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )

    messages = await client.read_messages("session-1", "default")

    assert [(message.id, message.role, message.content) for message in messages] == [
        ("visible-1", "assistant", "visible answer"),
        ("visible-2", "tool", "tool output"),
    ]


async def test_read_messages_omits_internal_role_before_validating_null_content():
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "messages": [
                        {"id": "1", "role": "user", "content": "hello", "created_at": 1},
                        {"id": "2", "role": "session_meta", "content": None, "created_at": 2},
                        {"id": "3", "role": "assistant", "content": "hi", "created_at": 3},
                    ]
                },
            )
        ),
    )

    messages = await client.read_messages("session-1", "default")

    assert [(message.id, message.role, message.content) for message in messages] == [
        ("1", "user", "hello"),
        ("3", "assistant", "hi"),
    ]


async def test_read_messages_normalizes_text_parts_in_source_order():
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "id": "1",
                            "role": "tool",
                            "content": ["first", {"text": " second"}, " third"],
                            "created_at": 1,
                        },
                        {
                            "id": "2",
                            "role": "assistant",
                            "content": "fallback",
                            "display_content": [{"text": "shown"}, " answer"],
                            "created_at": 2,
                        },
                    ]
                },
            )
        ),
    )

    messages = await client.read_messages("session-1", "default")

    assert [message.content for message in messages] == ["first second third", "shown answer"]


async def test_read_messages_redacts_image_url_parts_after_preceding_text():
    source_reference = "opaque-image-reference"
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "id": "1",
                            "role": "tool",
                            "content": [
                                "tool result",
                                {
                                    "type": "image_url",
                                    "image_url": {"url": source_reference},
                                },
                            ],
                            "created_at": 1,
                        }
                    ]
                },
            )
        ),
    )

    messages = await client.read_messages("session-1", "default")

    assert messages[0].content == "tool result\n[image]"
    assert source_reference not in messages[0].content


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "image"}],
        [{"type": "image_url", "image_url": ""}],
        [{"type": "image_url", "image_url": {"url": ""}}],
        [{"type": "image_url", "image_url": {"url": 1}}],
        [None],
        [1],
        [False],
    ],
)
async def test_read_messages_rejects_unsupported_content_parts(content: object):
    client = HermesReadClient(
        "http://hermes",
        "secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "messages": [
                        {"id": "1", "role": "tool", "content": content, "created_at": 1}
                    ]
                },
            )
        ),
    )

    with pytest.raises(HermesReadError, match="content"):
        await client.read_messages("session-1", "default")
