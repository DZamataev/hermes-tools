from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from hermes_bridge.connector.protocol import (
    HermesEventFrame, ProfileRoute, ReplayCompleteFrame, ReplayGapFrame,
)
from hermes_bridge.domain.models import Operation, OperationState, SessionIdentity, SessionMapping, utc_now
from hermes_bridge.hermes.client import HermesMessage, HermesSession
from hermes_bridge.openwebui.mirror import MirrorResult
from hermes_bridge.services.sync import SyncService


def mapping(root: str, *, tip: str | None = None, title: str | None = None) -> SessionMapping:
    now = utc_now()
    return SessionMapping(
        "local",
        "default",
        root,
        tip or f"tip-{root}",
        title or f"Title {root}",
        f"chat-{root}",
        None,
        None,
        None,
        now,
        now,
    )


class FakeMappings:
    def __init__(self, values: list[SessionMapping] | None = None) -> None:
        self.values = {item.lineage_key: item for item in values or []}
        self.failing_upsert_roots: set[str] = set()

    async def upsert_session(self, identity):
        if identity.lineage_root_id in self.failing_upsert_roots:
            raise RuntimeError("broken mapping row")
        current = self.values.get(identity.lineage_key)
        value = (
            replace(current, stored_session_id=identity.stored_session_id, title=identity.title)
            if current
            else mapping(identity.lineage_root_id, tip=identity.stored_session_id, title=identity.title)
        )
        self.values[identity.lineage_key] = value
        return value

    async def by_lineage_key(self, key):
        return self.values.get(key)

    async def by_route_and_stored_id(self, connection_id, profile, stored_session_id):
        return next(
            (
                item
                for item in self.values.values()
                if item.connection_id == connection_id
                and item.profile == profile
                and item.stored_session_id == stored_session_id
            ),
            None,
        )

    async def list_all(self):
        return list(self.values.values())

    async def attach_chat(self, lineage_key, chat_id):
        self.values[lineage_key] = replace(
            self.values[lineage_key], openwebui_chat_id=chat_id
        )
        return self.values[lineage_key]

    async def update_source_revision(self, lineage_key, source_revision):
        self.values[lineage_key] = replace(
            self.values[lineage_key], last_source_revision=source_revision
        )
        return self.values[lineage_key]


class FakeEvents:
    def __init__(self) -> None:
        self.ids: set[str] = set()

    async def record(self, event):
        if event.event_id in self.ids:
            return False
        self.ids.add(event.event_id)
        return True


class FakeHermes:
    def __init__(self, sessions: list[HermesSession], messages: dict[str, list[HermesMessage]]) -> None:
        self.sessions = sessions
        self.messages = messages
        self.snapshot_requests: list[tuple[str, str]] = []
        self.failing_tips: set[str] = set()

    async def iter_sessions(self, profile):
        for session in self.sessions:
            yield session

    async def read_messages(self, session_id, profile):
        self.snapshot_requests.append((session_id, profile))
        if session_id in self.failing_tips:
            raise RuntimeError("prompt text must not leak")
        return self.messages.get(session_id, [])


class FakeMirror:
    def __init__(self, mappings: FakeMappings) -> None:
        self.mappings = mappings
        self.calls: list[tuple[SessionIdentity, list[HermesMessage]]] = []
        self.failing_roots: set[str] = set()
        self.verified = False

    async def verify(self):
        self.verified = True

    async def reconcile(self, identity, messages):
        self.calls.append((identity, list(messages)))
        if identity.lineage_root_id in self.failing_roots:
            raise RuntimeError("secret prompt content")
        await self.mappings.upsert_session(identity)
        return MirrorResult(f"chat-{identity.lineage_root_id}", False, (), True)


class FakeOperations:
    def __init__(self, operations: list[Operation] | None = None) -> None:
        self.values = {item.id: item for item in operations or []}

    async def list_incomplete(self):
        return [
            item
            for item in self.values.values()
            if item.state
            in {
                OperationState.PENDING,
                OperationState.OFFERED,
                OperationState.ACCEPTED,
                OperationState.STREAMING,
                OperationState.DELIVERY_UNCERTAIN,
            }
        ]

    async def transition(self, operation_id, target, **fields):
        current = self.values[operation_id]
        updated = replace(current, state=target, updated_at=utc_now(), **fields)
        self.values[operation_id] = updated
        return updated

    async def get(self, operation_id):
        return self.values.get(operation_id)

    async def count_by_state(self):
        counts = {state.value: 0 for state in OperationState}
        for item in self.values.values():
            counts[item.state.value] += 1
        return counts


class FakeQueue:
    def __init__(self) -> None:
        self.reconciled: list[tuple[str, bool]] = []

    async def reconcile_lineage(self, value, *, blocked):
        self.reconciled.append((value.lineage_key, blocked))


class FakeConnector:
    def __init__(self) -> None:
        self.routes = (ProfileRoute(connection_id="local", profile="default", target_profile="default"),)
        self.connected = True
        self.epoch = "epoch-1"
        self.connector_version = "1.0.0"
        self.replay_result = None
        self.replay_results = None
        self.commands = []
        self.connection_listener = None
        self.event_listener = None

    def set_observer(self, *, on_connect, on_event):
        self.connection_listener = on_connect
        self.event_listener = on_event

    async def dispatch(self, command):
        self.commands.append(command)
        if self.replay_results is not None:
            for result in self.replay_results:
                yield result
            return
        if self.replay_result is not None:
            yield self.replay_result


def operation(
    state: OperationState,
    *,
    runtime_session_id: str | None = "runtime-1",
    last_event_seq: int | None = 511,
) -> Operation:
    now = utc_now()
    return Operation(
        UUID("00000000-0000-0000-0000-000000000001"),
        "chat-root-1",
        "local:default:root-1",
        "user-1",
        "assistant-1",
        "very secret prompt",
        state,
        None,
        None,
        None,
        last_event_seq,
        now,
        now,
        runtime_session_id=runtime_session_id,
    )


def service(
    *,
    sessions: list[HermesSession] | None = None,
    messages: dict[str, list[HermesMessage]] | None = None,
    mappings: FakeMappings | None = None,
    operations: FakeOperations | None = None,
):
    mappings = mappings or FakeMappings()
    hermes = FakeHermes(sessions or [], messages or {})
    mirror = FakeMirror(mappings)
    operations = operations or FakeOperations()
    connector = FakeConnector()
    queue = FakeQueue()
    sync = SyncService(
        hermes,
        mirror,
        mappings,
        operations,
        FakeEvents(),
        connector,
        queue,
        interval_seconds=0.01,
    )
    return sync, hermes, mirror, mappings, operations, connector, queue


async def test_full_scan_isolates_one_failing_chat_and_tracks_safe_failure():
    sessions = [
        HermesSession("tip-root-1", "root-1", "One", message_count=1, last_active=1),
        HermesSession("tip-root-2", "root-2", "Two", message_count=1, last_active=1),
        HermesSession("tip-root-3", "root-3", "Three", message_count=1, last_active=1),
    ]
    sync, hermes, mirror, *_ = service(sessions=sessions)
    mirror.failing_roots.add("root-2")

    report = await sync.full_scan()

    assert report.succeeded == 2 and report.failed == 1
    assert [call[0].lineage_root_id for call in mirror.calls] == ["root-1", "root-2", "root-3"]
    assert report.failures[0].lineage_key == "local:default:root-2"
    assert "secret prompt content" not in str(report.failures)
    assert sync.status_snapshot()["components"]["hermes_read_api"] == "ready"


async def test_full_scan_isolates_a_failing_second_mapping_write():
    sessions = [
        HermesSession(f"tip-root-{index}", f"root-{index}", str(index))
        for index in (1, 2, 3)
    ]
    mappings = FakeMappings()
    mappings.failing_upsert_roots.add("root-2")
    sync, _, mirror, *_ = service(sessions=sessions, mappings=mappings)

    report = await sync.full_scan()

    assert report.succeeded == 2 and report.failed == 1
    assert [call[0].lineage_root_id for call in mirror.calls] == ["root-1", "root-3"]


async def test_openwebui_and_hermes_dependency_checks_start_independently():
    sync, hermes, mirror, *_ = service()
    mirror_started = asyncio.Event()
    hermes_started = asyncio.Event()

    async def verify():
        mirror_started.set()
        await hermes_started.wait()

    async def inventory(_profile):
        hermes_started.set()
        await mirror_started.wait()
        if False:
            yield None

    mirror.verify = verify
    hermes.iter_sessions = inventory
    await asyncio.wait_for(sync.full_scan(), 0.1)
    assert mirror_started.is_set() and hermes_started.is_set()


async def test_readiness_updates_before_the_slowest_probe_finishes():
    sync, hermes, mirror, *_ = service()
    openwebui_done = asyncio.Event()
    release_hermes = asyncio.Event()

    async def verify():
        openwebui_done.set()

    async def inventory(_profile):
        await release_hermes.wait()
        if False:
            yield None

    mirror.verify = verify
    hermes.iter_sessions = inventory
    scan = asyncio.create_task(sync.full_scan())
    await openwebui_done.wait()
    await asyncio.sleep(0)
    assert sync.ready_components["openwebui"] == "ready"
    assert scan.done() is False
    release_hermes.set()
    await scan

    release_openwebui = asyncio.Event()
    hermes_done = asyncio.Event()

    async def slow_verify():
        await release_openwebui.wait()

    async def quick_inventory(_profile):
        hermes_done.set()
        if False:
            yield None

    mirror.verify = slow_verify
    hermes.iter_sessions = quick_inventory
    scan = asyncio.create_task(sync.full_scan())
    await hermes_done.wait()
    await asyncio.sleep(0)
    assert sync.ready_components["hermes_read_api"] == "ready"
    assert scan.done() is False
    release_openwebui.set()
    await scan


async def test_unchanged_inventory_revision_skips_history_but_title_or_tip_drift_reconciles():
    existing = mapping("root-1", title="Old")
    existing = replace(existing, last_source_revision="1:1")
    mappings = FakeMappings([existing])
    sessions = [HermesSession("tip-root-1", "root-1", "Old", message_count=1, last_active=1)]
    sync, hermes, mirror, *_ = service(sessions=sessions, mappings=mappings)

    first = await sync.full_scan()
    assert first.skipped == 1 and hermes.snapshot_requests == []

    hermes.sessions = [HermesSession("tip-root-2", "root-1", "New", message_count=2, last_active=2)]
    await sync.full_scan()
    assert hermes.snapshot_requests == [("tip-root-2", "default")]
    assert mirror.calls[-1][0].title == "New"


async def test_terminal_desktop_event_and_session_info_tip_rotation_reconcile():
    existing = mapping("root-1")
    sync, hermes, mirror, mappings, *_ = service(
        mappings=FakeMappings([existing]),
        messages={"tip-2": [HermesMessage("m1", "assistant", "done", 1)]},
    )
    sync.remember_runtime("local", "default", "runtime-1", existing.lineage_key)
    info = event_frame("session.info", {"stored_session_id": "tip-2"}, seq=10)
    await sync.handle_connector_event(info)
    assert mappings.values[existing.lineage_key].stored_session_id == "tip-2"

    await sync.handle_connector_event(event_frame("message.complete", {"text": "done"}, seq=11))
    assert hermes.snapshot_requests[-1] == ("tip-2", "default")
    assert mirror.calls[-1][0].lineage_root_id == "root-1"


async def test_duplicate_and_out_of_order_events_do_not_reconcile_twice():
    existing = replace(
        mapping("root-1"), last_event_seq=20, last_event_epoch="epoch-1"
    )
    sync, hermes, mirror, *_ = service(mappings=FakeMappings([existing]))
    sync.remember_runtime("local", "default", "runtime-1", existing.lineage_key)
    terminal = event_frame("message.complete", {"text": "done", "event_id": "terminal-1"}, seq=20)

    await sync.handle_connector_event(terminal)
    await sync.handle_connector_event(terminal)

    assert hermes.snapshot_requests == []
    assert mirror.calls == []


async def test_new_connector_epoch_accepts_a_fresh_low_sequence():
    existing = replace(
        mapping("root-1"), last_event_seq=900, last_event_epoch="epoch-old"
    )
    sync, hermes, mirror, *_ = service(mappings=FakeMappings([existing]))
    sync.remember_runtime("local", "default", "runtime-1", existing.lineage_key)

    await sync.handle_connector_event(
        event_frame("message.complete", {"text": "fresh"}, seq=1)
    )

    assert hermes.snapshot_requests == [("tip-root-1", "default")]
    assert len(mirror.calls) == 1


async def test_reconnect_replays_then_falls_back_to_snapshot_without_similarity_matching():
    current = mapping("root-1")
    op = operation(OperationState.DELIVERY_UNCERTAIN)
    operations = FakeOperations([op])
    sync, hermes, _, _, operations, connector, queue = service(
        mappings=FakeMappings([current]),
        operations=operations,
        messages={
            "tip-root-1": [
                HermesMessage("m1", "user", op.text, op.created_at.isoformat()),
                HermesMessage("m2", "assistant", "plausible answer", op.updated_at.isoformat()),
            ]
        },
    )
    connector.replay_result = replay_gap(str(op.id), after_seq=511)

    report = await sync.recover_incomplete_operations()

    assert hermes.snapshot_requests == [("tip-root-1", "default")]
    assert operations.values[op.id].state is OperationState.DELIVERY_UNCERTAIN
    assert report.uncertain == 1
    assert queue.reconciled[-1] == (current.lineage_key, True)


async def test_replay_terminal_proves_completion_and_unblocks_lineage():
    current = mapping("root-1")
    op = operation(OperationState.ACCEPTED, last_event_seq=510)
    operations = FakeOperations([op])
    sync, _, _, _, operations, connector, queue = service(
        mappings=FakeMappings([current]), operations=operations
    )
    replayed = event_frame("message.complete", {"text": "replayed answer"}, seq=511)
    connector.replay_result = replayed.model_copy(
        update={
            "correlation_id": str(op.id),
            "payload": replayed.payload.model_copy(update={"operation_id": str(op.id)}),
        }
    )

    report = await sync.recover_incomplete_operations()

    assert report.completed == 1 and report.uncertain == 0
    assert operations.values[op.id].state is OperationState.COMPLETED
    assert operations.values[op.id].result_text == "replayed answer"
    assert queue.reconciled[-1] == (current.lineage_key, False)


@pytest.mark.parametrize("events", [[], ["message.delta"]])
async def test_replay_complete_terminates_without_proof_and_keeps_uncertain(events):
    current = mapping("root-1")
    op = operation(OperationState.ACCEPTED, last_event_seq=510)
    operations = FakeOperations([op])
    sync, _, _, _, operations, connector, queue = service(
        mappings=FakeMappings([current]), operations=operations
    )
    frames = []
    if events:
        delta = event_frame("message.delta", {"text": "partial"}, seq=511)
        frames.append(delta.model_copy(update={
            "correlation_id": str(op.id),
            "payload": delta.payload.model_copy(update={"operation_id": str(op.id)}),
        }))
    frames.append(replay_complete(str(op.id), after_seq=510))
    connector.replay_results = frames

    report = await asyncio.wait_for(sync.recover_incomplete_operations(), 0.1)

    assert report.uncertain == 1
    assert operations.values[op.id].state is OperationState.DELIVERY_UNCERTAIN
    assert queue.reconciled[-1] == (current.lineage_key, True)


async def test_replayed_error_completion_is_persisted_as_hermes_error():
    current = mapping("root-1")
    op = operation(OperationState.ACCEPTED, last_event_seq=510)
    operations = FakeOperations([op])
    sync, _, _, _, operations, connector, _ = service(
        mappings=FakeMappings([current]), operations=operations
    )
    failed = event_frame(
        "message.complete", {"status": "error", "text": "sensitive provider detail"}, seq=511
    )
    connector.replay_result = failed.model_copy(update={
        "correlation_id": str(op.id),
        "payload": failed.payload.model_copy(update={"operation_id": str(op.id)}),
    })

    await sync.recover_incomplete_operations()

    restored = operations.values[op.id]
    assert restored.state is OperationState.COMPLETED
    assert restored.error_code == "hermes_error"
    assert restored.error_message == "Hermes turn failed"
    assert "sensitive" not in restored.error_message


async def test_replay_gap_completes_only_with_persisted_bridge_operation_identity():
    current = mapping("root-1")
    op = operation(OperationState.DELIVERY_UNCERTAIN)
    tagged = HermesMessage(
        "m2", "assistant", "authoritative", 2, bridge_operation_id=str(op.id)
    )
    operations = FakeOperations([op])
    sync, _, _, _, operations, connector, queue = service(
        mappings=FakeMappings([current]),
        operations=operations,
        messages={"tip-root-1": [tagged]},
    )
    connector.replay_result = replay_gap(str(op.id), after_seq=511)

    report = await sync.recover_incomplete_operations()

    assert operations.values[op.id].state is OperationState.COMPLETED
    assert operations.values[op.id].result_text == "authoritative"
    assert report.completed == 1
    assert queue.reconciled[-1] == (current.lineage_key, False)


async def test_unmapped_active_operation_is_still_marked_uncertain():
    op = operation(OperationState.OFFERED)
    operations = FakeOperations([op])
    sync, _, _, _, operations, _, _ = service(operations=operations)

    report = await sync.recover_incomplete_operations()

    assert operations.values[op.id].state is OperationState.DELIVERY_UNCERTAIN
    assert report.uncertain == 1
    assert report.failures[0].code == "LookupError"


async def test_connector_reconnect_schedules_recovery_and_scan():
    sync, *_rest, connector, _queue = service()
    called = []

    async def recovery():
        called.append("recover")

    async def scan():
        called.append("scan")

    sync.recover_incomplete_operations = recovery
    sync.full_scan = scan
    await sync.start()
    await asyncio.sleep(0)
    called.clear()
    connector.connection_listener(SimpleNamespace())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await sync.close()
    assert called[:2] == ["recover", "scan"]


async def test_periodic_scan_is_cancelled_cleanly_on_shutdown():
    sync, *_ = service()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def scan():
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    sync.full_scan = scan
    await sync.start()
    await started.wait()
    await sync.close()
    assert cancelled.is_set()
    assert sync.background_task_count == 0


async def test_unsolicited_event_buffer_is_bounded_and_reports_drops():
    sync, *_ = service()
    event = event_frame("reasoning.delta", {"text": "internal"}, seq=1)
    for _ in range(300):
        sync._connector_event(event)
    assert sync._event_queue.qsize() == 256
    assert sync.status_snapshot()["dropped_live_events"] == 44


async def test_same_lineage_reconciliations_cannot_create_two_chats():
    current = replace(mapping("root-1"), openwebui_chat_id=None)
    mappings = FakeMappings([current])
    sync, _, _, _, *_ = service(mappings=mappings)
    started = asyncio.Event()
    release = asyncio.Event()

    class CreatingMirror:
        async def verify(self):
            return None

        async def reconcile(self, identity, messages):
            stored = await mappings.by_lineage_key(identity.lineage_key)
            created = stored.openwebui_chat_id is None
            if created:
                started.set()
                await release.wait()
                await mappings.attach_chat(identity.lineage_key, "chat-created")
            return MirrorResult("chat-created", created, (), True)

    sync._mirror = CreatingMirror()
    first = asyncio.create_task(sync.reconcile_lineage(current.lineage_key))
    await started.wait()
    second = asyncio.create_task(sync.reconcile_lineage(current.lineage_key))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert sum(result.created for result in results) == 1


async def test_current_status_refreshes_operation_counts():
    op = operation(OperationState.DELIVERY_UNCERTAIN)
    operations = FakeOperations()
    sync, *_ = service(operations=operations)
    assert sync.status_snapshot()["queue"]["uncertain"] == 0
    operations.values[op.id] = op
    status = await sync.current_status_snapshot()
    assert status["queue"]["uncertain"] == 1


def event_frame(event_type: str, data: dict, *, seq: int) -> HermesEventFrame:
    return HermesEventFrame.model_validate(
        {
            "protocol": 1,
            "kind": "hermes_event",
            "id": f"frame-{seq}",
            "correlation_id": f"event-{seq}",
            "sent_at": datetime.now(timezone.utc),
            "payload": {
                "operation_id": None,
                "connection_id": "local",
                "profile": "default",
                "session_id": "runtime-1",
                "seq": seq,
                "event_type": event_type,
                "data": data,
            },
        }
    )


def replay_gap(operation_id: str, *, after_seq: int) -> ReplayGapFrame:
    return ReplayGapFrame.model_validate(
        {
            "protocol": 1,
            "kind": "replay_gap",
            "id": "gap-1",
            "correlation_id": operation_id,
            "sent_at": datetime.now(timezone.utc),
            "payload": {
                "operation_id": operation_id,
                "after_seq": after_seq,
                "oldest_available": 700,
            },
        }
    )


def replay_complete(operation_id: str, *, after_seq: int) -> ReplayCompleteFrame:
    return ReplayCompleteFrame.model_validate({
        "protocol": 1,
        "kind": "replay_complete",
        "id": "complete-1",
        "correlation_id": operation_id,
        "sent_at": datetime.now(timezone.utc),
        "payload": {"operation_id": operation_id, "after_seq": after_seq},
    })
