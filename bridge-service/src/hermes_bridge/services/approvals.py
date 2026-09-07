"""Fail-closed orchestration of live, route-scoped Hermes approvals."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from hermes_bridge.connector.hub import ConnectorHub
from hermes_bridge.connector.protocol import (
    APPROVALS_CAPABILITY, ApprovalRequestFrame, ApprovalScanFrame, ApprovalSnapshotFrame,
    ApprovalResolvedFrame, CommandErrorFrame, ConnectorEvent, ProfileRoute, ResolveApprovalFrame,
)
from hermes_bridge.domain.models import (
    ApprovalState, InvalidTransition, PendingApproval, stable_approval_id, utc_now,
)
from hermes_bridge.domain.ports import ApprovalStore, MappingStore

_sleep = asyncio.sleep


@dataclass(frozen=True)
class ResolveApprovalRequest:
    chat_id: str
    message_id: str
    approval_id: str
    choice: str


class ApprovalNotFound(LookupError):
    pass


class ApprovalIdentityMismatch(ValueError):
    pass


class ApprovalAlreadySettled(RuntimeError):
    pass


class ApprovalChoiceRejected(ValueError):
    pass


class ApprovalRouteUnavailable(RuntimeError):
    pass


class ApprovalGone(RuntimeError):
    pass


class ApprovalDeliveryUncertain(RuntimeError):
    pass


def _route_key(route: ProfileRoute) -> tuple[str, str, str]:
    return route.connection_id, route.profile, route.target_profile


class ApprovalService:
    def __init__(
        self, connector: ConnectorHub, mappings: MappingStore, store: ApprovalStore,
        *, project: Callable[[PendingApproval], Awaitable[None]],
        request_scan: Callable[[], object], scan_interval_seconds: float = 2.0,
        scan_timeout_seconds: float = 10.0,
        resolve_timeout_seconds: float = 10.0,
    ) -> None:
        self._connector, self._mappings, self._store = connector, mappings, store
        self._project, self._request_scan = project, request_scan
        self._interval = scan_interval_seconds
        self._scan_timeout = scan_timeout_seconds
        self._resolve_timeout = resolve_timeout_seconds
        self._started = False
        self._lock = asyncio.Lock()
        self._buffer: dict[tuple[str, str, str], OrderedDict] = {}
        self._buffer_epochs: dict[tuple[str, str, str], str | None] = {}
        self._buffer_observed_at: dict[tuple[str, str, str], datetime] = {}
        self._projection_failures: set[str] = set()
        self._counts = {state.value: 0 for state in ApprovalState}
        self._periodic_task: asyncio.Task | None = None
        self._scans: dict[tuple[str, str, str], asyncio.Task] = {}
        self._scan_operations: OrderedDict[str, tuple[str, str, str]] = OrderedDict()

    async def start(self) -> None:
        async with self._lock:
            if not self._started:
                await self._store.recover_in_flight()
                self._counts = await self._store.count_by_state()
                for row in await self._store.list_reconcilable():
                    if row.state is ApprovalState.DELIVERY_UNCERTAIN:
                        await self._publish(row)
                self._started = True
                self.connector_connected()
                self._periodic_task = asyncio.create_task(self._periodic())

    async def close(self) -> None:
        self._started = False
        tasks = list(self._scans.values())
        if self._periodic_task is not None:
            tasks.append(self._periodic_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._scans.clear()
        self._periodic_task = None

    def connector_connected(self) -> None:
        for route in self._connector.routes:
            self._schedule_scan(route)

    def _schedule_scan(self, route: ProfileRoute) -> None:
        key = _route_key(route)
        task = self._scans.get(key)
        if self._available(route) and (task is None or task.done()):
            self._scans[key] = asyncio.create_task(self._scan(route))

    async def _periodic(self) -> None:
        while True:
            await _sleep(self._interval)
            self.connector_connected()

    async def _scan(self, route: ProfileRoute) -> None:
        operation_id = str(uuid4())
        epoch = self._connector.epoch
        self._scan_operations[operation_id] = _route_key(route)
        if len(self._scan_operations) > 256:
            self._scan_operations.popitem(last=False)
        command = ApprovalScanFrame(id=str(uuid4()), correlation_id=operation_id,
            sent_at=utc_now(), payload=dict(operation_id=operation_id, route=route))
        try:
            async with asyncio.timeout(self._scan_timeout):
                async for frame in self._connector.dispatch(command):
                    if (isinstance(frame, ApprovalSnapshotFrame)
                            and frame.correlation_id == operation_id
                            and frame.payload.operation_id == operation_id
                            and frame.payload.route == route
                            and self._connector.epoch == epoch):
                        await self._reconcile_snapshot(frame, scan_started_at=command.sent_at)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Scan failure is never evidence of absence; do not retain exception text.
            pass

    def _available(self, route: ProfileRoute | None = None) -> bool:
        return bool(self._started and self._connector.connected
                    and APPROVALS_CAPABILITY in self._connector.capabilities
                    and self._connector.routes
                    and (route is None or route in self._connector.routes))

    async def handle_connector_event(self, frame: ConnectorEvent, *, from_observer: bool = False) -> None:
        if isinstance(frame, ApprovalRequestFrame):
            self._schedule_scan(frame.payload.route)
            return
        # Correlated scan terminals are consumed once by their dispatch owner.
        if from_observer or frame.correlation_id in self._scan_operations:
            return
        await self._reconcile_snapshot(frame)

    async def _reconcile_snapshot(self, frame: ConnectorEvent, *, scan_started_at=None) -> None:
        if not isinstance(frame, ApprovalSnapshotFrame) or not self._available(frame.payload.route):
            return
        payload = frame.payload
        sessions = {(s.runtime_session_id, s.stored_session_id) for s in payload.sessions}
        identities = [(a.stored_session_id, a.request_id) for a in payload.approvals]
        # An inconsistent/partial snapshot is not negative evidence.
        if (len(identities) != len(set(identities))
                or any((a.runtime_session_id, a.stored_session_id) not in sessions
                       for a in payload.approvals)):
            return
        async with self._lock:
            route = payload.route
            key = _route_key(route)
            observed_at = scan_started_at or utc_now()
            protected = {
                (row.stored_session_id, row.request_id)
                for row in await self._store.list_for_route(*key)
                if scan_started_at is not None and row.updated_at > scan_started_at
            }
            buffered = OrderedDict()
            for candidate in payload.approvals:
                if (candidate.stored_session_id, candidate.request_id) in protected:
                    continue
                if not await self._persist_candidate(route, candidate):
                    buffered[(candidate.stored_session_id, candidate.request_id)] = candidate
                    if len(buffered) > 256:
                        buffered.popitem(last=False)
            if buffered and not self._buffer.get(key):
                self._request_scan()
            self._buffer[key] = buffered
            self._buffer_epochs[key] = self._connector.epoch
            self._buffer_observed_at[key] = observed_at
            for row in await self._store.list_for_route(*key):
                if (row.stored_session_id, row.request_id) in protected:
                    continue
                if row.state not in {ApprovalState.PENDING, ApprovalState.DELIVERY_UNCERTAIN}:
                    continue
                if (row.stored_session_id, row.request_id) in identities:
                    continue
                if (row.stored_session_id, row.request_id) not in identities:
                    target = (ApprovalState.RESOLVED_EXTERNAL
                              if (row.runtime_session_id, row.stored_session_id) in sessions
                              else ApprovalState.EXPIRED)
                    try:
                        if row.state is ApprovalState.DELIVERY_UNCERTAIN and target is ApprovalState.EXPIRED:
                            # The service lock prevents a resolution claim in this intermediate state.
                            row = await self._store.transition(
                                row.id, frozenset({row.state}), ApprovalState.PENDING)
                        changed = await self._store.transition(row.id, frozenset({row.state}), target)
                    except InvalidTransition:
                        continue
                    await self._publish(changed)
            self._counts = await self._store.count_by_state()

    async def _persist_candidate(self, route, candidate) -> bool:
        mapping = await self._mappings.by_route_and_stored_id(
            route.connection_id, route.profile, candidate.stored_session_id)
        if mapping is None or mapping.openwebui_chat_id is None:
            return False
        approval_id = stable_approval_id(*_route_key(route), mapping.lineage_root_id, candidate.request_id)
        previous = await self._store.get(approval_id)
        if previous is not None and previous.state is ApprovalState.DELIVERY_UNCERTAIN:
            # Refresh identity/choices in the same claim lock before exposing pending.
            await self._store.transition(approval_id, frozenset({previous.state}), ApprovalState.PENDING)
        now = utc_now()
        row = await self._store.upsert_pending(PendingApproval(
            id=approval_id, connection_id=route.connection_id, profile=route.profile,
            target_profile=route.target_profile, lineage_key=mapping.lineage_key,
            chat_id=mapping.openwebui_chat_id, message_id=f"{approval_id}_message",
            stored_session_id=candidate.stored_session_id,
            runtime_session_id=candidate.runtime_session_id, request_id=candidate.request_id,
            command=candidate.command, description=candidate.description,
            choices=tuple(candidate.choices), state=ApprovalState.PENDING,
            created_at=now, updated_at=now,
        ))
        if row != previous:
            await self._publish(row)
        return True

    async def retry_buffered(self) -> None:
        """Retry sanitized candidates when normal inventory has established mappings."""
        async with self._lock:
            for key, candidates in self._buffer.items():
                if self._buffer_epochs.get(key) != self._connector.epoch:
                    candidates.clear()
                    continue
                route = ProfileRoute(connection_id=key[0], profile=key[1], target_profile=key[2])
                if not self._available(route):
                    continue
                for identity, candidate in list(candidates.items()):
                    previous = await self._store.by_route_request(*key, *identity)
                    if previous is not None and previous.updated_at > self._buffer_observed_at[key]:
                        del candidates[identity]
                        continue
                    if await self._persist_candidate(route, candidate):
                        del candidates[identity]
            self._counts = await self._store.count_by_state()
            for approval_id in tuple(self._projection_failures):
                row = await self._store.get(approval_id)
                if row is not None:
                    await self._publish(row)

    async def _publish(self, row: PendingApproval) -> None:
        try:
            await self._project(row)
        except Exception:
            self._projection_failures.add(row.id)
        else:
            self._projection_failures.discard(row.id)

    def status_snapshot(self) -> dict:
        return {"available": self._available(), "counts": dict(self._counts),
                "projection_failures": len(self._projection_failures),
                "buffered": sum(len(values) for values in self._buffer.values())}

    async def resolve(self, request: ResolveApprovalRequest) -> PendingApproval:
        async with self._lock:
            row = await self._store.get(request.approval_id)
            if row is None:
                raise ApprovalNotFound("approval unavailable")
            if (request.chat_id, request.message_id) != (row.chat_id, row.message_id):
                raise ApprovalIdentityMismatch("approval identity mismatch")
            if row.state is not ApprovalState.PENDING:
                raise ApprovalAlreadySettled("approval is not actionable")
            if request.choice not in row.choices:
                raise ApprovalChoiceRejected("choice unavailable")
            route = ProfileRoute(connection_id=row.connection_id, profile=row.profile,
                                 target_profile=row.target_profile)
            if not self._available(route):
                raise ApprovalRouteUnavailable("approval route unavailable")
            try:
                row = await self._store.transition(row.id, frozenset({ApprovalState.PENDING}),
                                                   ApprovalState.RESOLVING)
            except InvalidTransition:
                raise ApprovalAlreadySettled("approval is not actionable") from None
            self._counts = await self._store.count_by_state()
            await self._publish(row)
        operation_id = str(uuid4())
        command = ResolveApprovalFrame(id=str(uuid4()), correlation_id=operation_id,
            sent_at=utc_now(), payload=dict(operation_id=operation_id, route=route,
                approval_id=row.id, runtime_session_id=row.runtime_session_id,
                stored_session_id=row.stored_session_id, request_id=row.request_id,
                choice=request.choice))
        target, error = ApprovalState.DELIVERY_UNCERTAIN, ApprovalDeliveryUncertain
        try:
            async with asyncio.timeout(self._resolve_timeout):
                async for frame in self._connector.dispatch(command):
                    if (frame.correlation_id != operation_id
                            or getattr(frame.payload, "operation_id", None) != operation_id):
                        continue
                    if isinstance(frame, ApprovalResolvedFrame):
                        if frame.payload.approval_id != row.id or frame.payload.choice != request.choice:
                            break
                        if frame.payload.accepted:
                            target, error = ApprovalState.RESOLVED, None
                        else:
                            target, error = ApprovalState.PENDING, ApprovalChoiceRejected
                        break
                    if isinstance(frame, CommandErrorFrame):
                        if not frame.payload.acceptance_unknown:
                            if frame.payload.code == "approval_stale":
                                target, error = ApprovalState.RESOLVED_EXTERNAL, ApprovalGone
                            else:
                                target, error = ApprovalState.PENDING, ApprovalChoiceRejected
                        break
        except asyncio.CancelledError:
            await self._finish_resolution(row, ApprovalState.DELIVERY_UNCERTAIN, None)
            raise
        except Exception:
            pass
        result = await self._finish_resolution(
            row, target, request.choice if target is ApprovalState.RESOLVED else None)
        if error is not None:
            raise error("approval response was not confirmed")
        return result

    async def _finish_resolution(self, row, target, choice) -> PendingApproval:
        async with self._lock:
            if target is ApprovalState.RESOLVED_EXTERNAL:
                row = await self._store.transition(row.id, frozenset({row.state}),
                                                   ApprovalState.DELIVERY_UNCERTAIN)
            result = await self._store.transition(row.id, frozenset({row.state}), target,
                                                  resolved_choice=choice)
            self._counts = await self._store.count_by_state()
            await self._publish(result)
            return result
