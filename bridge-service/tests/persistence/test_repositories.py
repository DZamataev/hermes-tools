from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import aiosqlite
import pytest

from hermes_bridge.domain.models import (
    ApprovalState,
    InvalidTransition,
    OperationState,
    PendingApproval,
    SessionIdentity,
    TurnEvent,
    TurnRequest,
    stable_approval_id,
)
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import (
    ApprovalRepository,
    EventRepository,
    MappingRepository,
    OperationRepository,
)


@dataclass
class Repositories:
    database: Database
    mappings: MappingRepository
    operations: OperationRepository
    events: EventRepository


@pytest.fixture
async def repositories(tmp_path):
    database = await Database.open(tmp_path / "bridge.db")
    yield Repositories(
        database=database,
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


async def test_reset_openwebui_links_preserves_hermes_identity_and_event_position(
    repositories,
):
    first = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "First")
    )
    second = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-2", "tip-2", "Second")
    )
    await repositories.mappings.attach_chat(first.lineage_key, "chat-1")
    await repositories.mappings.attach_chat(second.lineage_key, "chat-2")
    await repositories.mappings.update_snapshot(
        first.lineage_key,
        last_hermes_message_id="message-1",
        snapshot_hash="snapshot-1",
    )
    await repositories.mappings.update_source_revision(
        first.lineage_key, "projection-v2:1:1"
    )
    async with repositories.database.write_transaction() as connection:
        await connection.execute(
            """
            UPDATE session_mapping
            SET last_event_seq = 42, last_event_epoch = 'epoch-1'
            WHERE connection_id || ':' || profile || ':' || lineage_root_id = ?
            """,
            (first.lineage_key,),
        )

    reset_count = await repositories.mappings.reset_openwebui_links()

    restored = await repositories.mappings.by_lineage_key(first.lineage_key)
    assert reset_count == 2
    assert restored is not None
    assert restored.openwebui_chat_id is None
    assert restored.last_hermes_message_id is None
    assert restored.last_snapshot_hash is None
    assert restored.last_source_revision is None
    assert restored.last_event_seq == 42
    assert restored.last_event_epoch == "epoch-1"
    assert restored.stored_session_id == "tip-1"


async def test_operation_key_is_idempotent_and_transition_is_monotonic(repositories):
    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await repositories.mappings.attach_chat(mapping.lineage_key, "chat-1")
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


async def test_only_the_first_pending_operation_can_be_offered_per_lineage(repositories):
    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await repositories.mappings.attach_chat(mapping.lineage_key, "chat-1")
    first, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-1", "assistant-1", "first")
    )
    second, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-2", "assistant-2", "second")
    )

    with pytest.raises(InvalidTransition):
        await repositories.operations.transition(second.id, OperationState.OFFERED)

    await repositories.operations.transition(first.id, OperationState.OFFERED)
    with pytest.raises(InvalidTransition):
        await repositories.operations.transition(second.id, OperationState.OFFERED)
    await repositories.operations.transition(first.id, OperationState.ACCEPTED)
    await repositories.operations.transition(first.id, OperationState.COMPLETED)

    offered = await repositories.operations.transition(second.id, OperationState.OFFERED)

    assert offered.state is OperationState.OFFERED


async def test_database_rejects_two_active_operations_for_the_same_lineage(repositories):
    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await repositories.mappings.attach_chat(mapping.lineage_key, "chat-1")
    first, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-1", "assistant-1", "first")
    )
    second, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-2", "assistant-2", "second")
    )

    async with repositories.database.write_transaction(immediate=True) as connection:
        await connection.execute(
            "UPDATE operation SET lineage_key = ?, state = ? WHERE operation_id = ?",
            (mapping.lineage_key, OperationState.OFFERED.value, str(first.id)),
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                "UPDATE operation SET lineage_key = ?, state = ? WHERE operation_id = ?",
                (mapping.lineage_key, OperationState.ACCEPTED.value, str(second.id)),
            )


async def test_migration_reconciles_legacy_conflicting_active_operations(tmp_path):
    path = tmp_path / "bridge.db"
    connection = await aiosqlite.connect(path)
    await connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        CREATE TABLE session_mapping (
            connection_id TEXT NOT NULL,
            profile TEXT NOT NULL,
            lineage_root_id TEXT NOT NULL,
            stored_session_id TEXT NOT NULL,
            title TEXT NOT NULL,
            openwebui_chat_id TEXT UNIQUE,
            last_hermes_message_id TEXT,
            last_event_seq INTEGER,
            last_snapshot_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(connection_id, profile, lineage_root_id)
        );
        CREATE TABLE operation (
            operation_order INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id TEXT NOT NULL UNIQUE,
            chat_id TEXT NOT NULL,
            user_message_id TEXT NOT NULL,
            assistant_message_id TEXT NOT NULL,
            text TEXT NOT NULL,
            state TEXT NOT NULL,
            result_text TEXT,
            error_code TEXT,
            error_message TEXT,
            last_event_seq INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(chat_id, user_message_id)
        );
        """
    )
    timestamp = datetime(2026, 9, 3, tzinfo=timezone.utc).isoformat()
    await connection.execute(
        """
        INSERT INTO session_mapping (
            connection_id, profile, lineage_root_id, stored_session_id, title,
            openwebui_chat_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("local", "default", "root-1", "tip-1", "Chat", "chat-1", timestamp, timestamp),
    )
    for operation_id, user_message_id, state in (
        ("00000000-0000-0000-0000-000000000001", "user-1", OperationState.OFFERED),
        ("00000000-0000-0000-0000-000000000002", "user-2", OperationState.ACCEPTED),
        ("00000000-0000-0000-0000-000000000003", "user-3", OperationState.STREAMING),
    ):
        await connection.execute(
            """
            INSERT INTO operation (
                operation_id, chat_id, user_message_id, assistant_message_id, text, state,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                "chat-1",
                user_message_id,
                f"assistant-{user_message_id}",
                user_message_id,
                state.value,
                timestamp,
                timestamp,
            ),
        )
    await connection.commit()
    await connection.close()

    database = await Database.open(path)
    operations_repository = OperationRepository(database)
    operations = await operations_repository.list_incomplete()
    new_operation, _ = await operations_repository.create_or_get(
        TurnRequest("chat-1", "user-4", "assistant-4", "new")
    )
    with pytest.raises(InvalidTransition):
        await operations_repository.transition(new_operation.id, OperationState.OFFERED)
    await database.close()

    assert [operation.state for operation in operations] == [
        OperationState.OFFERED,
        OperationState.DELIVERY_UNCERTAIN,
        OperationState.DELIVERY_UNCERTAIN,
    ]


async def test_incomplete_operations_and_pending_lineage_queue_are_recoverable(repositories):
    queue_mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await repositories.mappings.attach_chat(queue_mapping.lineage_key, "chat-1")
    offered_mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-2", "tip-2", "Chat")
    )
    await repositories.mappings.attach_chat(offered_mapping.lineage_key, "chat-2")
    accepted_mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-3", "tip-3", "Chat")
    )
    await repositories.mappings.attach_chat(accepted_mapping.lineage_key, "chat-3")
    streaming_mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-4", "tip-4", "Chat")
    )
    await repositories.mappings.attach_chat(streaming_mapping.lineage_key, "chat-4")

    first_pending, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-1", "assistant-1", "one")
    )
    offered, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-2", "user-2", "assistant-2", "two")
    )
    accepted, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-3", "user-3", "assistant-3", "three")
    )
    streaming, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-4", "user-4", "assistant-4", "four")
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
    queued = await repositories.operations.list_pending(queue_mapping.lineage_key)

    assert {operation.id for operation in incomplete} >= {
        offered.id,
        accepted.id,
        streaming.id,
    }
    assert [operation.id for operation in queued] == [first_pending.id, second_pending.id]


async def test_event_is_not_recorded_without_an_existing_lineage_mapping(repositories):
    event = TurnEvent("event-1", "local:default:root-1", 9, "message.delta", text="hello")

    with pytest.raises(LookupError):
        await repositories.events.record(event)

    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )

    assert await repositories.events.record(event) is True
    assert (await repositories.mappings.by_lineage_key(mapping.lineage_key)).watermark == 9


async def test_event_cannot_update_an_operation_from_another_lineage(repositories):
    first_mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "First")
    )
    await repositories.mappings.attach_chat(first_mapping.lineage_key, "chat-1")
    second_mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-2", "tip-2", "Second")
    )
    operation, _ = await repositories.operations.create_or_get(
        TurnRequest("chat-1", "user-1", "assistant-1", "hello")
    )
    wrong_lineage_event = TurnEvent(
        "event-1", second_mapping.lineage_key, 9, "message.delta", operation_id=operation.id
    )

    with pytest.raises(ValueError, match="different lineage"):
        await repositories.events.record(wrong_lineage_event)

    correct_event = TurnEvent(
        "event-1", first_mapping.lineage_key, 9, "message.delta", operation_id=operation.id
    )
    assert await repositories.events.record(correct_event) is True


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


async def test_recovery_runtime_and_inventory_revision_survive_reopen(tmp_path):
    path = tmp_path / "bridge.db"
    database = await Database.open(path)
    mappings = MappingRepository(database)
    operations = OperationRepository(database)
    mapping = await mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await mappings.attach_chat(mapping.lineage_key, "chat-1")
    await mappings.update_source_revision(mapping.lineage_key, "2:42")
    operation, _ = await operations.create_or_get(
        TurnRequest("chat-1", "user-1", "assistant-1", "hello")
    )
    await operations.transition(operation.id, OperationState.OFFERED)
    await operations.transition(
        operation.id,
        OperationState.ACCEPTED,
        runtime_session_id="runtime-1",
    )
    await database.close()

    database = await Database.open(path)
    mappings = MappingRepository(database)
    operations = OperationRepository(database)
    restored_mapping = await mappings.by_lineage_key(mapping.lineage_key)
    restored_operation = await operations.get(operation.id)
    await database.close()

    assert restored_mapping is not None
    assert restored_mapping.last_source_revision == "2:42"
    assert restored_operation is not None
    assert restored_operation.runtime_session_id == "runtime-1"


async def test_new_connector_epoch_can_restart_mapping_sequence(repositories):
    mapping = await repositories.mappings.upsert_session(
        SessionIdentity("local", "default", "root-1", "tip-1", "Chat")
    )
    await repositories.events.record(
        TurnEvent(
            "old-900",
            mapping.lineage_key,
            900,
            "message.delta",
            connector_epoch="epoch-old",
        )
    )
    await repositories.events.record(
        TurnEvent(
            "new-1",
            mapping.lineage_key,
            1,
            "message.delta",
            connector_epoch="epoch-new",
        )
    )

    restored = await repositories.mappings.by_lineage_key(mapping.lineage_key)
    assert restored is not None
    assert restored.last_event_epoch == "epoch-new"
    assert restored.last_event_seq == 1


def approval_candidate(**changes):
    fields = dict(
        connection_id="local", profile="default", target_profile="backend-default",
        lineage_key="local:default:root-1", chat_id="chat-1", message_id="message-1",
        stored_session_id="stored-1", runtime_session_id="runtime-1",
        request_id="request-1", command="git status", description="Run a command",
        choices=("once", "session", "always", "deny"), state=ApprovalState.PENDING,
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    fields.update(changes)
    fields.setdefault("id", stable_approval_id(
        fields["connection_id"], fields["profile"], fields["target_profile"],
        fields["lineage_key"], fields["request_id"],
    ))
    return PendingApproval(**fields)


@pytest.mark.parametrize("legacy", [False, True])
async def test_approval_schema_initializes_new_and_v1_databases(tmp_path, legacy):
    path = tmp_path / "approvals.db"
    if legacy:
        async with aiosqlite.connect(path) as connection:
            await connection.executescript("""
                CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                INSERT INTO schema_version VALUES (1);
                CREATE TABLE session_mapping (
                    connection_id TEXT NOT NULL, profile TEXT NOT NULL,
                    lineage_root_id TEXT NOT NULL, stored_session_id TEXT NOT NULL,
                    title TEXT NOT NULL, openwebui_chat_id TEXT UNIQUE,
                    last_hermes_message_id TEXT, last_event_seq INTEGER,
                    last_snapshot_hash TEXT, last_source_revision TEXT,
                    last_event_epoch TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(connection_id, profile, lineage_root_id)
                );
                CREATE TABLE operation (
                    operation_order INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL UNIQUE, chat_id TEXT NOT NULL,
                    lineage_key TEXT, user_message_id TEXT NOT NULL,
                    assistant_message_id TEXT NOT NULL, text TEXT NOT NULL,
                    state TEXT NOT NULL, result_text TEXT, error_code TEXT,
                    error_message TEXT, last_event_seq INTEGER,
                    runtime_session_id TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, UNIQUE(chat_id, user_message_id)
                );
                CREATE TABLE turn_event (
                    event_id TEXT PRIMARY KEY, lineage_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT,
                    operation_id TEXT, stored_session_id TEXT, connector_epoch TEXT,
                    occurred_at TEXT NOT NULL
                );
                INSERT INTO session_mapping (
                    connection_id, profile, lineage_root_id, stored_session_id,
                    title, created_at, updated_at
                ) VALUES ('local', 'default', 'legacy-root', 'legacy-stored',
                          'Legacy chat', '2026-09-07T00:00:00+00:00',
                          '2026-09-07T00:00:00+00:00');
            """)
    database = await Database.open(path)
    try:
        approvals = ApprovalRepository(database)
        candidate = approval_candidate()
        assert await approvals.upsert_pending(candidate) == candidate
        cursor = await database.connection.execute("PRAGMA index_list(pending_approval)")
        indexes = await cursor.fetchall()
        columns = []
        for index in indexes:
            cursor = await database.connection.execute(
                'PRAGMA index_info("' + index["name"] + '")'
            )
            columns.append(tuple(row["name"] for row in await cursor.fetchall()))
        assert ("connection_id", "profile", "target_profile", "state") in columns
        assert ("lineage_key", "state") in columns
        if legacy:
            mapping = await MappingRepository(database).by_lineage_key(
                "local:default:legacy-root"
            )
            assert mapping.title == "Legacy chat"
    finally:
        await database.close()
    database = await Database.open(path)
    try:
        assert await ApprovalRepository(database).get(candidate.id) == candidate
    finally:
        await database.close()


async def test_approval_upsert_preserves_choices_and_refreshes_pending_metadata(repositories):
    approvals = ApprovalRepository(repositories.database)
    candidate = approval_candidate()
    assert await approvals.upsert_pending(candidate) == candidate
    assert await approvals.upsert_pending(candidate) == candidate
    cursor = await repositories.database.connection.execute(
        "SELECT choices FROM pending_approval WHERE approval_id = ?", (candidate.id,)
    )
    assert json.loads((await cursor.fetchone())["choices"]) == [
        "once", "session", "always", "deny",
    ]
    updated = await approvals.upsert_pending(replace(
        candidate, runtime_session_id="runtime-2", choices=("once", "deny"),
        command="redacted update", description="Updated description",
    ))
    assert updated.id == candidate.id
    assert updated.created_at == candidate.created_at
    assert updated.updated_at > candidate.updated_at
    assert updated.runtime_session_id == "runtime-2"
    assert updated.choices == ("once", "deny")
    assert updated.command == "redacted update"
    assert updated.description == "Updated description"
    assert await approvals.by_route_request(
        "local", "default", "backend-default", "stored-1", "request-1"
    ) == updated
    assert await approvals.get("missing") is None
    assert await approvals.by_route_request(
        "local", "default", "backend-other", "stored-1", "request-1"
    ) is None


@pytest.mark.parametrize("field", [
    "connection_id", "profile", "target_profile", "stored_session_id", "request_id",
])
async def test_approval_route_stored_request_identity_is_unique(repositories, field):
    approvals = ApprovalRepository(repositories.database)
    original = approval_candidate()
    await approvals.upsert_pending(original)
    other = approval_candidate(**{field: "other", "id": "ha_other"})
    await approvals.upsert_pending(other)
    assert await approvals.by_route_request(
        other.connection_id, other.profile, other.target_profile,
        other.stored_session_id, other.request_id,
    ) == other
    with pytest.raises(ValueError, match="identity"):
        await approvals.upsert_pending(replace(original, id="ha_duplicate"))
    with pytest.raises(ValueError, match="identity"):
        await approvals.upsert_pending(replace(other, id=original.id))
    async with repositories.database.write_transaction() as connection:
        with pytest.raises(aiosqlite.IntegrityError):
            await connection.execute(
                "UPDATE pending_approval SET " + field + " = ? WHERE approval_id = ?",
                (getattr(original, field), other.id),
            )
    assert await approvals.get(original.id) == original


async def test_approval_transitions_reject_stale_state_and_invalid_edges(repositories):
    approvals = ApprovalRepository(repositories.database)
    candidate = await approvals.upsert_pending(approval_candidate())
    with pytest.raises(InvalidTransition):
        await approvals.transition(
            candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVED,
            resolved_choice="once",
        )
    with pytest.raises(InvalidTransition):
        await approvals.transition(candidate.id, frozenset(), ApprovalState.RESOLVING)
    with pytest.raises(LookupError):
        await approvals.transition("missing", frozenset(), ApprovalState.RESOLVING)
    resolving = await approvals.transition(
        candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING,
        resolved_choice="once",
    )
    assert resolving.state is ApprovalState.RESOLVING
    assert resolving.resolved_choice == "once"
    with pytest.raises(InvalidTransition):
        await approvals.transition(
            candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING,
        )
    resolved = await approvals.transition(
        candidate.id, frozenset({ApprovalState.RESOLVING}), ApprovalState.RESOLVED,
    )
    assert resolved.resolved_choice == "once"
    assert resolved.updated_at > candidate.updated_at
    assert await approvals.upsert_pending(candidate) == resolved
    assert (await approvals.get(candidate.id)).state is ApprovalState.RESOLVED


@pytest.mark.parametrize("changes", [
    {"state": ApprovalState.RESOLVED, "resolved_choice": "once"},
    {"resolved_choice": "once"},
])
async def test_approval_upsert_cannot_bypass_resolution_transitions(repositories, changes):
    approvals = ApprovalRepository(repositories.database)
    candidate = approval_candidate(**changes)
    with pytest.raises(ValueError, match="pending"):
        await approvals.upsert_pending(candidate)
    assert await approvals.get(candidate.id) is None


async def test_approval_transition_requires_advertised_resolution_choice(repositories):
    approvals = ApprovalRepository(repositories.database)
    candidate = await approvals.upsert_pending(approval_candidate(choices=("once", "deny")))
    with pytest.raises(ValueError, match="choice"):
        await approvals.transition(
            candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING,
            resolved_choice="always",
        )
    await approvals.transition(
        candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING,
    )
    with pytest.raises(ValueError, match="choice"):
        await approvals.transition(
            candidate.id, frozenset({ApprovalState.RESOLVING}), ApprovalState.RESOLVED,
        )
    pending = await approvals.transition(
        candidate.id, frozenset({ApprovalState.RESOLVING}), ApprovalState.PENDING,
    )
    assert pending.resolved_choice is None
    await approvals.transition(
        candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING,
        resolved_choice="deny",
    )
    await approvals.transition(
        candidate.id, frozenset({ApprovalState.RESOLVING}), ApprovalState.DELIVERY_UNCERTAIN,
    )
    external = await approvals.transition(
        candidate.id, frozenset({ApprovalState.DELIVERY_UNCERTAIN}),
        ApprovalState.RESOLVED_EXTERNAL,
    )
    assert external.resolved_choice is None


async def test_approval_transition_has_one_winner_across_database_connections(tmp_path):
    path = tmp_path / "approvals.db"
    first = await Database.open(path)
    second = await Database.open(path)
    try:
        approvals = ApprovalRepository(first)
        candidate = await approvals.upsert_pending(approval_candidate())
        results = await asyncio.gather(*(
            repository.transition(
                candidate.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING,
                resolved_choice=choice,
            )
            for repository, choice in (
                (approvals, "once"), (ApprovalRepository(second), "deny"),
            )
        ), return_exceptions=True)
        winners = [result for result in results if isinstance(result, PendingApproval)]
        assert len(winners) == 1
        assert sum(isinstance(result, InvalidTransition) for result in results) == 1
        assert await approvals.get(candidate.id) == winners[0]
    finally:
        await first.close()
        await second.close()


async def test_approval_recovery_and_queries_preserve_unsettled_state_on_reopen(tmp_path):
    path = tmp_path / "approvals.db"
    database = await Database.open(path)
    approvals = ApprovalRepository(database)
    ids = {}
    try:
        for state in ApprovalState:
            candidate = approval_candidate(request_id=state.value)
            await approvals.upsert_pending(candidate)
            ids[state] = candidate.id
            if state in {
                ApprovalState.RESOLVING, ApprovalState.RESOLVED,
                ApprovalState.DELIVERY_UNCERTAIN,
            }:
                await approvals.transition(
                    candidate.id, frozenset({ApprovalState.PENDING}),
                    ApprovalState.RESOLVING, resolved_choice="once",
                )
                if state is not ApprovalState.RESOLVING:
                    await approvals.transition(
                        candidate.id, frozenset({ApprovalState.RESOLVING}), state,
                    )
            elif state is not ApprovalState.PENDING:
                await approvals.transition(
                    candidate.id, frozenset({ApprovalState.PENDING}), state,
                )
        other = approval_candidate(target_profile="backend-other")
        await approvals.upsert_pending(other)
        assert len(await approvals.list_for_route("local", "default", "backend-default")) == 6
        assert await approvals.list_for_route("local", "default", "missing") == []
        assert {row.id for row in await approvals.list_reconcilable()} == {
            ids[ApprovalState.PENDING], ids[ApprovalState.RESOLVING],
            ids[ApprovalState.DELIVERY_UNCERTAIN], other.id,
        }
    finally:
        await database.close()
    database = await Database.open(path)
    try:
        approvals = ApprovalRepository(database)
        assert await approvals.recover_in_flight() == 1
        assert await approvals.recover_in_flight() == 0
        recovered = await approvals.get(ids[ApprovalState.RESOLVING])
        assert recovered.state is ApprovalState.DELIVERY_UNCERTAIN
        assert recovered.resolved_choice == "once"
        assert await approvals.upsert_pending(approval_candidate(request_id="resolving")) == recovered
        assert await approvals.count_by_state() == {
            "pending": 2, "resolving": 0, "delivery_uncertain": 2,
            "resolved": 1, "resolved_external": 1, "expired": 1,
        }
    finally:
        await database.close()
