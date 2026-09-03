from __future__ import annotations

from dataclasses import dataclass

import pytest

from hermes_bridge.domain.models import (
    InvalidTransition,
    OperationState,
    SessionIdentity,
    TurnEvent,
    TurnRequest,
)
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import (
    EventRepository,
    MappingRepository,
    OperationRepository,
)


@dataclass
class Repositories:
    mappings: MappingRepository
    operations: OperationRepository
    events: EventRepository


@pytest.fixture
async def repositories(tmp_path):
    database = await Database.open(tmp_path / "bridge.db")
    yield Repositories(
        mappings=MappingRepository(database),
        operations=OperationRepository(database),
        events=EventRepository(database),
    )
    await database.close()


async def test_mapping_survives_reopen_and_rotates_tip(tmp_path):
    database = await Database.open(tmp_path / "bridge.db")
    mappings = MappingRepository(database)
    first = await mappings.upsert_session(
        SessionIdentity(
            connection_id="local",
            profile="default",
            lineage_root_id="root-1",
            stored_session_id="tip-1",
            title="Chat",
        )
    )
    await mappings.attach_chat(first.lineage_key, "ow-chat-1")
    await database.close()

    database = await Database.open(tmp_path / "bridge.db")
    mappings = MappingRepository(database)
    await mappings.upsert_session(
        SessionIdentity(
            connection_id="local",
            profile="default",
            lineage_root_id="root-1",
            stored_session_id="tip-2",
            title="Chat",
        )
    )
    restored = await mappings.by_chat_id("ow-chat-1")
    await database.close()

    assert restored is not None
    assert restored.stored_session_id == "tip-2"


async def test_operation_key_is_idempotent_and_transition_is_monotonic(repositories):
    request = TurnRequest(
        chat_id="chat-1",
        user_message_id="user-1",
        assistant_message_id="assistant-1",
        text="hello",
    )
    first, created = await repositories.operations.create_or_get(request)
    duplicate, created_again = await repositories.operations.create_or_get(request)

    assert created is True
    assert created_again is False
    assert duplicate.id == first.id

    await repositories.operations.transition(first.id, OperationState.OFFERED)
    await repositories.operations.transition(first.id, OperationState.ACCEPTED)
    with pytest.raises(InvalidTransition):
        await repositories.operations.transition(first.id, OperationState.PENDING)


async def test_incomplete_operations_and_pending_lineage_queue_are_recoverable(repositories):
    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await repositories.mappings.attach_chat(mapping.lineage_key, "chat-1")

    first_pending, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-1", "assistant-1", "one")
    )
    offered, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-2", "assistant-2", "two")
    )
    accepted, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-3", "assistant-3", "three")
    )
    streaming, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-4", "assistant-4", "four")
    )
    second_pending, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-5", "assistant-5", "five")
    )
    await repositories.operations.transition(offered.id, OperationState.OFFERED)
    await repositories.operations.transition(accepted.id, OperationState.OFFERED)
    await repositories.operations.transition(accepted.id, OperationState.ACCEPTED)
    await repositories.operations.transition(streaming.id, OperationState.OFFERED)
    await repositories.operations.transition(streaming.id, OperationState.ACCEPTED)
    await repositories.operations.transition(streaming.id, OperationState.STREAMING)

    incomplete = await repositories.operations.list_incomplete()
    queued = await repositories.operations.list_pending(mapping.lineage_key)

    assert {operation.id for operation in incomplete} >= {
        offered.id,
        accepted.id,
        streaming.id,
    }
    assert [operation.id for operation in queued] == [first_pending.id, second_pending.id]


async def test_duplicate_event_does_not_advance_mapping_watermark(repositories):
    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    event = TurnEvent(
        event_id="event-1",
        lineage_key=mapping.lineage_key,
        sequence=9,
        kind="message.delta",
        text="hello",
    )

    first_recorded = await repositories.events.record(event)
    duplicate_recorded = await repositories.events.record(
        TurnEvent(
            event_id="event-1",
            lineage_key=mapping.lineage_key,
            sequence=99,
            kind="message.delta",
            text="different",
        )
    )
    restored = await repositories.mappings.by_lineage_key(mapping.lineage_key)

    assert first_recorded is True
    assert duplicate_recorded is False
    assert restored is not None
    assert restored.watermark == 9
