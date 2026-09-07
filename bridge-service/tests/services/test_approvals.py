import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hermes_bridge.connector.protocol import (
    ApprovalRequestFrame, ApprovalSnapshotFrame, CommandErrorFrame, ProfileRoute,
)
from hermes_bridge.domain.models import ApprovalState, SessionIdentity, utc_now
from hermes_bridge.persistence.database import Database
from hermes_bridge.persistence.repositories import ApprovalRepository, MappingRepository
from hermes_bridge.services.approvals import ApprovalService


ROUTE = ProfileRoute(connection_id="local", profile="default", target_profile="backend-default")
RECORD = dict(runtime_session_id="runtime-1", stored_session_id="stored-1",
              request_id="request-1", command="git status", description="Run command",
              allow_permanent=True, smart_denied=False,
              choices=["once", "session", "always", "deny"])
SESSION = dict(runtime_session_id="runtime-1", stored_session_id="stored-1", status="waiting")


def snapshot(*, approvals=None, sessions=None, route=ROUTE, operation_id="scan-1"):
    return ApprovalSnapshotFrame(id=str(uuid4()), correlation_id=operation_id, sent_at=utc_now(),
        payload=dict(operation_id=operation_id, route=route,
                     approvals=[RECORD] if approvals is None else approvals,
                     sessions=[SESSION] if sessions is None else sessions))


class Connector:
    connected = False
    capabilities = frozenset({"approvals_v1"})
    routes = (ROUTE,)
    epoch = "epoch-1"

    def __init__(self):
        self.commands = []
        self.reply = None
        self.gate = None

    async def dispatch(self, command):
        self.commands.append(command)
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(self.reply, BaseException):
            raise self.reply
        if self.reply is not None:
            yield self.reply(command)


@pytest.fixture
async def harness(tmp_path):
    db = await Database.open(tmp_path / "bridge.db")
    mappings, store = MappingRepository(db), ApprovalRepository(db)
    identity = SessionIdentity("local", "default", "lineage-1", "stored-1", "Chat")
    await mappings.upsert_session(identity)
    await mappings.attach_chat(identity.lineage_key, "chat-1")
    connector, projected, scans = Connector(), [], []

    async def project(approval):
        assert not db.connection.in_transaction
        projected.append(approval)

    service = ApprovalService(connector, mappings, store, project=project,
                              request_scan=lambda: scans.append(True))
    await service.start()
    connector.connected = True
    yield SimpleNamespace(service=service, store=store, mappings=mappings,
                          connector=connector, projected=projected, scans=scans, db=db)
    await service.close()
    await db.close()


async def pending(h):
    await h.service.handle_connector_event(snapshot())
    return await h.store.by_route_request("local", "default", "backend-default", "stored-1", "request-1")


async def test_complete_snapshot_persists_and_projects_idempotently(harness):
    h = harness
    row = await pending(h)
    assert row.state is ApprovalState.PENDING
    assert row.chat_id == "chat-1"
    assert row.runtime_session_id == "runtime-1"
    assert row.choices == ("once", "session", "always", "deny")
    await h.service.handle_connector_event(snapshot())
    assert await h.store.get(row.id) == row
    assert h.projected == [row]


@pytest.mark.parametrize("sessions,state", [([SESSION], ApprovalState.RESOLVED_EXTERNAL),
                                           ([], ApprovalState.EXPIRED)])
async def test_complete_absence_settles_only_with_runtime_evidence(harness, sessions, state):
    row = await pending(harness)
    await harness.service.handle_connector_event(snapshot(approvals=[], sessions=sessions))
    current = await harness.store.get(row.id)
    assert current.state is state
    assert current.resolved_choice is None
    assert harness.projected[-1] == current


@pytest.mark.parametrize("case", ["wrong_route", "partial", "disconnected", "unsupported"])
async def test_untrusted_snapshot_cannot_settle_pending(harness, case):
    h = harness
    row = await pending(h)
    frame = snapshot(approvals=[])
    if case == "wrong_route":
        frame = snapshot(approvals=[], route=ROUTE.model_copy(update={"target_profile": "other"}))
    elif case == "partial":
        frame = snapshot(approvals=[{**RECORD, "runtime_session_id": "unknown"}])
    elif case == "disconnected":
        h.connector.connected = False
    else:
        h.connector.capabilities = frozenset()
    await h.service.handle_connector_event(frame)
    assert await h.store.get(row.id) == row


async def test_unmapped_candidates_request_one_inventory_scan_and_wait_for_stored_mapping(harness):
    h = harness
    records = [{**RECORD, "stored_session_id": "unmapped", "request_id": f"request-{i}"} for i in range(260)]
    frame = snapshot(approvals=records, sessions=[{**SESSION, "stored_session_id": "unmapped"}])
    await h.service.handle_connector_event(frame)
    assert await h.store.list_reconcilable() == []
    assert h.scans == [True]
    assert h.service.status_snapshot()["buffered"] == 256
    identity = SessionIdentity("local", "default", "new-lineage", "unmapped", "New")
    await h.mappings.upsert_session(identity)
    await h.mappings.attach_chat(identity.lineage_key, "chat-new")
    await h.service.handle_connector_event(frame)
    assert len(await h.store.list_reconcilable()) == 260
    assert h.service.status_snapshot()["buffered"] == 0


async def spin():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_connect_scans_each_route_immediately_and_every_two_seconds(harness, monkeypatch):
    h = harness
    intervals, ticks = [], asyncio.Queue()

    async def timer(delay):
        intervals.append(delay)
        await ticks.get()

    monkeypatch.setattr("hermes_bridge.services.approvals._sleep", timer, raising=False)
    await h.service.close()
    h.connector.routes = (ROUTE, ROUTE.model_copy(update={"profile": "second"}))
    await h.service.start()
    await spin()
    assert len(h.connector.commands) == 2
    assert {c.payload.route.profile for c in h.connector.commands} == {"default", "second"}
    assert all(c.kind == "approval_scan" and c.correlation_id == c.payload.operation_id
               for c in h.connector.commands)
    assert intervals == [2.0]
    await ticks.put(None)
    await spin()
    assert len(h.connector.commands) == 4
    assert len({c.correlation_id for c in h.connector.commands}) == 4
    h.connector.connected = False
    await ticks.put(None)
    await spin()
    assert len(h.connector.commands) == 4
    assert h.service.status_snapshot()["available"] is False


async def test_request_hint_scans_same_route_and_coalesces_overlap(harness):
    h = harness
    h.connector.gate = asyncio.Event()
    frame = ApprovalRequestFrame(id="hint", correlation_id="hint", sent_at=utc_now(),
                                 payload={**RECORD, "route": ROUTE})
    await h.service.handle_connector_event(frame)
    await spin()
    assert len(h.connector.commands) == 1
    await h.service.handle_connector_event(frame)
    await spin()
    assert len(h.connector.commands) == 1
    assert await h.store.list_reconcilable() == []


@pytest.mark.parametrize("failure", ["error", "timeout", "disconnect", "wrong_route", "wrong_operation"])
async def test_failed_scan_never_settles_pending(harness, failure):
    from hermes_bridge.connector.hub import ConnectorDisconnected
    h = harness
    row = await pending(h)
    if failure == "error":
        h.connector.reply = lambda c: CommandErrorFrame(id="error", correlation_id=c.correlation_id,
            sent_at=utc_now(), payload=dict(operation_id=c.payload.operation_id, code="scan_failed",
                                          message="secret error", acceptance_unknown=False))
    elif failure == "timeout":
        h.service._scan_timeout = 0.001
        h.connector.gate = asyncio.Event()
    elif failure == "disconnect":
        h.connector.reply = ConnectorDisconnected("secret transport error")
    else:
        h.connector.reply = lambda c: snapshot(approvals=[],
            operation_id="other" if failure == "wrong_operation" else c.payload.operation_id,
            route=ROUTE.model_copy(update={"target_profile": "other"}) if failure == "wrong_route" else ROUTE)
    h.service.connector_connected()
    await asyncio.sleep(0.02)
    assert h.connector.commands
    assert await h.store.get(row.id) == row


async def test_start_recovers_before_controls_or_scan_dispatch(harness):
    h = harness
    row = await pending(h)
    await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING)
    await h.service.close()
    assert h.service.status_snapshot()["available"] is False
    await h.service.start()
    assert (await h.store.get(row.id)).state is ApprovalState.DELIVERY_UNCERTAIN
    assert h.service.status_snapshot()["available"] is True


def resolve_request(row, **changes):
    from hermes_bridge.services.approvals import ResolveApprovalRequest
    return ResolveApprovalRequest(**dict(chat_id=row.chat_id, message_id=row.message_id,
                                        approval_id=row.id, choice="session") | changes)


def resolved(command, **changes):
    from hermes_bridge.connector.protocol import ApprovalResolvedFrame
    return ApprovalResolvedFrame(id="result", correlation_id=command.correlation_id,
        sent_at=utc_now(), payload=dict(operation_id=command.payload.operation_id,
            approval_id=command.payload.approval_id, choice=command.payload.choice, accepted=True) | changes)


@pytest.mark.parametrize("choice", ["once", "session", "always", "deny"])
async def test_resolution_delivers_repository_identity_and_exact_choice(harness, choice):
    h = harness
    row = await pending(h)
    h.connector.reply = resolved
    result = await h.service.resolve(resolve_request(row, choice=choice))
    assert result.state is ApprovalState.RESOLVED
    assert result.resolved_choice == choice
    assert h.projected[-1] == result
    command, = h.connector.commands
    assert command.kind == "resolve_approval"
    assert command.payload.route == ROUTE
    assert command.payload.runtime_session_id == "runtime-1"
    assert command.payload.stored_session_id == "stored-1"
    assert command.payload.request_id == "request-1"
    assert command.payload.choice == choice


@pytest.mark.parametrize("case,error", [
    ("missing", "ApprovalNotFound"), ("chat", "ApprovalIdentityMismatch"),
    ("message", "ApprovalIdentityMismatch"), ("choice", "ApprovalChoiceRejected"),
    ("offline", "ApprovalRouteUnavailable"), ("route", "ApprovalRouteUnavailable"),
    ("unsupported", "ApprovalRouteUnavailable"), ("closed", "ApprovalRouteUnavailable"),
    ("resolving", "ApprovalAlreadySettled"), ("settled", "ApprovalAlreadySettled"),
    ("uncertain", "ApprovalAlreadySettled"),
])
async def test_resolution_fails_closed_before_dispatch(harness, case, error):
    from hermes_bridge.services import approvals
    h = harness
    row = await pending(h)
    changes = {}
    if case == "missing": changes["approval_id"] = "missing"
    if case == "chat": changes["chat_id"] = "wrong-chat"
    if case == "message": changes["message_id"] = "wrong-message"
    if case == "choice": changes["choice"] = "other"
    if case == "offline": h.connector.connected = False
    if case == "route": h.connector.routes = (ROUTE.model_copy(update={"target_profile": "other"}),)
    if case == "unsupported": h.connector.capabilities = frozenset()
    if case == "closed": await h.service.close()
    if case == "settled":
        await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.EXPIRED)
    if case in {"resolving", "uncertain"}:
        await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING)
        if case == "uncertain": await h.store.recover_in_flight()
    with pytest.raises(getattr(approvals, error)):
        await h.service.resolve(resolve_request(row, **changes))
    assert h.connector.commands == []


@pytest.mark.parametrize("case,state,error", [
    ("rejected", ApprovalState.PENDING, "ApprovalChoiceRejected"),
    ("known_error", ApprovalState.PENDING, "ApprovalChoiceRejected"),
    ("stale", ApprovalState.RESOLVED_EXTERNAL, "ApprovalGone"),
    ("timeout", ApprovalState.DELIVERY_UNCERTAIN, "ApprovalDeliveryUncertain"),
    ("disconnect", ApprovalState.DELIVERY_UNCERTAIN, "ApprovalDeliveryUncertain"),
    ("unknown", ApprovalState.DELIVERY_UNCERTAIN, "ApprovalDeliveryUncertain"),
    ("wrong_choice", ApprovalState.DELIVERY_UNCERTAIN, "ApprovalDeliveryUncertain"),
    ("wrong_id", ApprovalState.DELIVERY_UNCERTAIN, "ApprovalDeliveryUncertain"),
    ("wrong_operation", ApprovalState.DELIVERY_UNCERTAIN, "ApprovalDeliveryUncertain"),
])
async def test_resolution_terminal_classification(harness, case, state, error):
    from hermes_bridge.services import approvals
    from hermes_bridge.connector.hub import ConnectorDisconnected
    h = harness
    row = await pending(h)
    if case == "rejected": h.connector.reply = lambda c: resolved(c, accepted=False)
    elif case == "wrong_choice": h.connector.reply = lambda c: resolved(c, choice="deny")
    elif case == "wrong_id": h.connector.reply = lambda c: resolved(c, approval_id="wrong")
    elif case == "wrong_operation": h.connector.reply = lambda c: resolved(c, operation_id="wrong")
    elif case == "timeout":
        h.service._resolve_timeout = 0.001
        h.connector.gate = asyncio.Event()
    elif case == "disconnect": h.connector.reply = ConnectorDisconnected("secret")
    else:
        h.connector.reply = lambda c: CommandErrorFrame(id="error", correlation_id=c.correlation_id,
            sent_at=utc_now(), payload=dict(operation_id=c.payload.operation_id,
                code="approval_stale" if case == "stale" else "approval_failed",
                message="secret", acceptance_unknown=case == "unknown"))
    with pytest.raises(getattr(approvals, error)):
        await h.service.resolve(resolve_request(row))
    result = await h.store.get(row.id)
    assert result.state is state
    assert result.resolved_choice is None
    assert h.projected[-1] == result


async def test_concurrent_resolution_claim_emits_exactly_one_command(harness):
    from hermes_bridge.services.approvals import ApprovalAlreadySettled
    h = harness
    row = await pending(h)
    h.connector.gate = asyncio.Event()
    h.connector.reply = resolved
    first = asyncio.create_task(h.service.resolve(resolve_request(row)))
    for _ in range(200):
        if h.connector.commands: break
        await asyncio.sleep(0.001)
    with pytest.raises(ApprovalAlreadySettled):
        await h.service.resolve(resolve_request(row))
    h.connector.gate.set()
    assert (await first).state is ApprovalState.RESOLVED
    assert len(h.connector.commands) == 1


@pytest.mark.parametrize("present,sessions,state", [
    (True, [SESSION], ApprovalState.PENDING),
    (False, [SESSION], ApprovalState.RESOLVED_EXTERNAL),
    (False, [], ApprovalState.EXPIRED),
])
async def test_uncertain_is_reconciled_only_by_complete_snapshot(harness, present, sessions, state):
    h = harness
    row = await pending(h)
    await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING)
    await h.store.recover_in_flight()
    h.projected.clear()
    await h.service.handle_connector_event(snapshot(approvals=[RECORD] if present else [], sessions=sessions))
    assert (await h.store.get(row.id)).state is state
    assert [p.state for p in h.projected] == [state]


async def test_inventory_completion_retries_buffer_without_new_snapshot(harness):
    h = harness
    frame = snapshot(approvals=[{**RECORD, "stored_session_id": "unmapped"}],
                     sessions=[{**SESSION, "stored_session_id": "unmapped"}])
    await h.service.handle_connector_event(frame)
    identity = SessionIdentity("local", "default", "new", "unmapped", "New")
    await h.mappings.upsert_session(identity)
    await h.mappings.attach_chat(identity.lineage_key, "chat-new")
    await h.service.retry_buffered()
    row, = await h.store.list_reconcilable()
    assert row.chat_id == "chat-new"
    assert row.stored_session_id == "unmapped"
    assert h.service.status_snapshot()["buffered"] == 0


async def test_late_observer_terminal_cannot_reconcile_another_advertised_route(harness):
    h = harness
    row = await pending(h)
    other = ROUTE.model_copy(update={"target_profile": "other"})
    h.connector.routes = (ROUTE, other)
    h.service.connector_connected()
    await spin()
    command = next(c for c in h.connector.commands if c.payload.route == other)
    # Hub observer delivery can lag behind the dispatch consumer.
    await h.service.handle_connector_event(snapshot(approvals=[], operation_id=command.correlation_id))
    assert await h.store.get(row.id) == row


async def test_projection_failure_does_not_interrupt_resolution_and_is_retried(harness):
    h = harness
    row = await pending(h)
    original = h.service._project

    async def unavailable(_):
        raise RuntimeError("secret projection error")

    h.service._project = unavailable
    h.connector.reply = resolved
    result = await h.service.resolve(resolve_request(row))
    assert result.state is ApprovalState.RESOLVED
    assert h.service.status_snapshot()["projection_failures"] == 1
    h.service._project = original
    await h.service.retry_buffered()
    assert h.projected[-1] == result
    assert h.service.status_snapshot()["projection_failures"] == 0


async def test_uncertain_snapshot_refreshes_runtime_and_choices_before_controls(harness):
    h = harness
    row = await pending(h)
    await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING)
    await h.store.recover_in_flight()
    await h.service.handle_connector_event(snapshot(
        approvals=[{**RECORD, "runtime_session_id": "runtime-2", "choices": ["deny"]}],
        sessions=[{**SESSION, "runtime_session_id": "runtime-2"}]))
    refreshed = await h.store.get(row.id)
    assert refreshed.state is ApprovalState.PENDING
    assert refreshed.runtime_session_id == "runtime-2"
    assert refreshed.choices == ("deny",)


async def test_cancelled_resolution_disables_controls(harness):
    h = harness
    row = await pending(h)
    h.connector.gate = asyncio.Event()
    task = asyncio.create_task(h.service.resolve(resolve_request(row)))
    for _ in range(200):
        if h.connector.commands: break
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert (await h.store.get(row.id)).state is ApprovalState.DELIVERY_UNCERTAIN


async def test_buffer_from_replaced_connector_is_not_replayed(harness):
    h = harness
    await h.service.handle_connector_event(snapshot(
        approvals=[{**RECORD, "stored_session_id": "unmapped"}],
        sessions=[{**SESSION, "stored_session_id": "unmapped"}]))
    identity = SessionIdentity("local", "default", "new", "unmapped", "New")
    await h.mappings.upsert_session(identity)
    await h.mappings.attach_chat(identity.lineage_key, "chat-new")
    h.connector.epoch = "replacement"
    await h.service.retry_buffered()
    assert await h.store.list_reconcilable() == []


async def test_route_removal_disables_aggregate_controls(harness):
    harness.connector.routes = ()
    assert harness.service.status_snapshot()["available"] is False


async def test_uncertain_without_exact_mapping_stays_disabled(harness):
    h = harness
    row = await pending(h)
    await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING)
    await h.store.recover_in_flight()
    await h.mappings.upsert_session(SessionIdentity("local", "default", "lineage-1", "rotated", "Chat"))
    await h.service.handle_connector_event(snapshot())
    assert (await h.store.get(row.id)).state is ApprovalState.DELIVERY_UNCERTAIN


async def test_snapshot_started_before_resolution_cannot_reenable_uncertain_choice(harness):
    from hermes_bridge.services.approvals import ApprovalDeliveryUncertain
    h = harness
    row = await pending(h)
    scan_release = asyncio.Event()

    async def dispatch(command):
        h.connector.commands.append(command)
        if command.kind == "approval_scan":
            await scan_release.wait()
            yield snapshot(operation_id=command.correlation_id)
        else:
            raise TimeoutError()

    h.connector.dispatch = dispatch
    h.service.connector_connected()
    await spin()
    with pytest.raises(ApprovalDeliveryUncertain):
        await h.service.resolve(resolve_request(row))
    scan_release.set()
    await asyncio.sleep(0.02)
    assert (await h.store.get(row.id)).state is ApprovalState.DELIVERY_UNCERTAIN


async def test_recovery_projects_disabled_state_before_start_returns(harness):
    h = harness
    row = await pending(h)
    await h.store.transition(row.id, frozenset({ApprovalState.PENDING}), ApprovalState.RESOLVING)
    await h.service.close()
    h.connector.connected = False
    h.projected.clear()
    await h.service.start()
    assert [row.state for row in h.projected] == [ApprovalState.DELIVERY_UNCERTAIN]


async def test_concurrent_start_does_not_run_recovery_twice(harness):
    h = harness
    await h.service.close()
    h.connector.connected = False
    recover = h.store.recover_in_flight
    calls = []

    async def counted_recover():
        calls.append(True)
        await asyncio.sleep(0)
        return await recover()

    h.store.recover_in_flight = counted_recover
    await asyncio.gather(h.service.start(), h.service.start())
    assert calls == [True]


async def test_buffer_observed_before_resolution_cannot_reenable_uncertain_choice(harness):
    from hermes_bridge.services.approvals import ApprovalDeliveryUncertain
    h = harness
    row = await pending(h)
    await h.mappings.upsert_session(SessionIdentity("local", "default", "lineage-1", "rotated", "Chat"))
    await h.service.handle_connector_event(snapshot())
    h.connector.reply = TimeoutError()
    with pytest.raises(ApprovalDeliveryUncertain):
        await h.service.resolve(resolve_request(row))
    await h.mappings.upsert_session(SessionIdentity("local", "default", "lineage-1", "stored-1", "Chat"))
    await h.service.retry_buffered()
    assert (await h.store.get(row.id)).state is ApprovalState.DELIVERY_UNCERTAIN
