from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

import pytest

from hermes_bridge.connector.hub import ConnectorDisconnected
from hermes_bridge.connector.protocol import AcceptedFrame, HermesEventFrame, ProfileRoute
from hermes_bridge.domain.models import OperationState, SessionIdentity, TurnRequest
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import MappingRepository, OperationRepository
from hermes_bridge.services.queue import (
    DeliveryUncertain, DesktopOffline, HermesTurnFailed, LineageQueue, OperationRejected,
)


def _frame(kind, correlation, operation_id, **payload):
    common = {"protocol": 1, "kind": kind, "id": f"{kind}-{operation_id}", "correlation_id": correlation,
              "sent_at": datetime.now(timezone.utc)}
    if kind == "accepted":
        return AcceptedFrame.model_validate({**common, "payload": {"operation_id": str(operation_id), "runtime_session_id": f"runtime-{operation_id}"}})
    return HermesEventFrame.model_validate({**common, "payload": {
        "operation_id": str(operation_id), "connection_id": payload.pop("connection_id", "local"),
        "profile": payload.pop("profile", "default"), "session_id": f"runtime-{operation_id}",
        "seq": payload.pop("seq", 1), "event_type": payload.pop("event_type"), "data": payload}})


class ControlledConnector:
    def __init__(self):
        self.connected = True
        self.routes = (ProfileRoute(connection_id="local", profile="default", target_profile="default"),)
        self.started = defaultdict(asyncio.Event)
        self.finishes = {}
        self.submit_count = 0
        self.disconnect_after_accept = set()

    async def dispatch(self, command):
        key = command.payload.text
        self.submit_count += 1
        self.started[key].set()
        yield _frame("accepted", command.correlation_id, command.payload.operation_id)
        if key in self.disconnect_after_accept:
            raise ConnectorDisconnected("lost")
        result = await self.finishes.setdefault(key, asyncio.get_running_loop().create_future())
        if isinstance(result, BaseException):
            yield _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                         connection_id=command.payload.route.connection_id, profile=command.payload.route.profile,
                         event_type="error", message=str(result))
        else:
            yield _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                         connection_id=command.payload.route.connection_id, profile=command.payload.route.profile,
                         event_type="message.delta", text=result)
            yield _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                         connection_id=command.payload.route.connection_id, profile=command.payload.route.profile,
                         event_type="message.complete", seq=2, text=result)

    def complete(self, key, value=None):
        self.finishes.setdefault(key, asyncio.get_running_loop().create_future()).set_result(key if value is None else value)


@pytest.fixture
async def stores(tmp_path):
    database = await Database.open(tmp_path / "queue.sqlite3")
    yield MappingRepository(database), OperationRepository(database)
    await database.close()


async def _mapping(mappings, suffix, *, profile="default"):
    value = await mappings.upsert_session(SessionIdentity("local", profile, f"root-{suffix}", f"tip-{suffix}", suffix))
    return await mappings.attach_chat(value.lineage_key, f"chat-{suffix}")


async def _operation(operations, mapping, key):
    return (await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, f"user-{key}", f"assistant-{key}", key)))[0]


async def _collect(iterator):
    return [event async for event in iterator]


async def test_same_lineage_is_fifo_but_different_lineages_overlap(stores):
    mappings, operations = stores
    a, b = await _mapping(mappings, "a"), await _mapping(mappings, "b", profile="other")
    connector = ControlledConnector()
    connector.routes += (ProfileRoute(connection_id="local", profile="other", target_profile="other"),)
    queue = LineageQueue(operations, connector, idle_timeout_seconds=0.05)
    a1, a2, b1 = await _operation(operations, a, "a1"), await _operation(operations, a, "a2"), await _operation(operations, b, "b1")
    tasks = [asyncio.create_task(_collect(queue.submit(a, item))) for item in (a1, a2)]
    tasks.append(asyncio.create_task(_collect(queue.submit(b, b1))))
    await asyncio.wait_for(connector.started["a1"].wait(), 1)
    await asyncio.wait_for(connector.started["b1"].wait(), 1)
    assert not connector.started["a2"].is_set()
    connector.complete("a1")
    await asyncio.wait_for(connector.started["a2"].wait(), 1)
    connector.complete("a2"); connector.complete("b1")
    results = await asyncio.gather(*tasks)
    assert [[event.kind for event in events] for events in results] == [["message.delta", "message.complete"]] * 3
    await queue.close()


async def test_duplicate_followers_dispatch_once_and_completed_duplicate_replays(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "dup")
    operation = await _operation(operations, mapping, "hello")
    connector, queue = ControlledConnector(), None
    queue = LineageQueue(operations, connector)
    first = asyncio.create_task(_collect(queue.submit(mapping, operation)))
    second = asyncio.create_task(_collect(queue.submit(mapping, operation)))
    await connector.started["hello"].wait(); connector.complete("hello", "answer")
    assert [event.text for event in await first] == ["answer", "answer"]
    assert [event.text for event in await second] == ["answer", "answer"]
    stored, created = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-hello", "ignored", "ignored"))
    assert not created and stored.state is OperationState.COMPLETED
    assert [(event.kind, event.text) for event in await _collect(queue.submit(mapping, stored))] == [("message.complete", "answer")]
    assert connector.submit_count == 1
    await queue.close()


async def test_duplicate_after_acceptance_follows_live_job_without_marking_uncertain(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "live-duplicate")
    operation = await _operation(operations, mapping, "live")
    connector = ControlledConnector(); queue = LineageQueue(operations, connector)
    first = asyncio.create_task(_collect(queue.submit(mapping, operation)))
    await connector.started["live"].wait()
    for _ in range(20):
        current, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-live", "x", "x"))
        if current.state is OperationState.ACCEPTED: break
        await asyncio.sleep(0)
    second = asyncio.create_task(_collect(queue.submit(mapping, current)))
    connector.complete("live", "answer")
    assert [event.text for event in await first] == ["answer", "answer"]
    assert [event.text for event in await second] == ["answer", "answer"]
    stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-live", "x", "x"))
    assert stored.state is OperationState.COMPLETED and connector.submit_count == 1
    await queue.close()


async def test_offline_before_offer_is_definitively_rejected(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "offline"); operation = await _operation(operations, mapping, "offline")
    connector = ControlledConnector(); connector.connected = False
    queue = LineageQueue(operations, connector)
    with pytest.raises(DesktopOffline):
        await _collect(queue.submit(mapping, operation))
    stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-offline", "x", "x"))
    assert stored.state is OperationState.REJECTED and stored.error_code == "hermes_desktop_offline"
    await queue.close()


async def test_disconnect_after_offer_marks_uncertain_and_blocks_lineage(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "uncertain")
    first, second = await _operation(operations, mapping, "first"), await _operation(operations, mapping, "second")
    connector = ControlledConnector(); connector.disconnect_after_accept.add("first")
    queue = LineageQueue(operations, connector)
    with pytest.raises(DeliveryUncertain):
        await _collect(queue.submit(mapping, first))
    with pytest.raises(DeliveryUncertain):
        await asyncio.wait_for(_collect(queue.submit(mapping, second)), 0.1)
    assert not connector.started["second"].is_set()
    await queue.close()


async def test_browser_cancellation_does_not_cancel_accepted_hermes_work(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "cancel"); operation = await _operation(operations, mapping, "cancel")
    connector = ControlledConnector(); queue = LineageQueue(operations, connector)
    browser = asyncio.create_task(_collect(queue.submit(mapping, operation)))
    await connector.started["cancel"].wait(); browser.cancel(); await asyncio.gather(browser, return_exceptions=True)
    connector.complete("cancel", "recovered")
    for _ in range(20):
        stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-cancel", "x", "x"))
        if stored.state is OperationState.COMPLETED: break
        await asyncio.sleep(0.01)
    assert stored.state is OperationState.COMPLETED and stored.result_text == "recovered"
    await queue.close()


async def test_hermes_error_is_terminal_and_replayed_without_redispatch(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "error"); operation = await _operation(operations, mapping, "error")
    connector = ControlledConnector(); queue = LineageQueue(operations, connector)
    task = asyncio.create_task(_collect(queue.submit(mapping, operation)))
    await connector.started["error"].wait(); connector.complete("error", RuntimeError("model failed"))
    with pytest.raises(HermesTurnFailed, match="model failed"): await task
    stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-error", "x", "x"))
    with pytest.raises(HermesTurnFailed): await _collect(queue.submit(mapping, stored))
    assert connector.submit_count == 1
    await queue.close()


async def test_slow_follower_overflow_does_not_change_completed_delivery(stores):
    class BurstConnector(ControlledConnector):
        async def dispatch(self, command):
            self.submit_count += 1; self.started[command.payload.text].set()
            yield _frame("accepted", command.correlation_id, command.payload.operation_id)
            for seq in range(1, 9):
                yield _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                             event_type="message.delta", seq=seq, text=str(seq))
            yield _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                         event_type="message.complete", seq=9, text="12345678")

    mappings, operations = stores
    mapping = await _mapping(mappings, "slow"); operation = await _operation(operations, mapping, "slow")
    connector = BurstConnector(); queue = LineageQueue(operations, connector, queue_size=2)
    browser = queue.submit(mapping, operation)
    with pytest.raises(OperationRejected, match="too slow"):
        await anext(browser)
    await asyncio.sleep(0.02)
    stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-slow", "x", "x"))
    assert stored.state is OperationState.COMPLETED and stored.result_text == "12345678"
    replay = await _collect(queue.submit(mapping, stored))
    assert replay[0].text == "12345678" and connector.submit_count == 1
    await browser.aclose(); await queue.close()


async def test_error_status_on_message_complete_is_terminal_failure(stores):
    class FailedCompletionConnector(ControlledConnector):
        async def dispatch(self, command):
            self.submit_count += 1
            yield _frame("accepted", command.correlation_id, command.payload.operation_id)
            yield _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                         event_type="message.complete", status="error", error="provider failed")

    mappings, operations = stores
    mapping = await _mapping(mappings, "complete-error"); operation = await _operation(operations, mapping, "failed")
    connector = FailedCompletionConnector(); queue = LineageQueue(operations, connector)
    with pytest.raises(HermesTurnFailed, match="provider failed"):
        await _collect(queue.submit(mapping, operation))
    stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-failed", "x", "x"))
    assert stored.state is OperationState.COMPLETED and stored.error_code == "hermes_error"
    await queue.close()


@pytest.mark.parametrize("predecessor_state", [
    OperationState.OFFERED, OperationState.ACCEPTED,
    OperationState.STREAMING, OperationState.DELIVERY_UNCERTAIN,
])
async def test_persisted_active_predecessor_fails_later_pending_without_hanging(stores, predecessor_state):
    mappings, operations = stores
    mapping = await _mapping(mappings, f"restart-{predecessor_state}")
    predecessor = await _operation(operations, mapping, "predecessor")
    predecessor = await operations.transition(predecessor.id, OperationState.OFFERED)
    if predecessor_state in {OperationState.ACCEPTED, OperationState.STREAMING}:
        predecessor = await operations.transition(predecessor.id, OperationState.ACCEPTED)
    if predecessor_state is OperationState.STREAMING:
        predecessor = await operations.transition(predecessor.id, OperationState.STREAMING)
    if predecessor_state is OperationState.DELIVERY_UNCERTAIN:
        predecessor = await operations.transition(predecessor.id, OperationState.DELIVERY_UNCERTAIN)
    pending = await _operation(operations, mapping, "later")
    another = await _operation(operations, mapping, "another-later")
    connector = ControlledConnector(); queue = LineageQueue(operations, connector)
    followers = [asyncio.create_task(_collect(queue.submit(mapping, item))) for item in (pending, another)]
    results = await asyncio.wait_for(asyncio.gather(*followers, return_exceptions=True), 0.1)
    assert all(isinstance(result, DeliveryUncertain) for result in results)
    assert not connector.started["later"].is_set()
    await queue.close()


async def test_submit_racing_cancelled_idle_worker_gets_replacement(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "teardown-race")
    first = await _operation(operations, mapping, "first-race")
    connector = ControlledConnector(); queue = LineageQueue(operations, connector, idle_timeout_seconds=60)
    initial = asyncio.create_task(_collect(queue.submit(mapping, first)))
    await connector.started["first-race"].wait(); connector.complete("first-race")
    await initial
    lineage = queue._lineages[mapping.lineage_key]
    second = await _operation(operations, mapping, "second-race")
    lineage.worker.cancel(); await asyncio.gather(lineage.worker, return_exceptions=True)
    tearing_down, release = asyncio.Event(), asyncio.Event()

    async def cancelling_worker():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            tearing_down.set()
            await release.wait()

    lineage.worker = asyncio.create_task(cancelling_worker())
    queue._lineages[mapping.lineage_key] = lineage
    await asyncio.sleep(0)
    lineage.worker.cancel(); await tearing_down.wait()
    follower = asyncio.create_task(_collect(queue.submit(mapping, second)))
    try:
        await asyncio.wait_for(connector.started["second-race"].wait(), 0.1)
        connector.complete("second-race")
        assert (await asyncio.wait_for(follower, 0.2))[-1].text == "second-race"
    finally:
        release.set()
        follower.cancel(); await asyncio.gather(follower, return_exceptions=True)
    await queue.close()


async def test_event_session_must_match_accepted_runtime_session(stores):
    class WrongRuntimeConnector(ControlledConnector):
        async def dispatch(self, command):
            yield _frame("accepted", command.correlation_id, command.payload.operation_id)
            event = _frame("hermes_event", command.correlation_id, command.payload.operation_id,
                           event_type="message.complete", text="wrong")
            yield event.model_copy(update={"payload": event.payload.model_copy(update={"session_id": "another-runtime"})})

    mappings, operations = stores
    mapping = await _mapping(mappings, "runtime-mismatch")
    operation = await _operation(operations, mapping, "runtime-mismatch")
    queue = LineageQueue(operations, WrongRuntimeConnector())
    with pytest.raises(DeliveryUncertain):
        await _collect(queue.submit(mapping, operation))
    stored, _ = await operations.create_or_get(TurnRequest(mapping.openwebui_chat_id, "user-runtime-mismatch", "x", "x"))
    assert stored.state is OperationState.DELIVERY_UNCERTAIN
    await queue.close()


async def test_submit_cannot_attach_after_concurrent_lineage_block_sweep(stores):
    mappings, operations = stores
    mapping = await _mapping(mappings, "block-race")
    first = await _operation(operations, mapping, "first-block-race")
    connector = ControlledConnector(); queue = LineageQueue(operations, connector, idle_timeout_seconds=60)
    initial = asyncio.create_task(_collect(queue.submit(mapping, first)))
    await connector.started["first-block-race"].wait(); connector.complete("first-block-race")
    await initial
    lineage = queue._lineages[mapping.lineage_key]
    pending = await _operation(operations, mapping, "late-block-race")

    await queue._lock.acquire()
    try:
        follower = asyncio.create_task(_collect(queue.submit(mapping, pending)))
        await asyncio.sleep(0)
        blocker = asyncio.create_task(queue._block_lineage(lineage, "reconciliation required"))
        await asyncio.sleep(0)
    finally:
        queue._lock.release()

    await blocker
    with pytest.raises(DeliveryUncertain, match="reconciliation"):
        await asyncio.wait_for(follower, 0.1)
    assert lineage.blocked and pending.id not in lineage.jobs
    await queue.close()
