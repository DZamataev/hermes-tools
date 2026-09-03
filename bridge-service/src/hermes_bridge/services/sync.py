"""Authoritative Hermes scans, live ingest, and conservative recovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from hermes_bridge.connector.protocol import (
    CommandErrorFrame,
    ConnectorEvent,
    HermesEventFrame,
    ProfileRoute,
    ReplayFrame,
    ReplayGapFrame,
)
from hermes_bridge.domain.models import (
    Operation,
    OperationState,
    SessionIdentity,
    SessionMapping,
)
from hermes_bridge.hermes.events import EventKind, normalize_event
from hermes_bridge.openwebui.mirror import MirrorResult


@dataclass(frozen=True)
class SyncFailure:
    lineage_key: str
    chat_id: str | None
    code: str


@dataclass(frozen=True)
class SyncReport:
    succeeded: int
    failed: int
    skipped: int
    failures: tuple[SyncFailure, ...]


@dataclass(frozen=True)
class RecoveryReport:
    completed: int
    uncertain: int
    failures: tuple[SyncFailure, ...]


class SyncService:
    """Coordinate snapshot truth without ever guessing whether a submit landed."""

    def __init__(
        self,
        hermes: Any,
        mirror: Any,
        mappings: Any,
        operations: Any,
        events: Any,
        connector: Any,
        queue: Any,
        *,
        interval_seconds: float,
        event_queue_size: int = 256,
    ) -> None:
        self._hermes = hermes
        self._mirror = mirror
        self._mappings = mappings
        self._operations = operations
        self._events = events
        self._connector = connector
        self._queue = queue
        self._interval = interval_seconds
        self._event_queue: asyncio.Queue[ConnectorEvent] = asyncio.Queue(event_queue_size)
        self._tasks: set[asyncio.Task] = set()
        self._periodic_task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self._closed = False
        self._runtime_lineages: dict[tuple[str, str, str], str] = {}
        self._last_scan: datetime | None = None
        self._last_successful_reconciliation: datetime | None = None
        self._failures: dict[str, SyncFailure] = {}
        self._openwebui_ready = False
        self._hermes_ready = False
        self._profile_count = 0
        self._operation_counts = {state.value: 0 for state in OperationState}
        self._dropped_events = 0
        self._scan_lock = asyncio.Lock()
        self._recovery_lock = asyncio.Lock()
        self._lineage_locks: dict[str, asyncio.Lock] = {}

    @property
    def background_task_count(self) -> int:
        return sum(not task.done() for task in self._tasks)

    async def start(self) -> None:
        if self._closed or self._periodic_task is not None:
            return
        self._connector.set_observer(
            on_connect=self._connector_connected,
            on_event=self._connector_event,
        )
        self._event_task = self._spawn(self._event_loop())
        self._spawn(self.full_scan())
        self._periodic_task = self._spawn(self._periodic())

    async def close(self) -> None:
        self._closed = True
        self._connector.set_observer(on_connect=None, on_event=None)
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._periodic_task = None
        self._event_task = None

    def _spawn(self, awaitable) -> asyncio.Task:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._remember_failure("background-task", None, error)

    async def _periodic(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.full_scan()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._remember_failure("periodic-scan", None, error)

    def _connector_connected(self, _hello: object) -> None:
        if not self._closed:
            self._spawn(self._recover_then_scan())

    async def _recover_then_scan(self) -> None:
        try:
            await self.recover_incomplete_operations()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._remember_failure("connector-recovery", None, error)
        try:
            await self.full_scan()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._remember_failure("connector-scan", None, error)

    def _connector_event(self, event: ConnectorEvent) -> None:
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            # Snapshot reconciliation is the repair path for a bounded live feed.
            self._dropped_events += 1

    async def _event_loop(self) -> None:
        while True:
            event = await self._event_queue.get()
            try:
                await self.handle_connector_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._remember_failure("connector-event", None, error)

    def remember_runtime(
        self,
        connection_id: str,
        profile: str,
        runtime_session_id: str,
        lineage_key: str,
    ) -> None:
        self._runtime_lineages[(connection_id, profile, runtime_session_id)] = lineage_key

    async def full_scan(self) -> SyncReport:
        async with self._scan_lock:
            failures: list[SyncFailure] = []
            succeeded = skipped = 0
            routes = self._unique_routes()
            self._profile_count = len(routes)
            self._hermes_ready = False
            dependency_results = await asyncio.gather(
                self._verify_openwebui(),
                *(self._read_inventory_probe(route) for route in routes),
                return_exceptions=True,
            )
            openwebui_result, *inventory_results = dependency_results
            if isinstance(openwebui_result, asyncio.CancelledError):
                raise openwebui_result
            if isinstance(openwebui_result, BaseException):
                failures.append(
                    self._remember_failure("openwebui", None, openwebui_result)
                )

            seen_lineages: set[str] = set()
            for route, inventory_result in zip(routes, inventory_results, strict=True):
                route_key = f"route:{route.connection_id}:{route.profile}"
                if isinstance(inventory_result, asyncio.CancelledError):
                    raise inventory_result
                if isinstance(inventory_result, BaseException):
                    failures.append(
                        self._remember_failure(route_key, None, inventory_result)
                    )
                    continue
                sessions = inventory_result
                self._failures.pop(route_key, None)
                for session in sessions:
                    identity = SessionIdentity(
                        route.connection_id,
                        route.profile,
                        session.lineage_root_id,
                        session.id,
                        session.title,
                    )
                    if identity.lineage_key in seen_lineages:
                        continue
                    seen_lineages.add(identity.lineage_key)
                    current: SessionMapping | None = None
                    try:
                        async with self._lineage_lock(identity.lineage_key):
                            previous = await self._mappings.by_lineage_key(
                                identity.lineage_key
                            )
                            current = await self._mappings.upsert_session(identity)
                            revision = session.revision
                            unchanged = (
                                previous is not None
                                and previous.openwebui_chat_id is not None
                                and previous.stored_session_id
                                == identity.stored_session_id
                                and previous.title == identity.title
                                and revision is not None
                                and previous.last_source_revision == revision
                            )
                            if unchanged:
                                skipped += 1
                                continue
                            if not self._openwebui_ready:
                                raise RuntimeError("OpenWebUI unavailable")
                            messages = await self._hermes.read_messages(
                                identity.stored_session_id, route.target_profile
                            )
                            await self._mirror.reconcile(identity, messages)
                            if revision is not None:
                                await self._mappings.update_source_revision(
                                    identity.lineage_key, revision
                                )
                    except Exception as error:
                        failure = self._remember_failure(
                            identity.lineage_key,
                            current.openwebui_chat_id if current is not None else None,
                            error,
                        )
                        failures.append(failure)
                        continue
                    succeeded += 1
                    self._mark_reconciled(identity.lineage_key)

            self._last_scan = datetime.now(timezone.utc)
            await self._refresh_operation_counts()
            return SyncReport(succeeded, len(failures), skipped, tuple(failures))

    async def _verify_openwebui(self) -> None | Exception:
        try:
            await self._mirror.verify()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._openwebui_ready = False
            return error
        self._openwebui_ready = True
        self._failures.pop("openwebui", None)
        return None

    async def _read_inventory_probe(self, route: ProfileRoute) -> list[Any]:
        sessions = [
            session
            async for session in self._hermes.iter_sessions(route.target_profile)
        ]
        self._hermes_ready = True
        return sessions

    async def reconcile_lineage(self, lineage_key: str) -> MirrorResult:
        async with self._lineage_lock(lineage_key):
            return await self._reconcile_lineage_locked(lineage_key)

    async def _reconcile_lineage_locked(self, lineage_key: str) -> MirrorResult:
        mapping = await self._mappings.by_lineage_key(lineage_key)
        if mapping is None:
            raise LookupError("lineage mapping is unavailable")
        messages = await self._hermes.read_messages(
            mapping.stored_session_id, self._target_profile(mapping)
        )
        result = await self._mirror.reconcile(
            SessionIdentity(
                mapping.connection_id,
                mapping.profile,
                mapping.lineage_root_id,
                mapping.stored_session_id,
                mapping.title,
            ),
            messages,
        )
        self._mark_reconciled(lineage_key)
        return result

    async def handle_connector_event(self, frame: ConnectorEvent) -> None:
        if not isinstance(frame, HermesEventFrame):
            return
        normalized = normalize_event(frame, connector_epoch=self._connector.epoch)
        if normalized is None:
            return
        mapping = await self._mapping_for_frame(frame, normalized.operation_id)
        if mapping is None:
            if normalized.stored_session_id:
                self._spawn(self.full_scan())
            return
        self.remember_runtime(
            frame.payload.connection_id,
            frame.payload.profile,
            frame.payload.session_id,
            mapping.lineage_key,
        )
        if (
            frame.payload.seq is not None
            and mapping.last_event_epoch == self._connector.epoch
            and mapping.last_event_seq is not None
            and frame.payload.seq <= mapping.last_event_seq
        ):
            return
        if (
            normalized.kind is EventKind.SESSION_INFO
            and normalized.stored_session_id
            and normalized.stored_session_id != mapping.stored_session_id
        ):
            mapping = await self._mappings.upsert_session(
                SessionIdentity(
                    mapping.connection_id,
                    mapping.profile,
                    mapping.lineage_root_id,
                    normalized.stored_session_id,
                    mapping.title,
                )
            )
        event = replace(
            normalized,
            lineage_key=mapping.lineage_key,
            stored_session_id=normalized.stored_session_id or mapping.stored_session_id,
        )
        if not await self._events.record(event):
            return
        if event.kind in {EventKind.MESSAGE_COMPLETE, EventKind.ERROR}:
            try:
                await self.reconcile_lineage(mapping.lineage_key)
            except Exception as error:
                self._remember_failure(mapping.lineage_key, mapping.openwebui_chat_id, error)

    async def recover_incomplete_operations(self) -> RecoveryReport:
        async with self._recovery_lock:
            failures: list[SyncFailure] = []
            completed = 0
            incomplete = await self._operations.list_incomplete()
            touched_lineages: set[str] = set()
            for operation in incomplete:
                if operation.state is OperationState.PENDING:
                    continue
                current = operation
                if current.state is not OperationState.DELIVERY_UNCERTAIN:
                    current = await self._operations.transition(
                        current.id,
                        OperationState.DELIVERY_UNCERTAIN,
                        error_code="recovery_required",
                        error_message="operation requires authoritative recovery",
                    )
                mapping = (
                    await self._mappings.by_lineage_key(current.lineage_key)
                    if current.lineage_key
                    else None
                )
                if mapping is None:
                    failures.append(
                        self._remember_failure(
                            current.lineage_key or "unmapped-operation",
                            current.chat_id,
                            LookupError("mapping unavailable"),
                        )
                    )
                    continue
                touched_lineages.add(mapping.lineage_key)
                try:
                    recovered = await self._recover_one(current, mapping)
                except Exception as error:
                    failures.append(
                        self._remember_failure(mapping.lineage_key, mapping.openwebui_chat_id, error)
                    )
                    continue
                if recovered.state is OperationState.COMPLETED:
                    completed += 1

            remaining = await self._operations.list_incomplete()
            blocked_lineages = {
                item.lineage_key
                for item in remaining
                if item.lineage_key and item.state is not OperationState.PENDING
            }
            pending_lineages = {item.lineage_key for item in remaining if item.lineage_key}
            for lineage_key in touched_lineages | pending_lineages:
                mapping = await self._mappings.by_lineage_key(lineage_key)
                if mapping is not None:
                    await self._queue.reconcile_lineage(
                        mapping,
                        blocked=lineage_key in blocked_lineages or not self._connector.connected,
                    )
            uncertain = sum(
                item.state is OperationState.DELIVERY_UNCERTAIN for item in remaining
            )
            await self._refresh_operation_counts()
            return RecoveryReport(completed, uncertain, tuple(failures))

    async def _recover_one(
        self, operation: Operation, mapping: SessionMapping
    ) -> Operation:
        route = self._route(mapping)
        if not self._connector.connected or route is None or not operation.runtime_session_id:
            return await self._snapshot_proof(operation, mapping)
        command = ReplayFrame.model_validate(
            {
                "protocol": 1,
                "kind": "replay",
                "id": str(uuid4()),
                "correlation_id": str(operation.id),
                "sent_at": datetime.now(timezone.utc),
                "payload": {
                    "operation_id": str(operation.id),
                    "route": route,
                    "runtime_session_id": operation.runtime_session_id,
                    "after_seq": operation.last_event_seq or 0,
                },
            }
        )
        current = operation
        async for frame in self._connector.dispatch(command):
            if isinstance(frame, ReplayGapFrame):
                return await self._snapshot_proof(current, mapping)
            if isinstance(frame, CommandErrorFrame):
                return await self._snapshot_proof(current, mapping)
            if not isinstance(frame, HermesEventFrame):
                continue
            event = normalize_event(frame, connector_epoch=self._connector.epoch)
            if event is None or event.operation_id != operation.id:
                continue
            event = replace(event, lineage_key=mapping.lineage_key)
            await self._events.record(event)
            if event.kind in {EventKind.MESSAGE_DELTA, EventKind.MESSAGE_START}:
                if current.state is OperationState.DELIVERY_UNCERTAIN:
                    current = await self._operations.transition(
                        current.id, OperationState.ACCEPTED
                    )
                if event.kind is EventKind.MESSAGE_DELTA and current.state is OperationState.ACCEPTED:
                    current = await self._operations.transition(
                        current.id, OperationState.STREAMING
                    )
            elif event.kind in {EventKind.MESSAGE_COMPLETE, EventKind.ERROR}:
                hermes_error = (
                    event.kind is EventKind.ERROR
                    or frame.payload.data.get("status") == "error"
                )
                current = await self._operations.transition(
                    current.id,
                    OperationState.COMPLETED,
                    result_text=None if hermes_error else event.text or "",
                    error_code="hermes_error" if hermes_error else None,
                    error_message="Hermes turn failed" if hermes_error else None,
                    last_event_seq=event.sequence,
                )
                await self.reconcile_lineage(mapping.lineage_key)
                return current
        if current.state in {OperationState.ACCEPTED, OperationState.STREAMING}:
            current = await self._operations.transition(
                current.id,
                OperationState.DELIVERY_UNCERTAIN,
                error_code="replay_incomplete",
                error_message="replay did not prove completion",
            )
        return current

    async def _snapshot_proof(
        self, operation: Operation, mapping: SessionMapping
    ) -> Operation:
        async with self._lineage_lock(mapping.lineage_key):
            return await self._snapshot_proof_locked(operation, mapping)

    async def _snapshot_proof_locked(
        self, operation: Operation, mapping: SessionMapping
    ) -> Operation:
        messages = await self._hermes.read_messages(
            mapping.stored_session_id, self._target_profile(mapping)
        )
        await self._mirror.reconcile(
            SessionIdentity(
                mapping.connection_id,
                mapping.profile,
                mapping.lineage_root_id,
                mapping.stored_session_id,
                mapping.title,
            ),
            messages,
        )
        self._mark_reconciled(mapping.lineage_key)
        # An explicit durable identity is proof. Text, order, and timestamps are not.
        tagged_assistant = next(
            (
                message
                for message in messages
                if message.bridge_operation_id == str(operation.id)
                and message.role == "assistant"
            ),
            None,
        )
        if tagged_assistant is None:
            return operation
        return await self._operations.transition(
            operation.id,
            OperationState.COMPLETED,
            result_text=tagged_assistant.content,
            error_code=None,
            error_message=None,
        )

    async def _mapping_for_frame(
        self, frame: HermesEventFrame, operation_id: UUID | None
    ) -> SessionMapping | None:
        if operation_id is not None:
            operation = await self._operations.get(operation_id)
            if operation is not None and operation.lineage_key:
                return await self._mappings.by_lineage_key(operation.lineage_key)
        key = self._runtime_lineages.get(
            (frame.payload.connection_id, frame.payload.profile, frame.payload.session_id)
        )
        if key:
            return await self._mappings.by_lineage_key(key)
        stored = frame.payload.data.get("stored_session_id")
        if isinstance(stored, str) and stored:
            return await self._mappings.by_route_and_stored_id(
                frame.payload.connection_id, frame.payload.profile, stored
            )
        return await self._mappings.by_route_and_stored_id(
            frame.payload.connection_id, frame.payload.profile, frame.payload.session_id
        )

    def _unique_routes(self) -> tuple[ProfileRoute, ...]:
        unique: dict[tuple[str, str, str], ProfileRoute] = {}
        for route in self._connector.routes:
            unique[(route.connection_id, route.profile, route.target_profile)] = route
        return tuple(unique.values())

    def _route(self, mapping: SessionMapping) -> ProfileRoute | None:
        routes = [
            route
            for route in self._connector.routes
            if route.connection_id == mapping.connection_id
            and route.profile == mapping.profile
        ]
        return routes[0] if len(routes) == 1 else None

    def _target_profile(self, mapping: SessionMapping) -> str:
        route = self._route(mapping)
        return route.target_profile if route is not None else mapping.profile

    def _remember_failure(
        self, lineage_key: str, chat_id: str | None, error: BaseException
    ) -> SyncFailure:
        failure = SyncFailure(lineage_key, chat_id, type(error).__name__)
        self._failures[lineage_key] = failure
        return failure

    def _mark_reconciled(self, lineage_key: str) -> None:
        self._last_successful_reconciliation = datetime.now(timezone.utc)
        self._failures.pop(lineage_key, None)

    async def _refresh_operation_counts(self) -> None:
        self._operation_counts = await self._operations.count_by_state()

    async def current_status_snapshot(self) -> dict[str, Any]:
        await self._refresh_operation_counts()
        return self.status_snapshot()

    def _lineage_lock(self, lineage_key: str) -> asyncio.Lock:
        return self._lineage_locks.setdefault(lineage_key, asyncio.Lock())

    @property
    def ready_components(self) -> dict[str, str]:
        return {
            "openwebui": "ready" if self._openwebui_ready else "unavailable",
            "hermes_read_api": "ready" if self._hermes_ready else "unavailable",
        }

    def status_snapshot(self) -> dict[str, Any]:
        active = sum(
            self._operation_counts.get(state.value, 0)
            for state in (OperationState.OFFERED, OperationState.ACCEPTED, OperationState.STREAMING)
        )
        failed = self._operation_counts.get(OperationState.REJECTED.value, 0)
        return {
            "last_scan": self._iso(self._last_scan),
            "last_successful_reconciliation": self._iso(
                self._last_successful_reconciliation
            ),
            "connector": {
                "connected": bool(self._connector.connected),
                "version": self._connector.connector_version,
                "epoch": self._connector.epoch,
                "profile_count": self._profile_count,
            },
            "components": self.ready_components,
            "queue": {
                "pending": self._operation_counts.get(OperationState.PENDING.value, 0),
                "active": active,
                "uncertain": self._operation_counts.get(
                    OperationState.DELIVERY_UNCERTAIN.value, 0
                ),
                "failed": failed,
            },
            "dropped_live_events": self._dropped_events,
            "failures": [
                {
                    "lineage_key": failure.lineage_key,
                    "chat_id": failure.chat_id,
                    "code": failure.code,
                }
                for failure in sorted(
                    self._failures.values(), key=lambda item: item.lineage_key
                )
            ],
        }

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None
