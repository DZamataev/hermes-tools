"""Durable per-lineage FIFO dispatch independent of browser lifetimes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from hermes_bridge.connector.hub import ConnectorDisconnected, ConnectorOffline
from hermes_bridge.connector.protocol import (
    AcceptedFrame, CommandErrorFrame, HermesEventFrame, ProfileRoute, SubmitFrame,
)
from hermes_bridge.domain.models import (
    InvalidTransition, Operation, OperationState, SessionMapping, TurnEvent,
)
from hermes_bridge.domain.ports import OperationStore


class DesktopOffline(RuntimeError):
    """The Desktop connector was definitively offline before offer."""


class DeliveryUncertain(RuntimeError):
    """The connector may have delivered a persisted operation."""


class OperationRejected(RuntimeError):
    """Hermes definitively rejected an operation."""


class HermesTurnFailed(RuntimeError):
    """Hermes accepted the turn but produced a terminal error."""


class _Done:
    pass


DONE = _Done()


@dataclass
class _Job:
    operation: Operation
    history: list[TurnEvent] = field(default_factory=list)
    followers: set[asyncio.Queue[Any]] = field(default_factory=set)
    terminal: BaseException | _Done | None = None


@dataclass
class _Lineage:
    mapping: SessionMapping
    wake: asyncio.Queue[None]
    jobs: dict[UUID, _Job] = field(default_factory=dict)
    worker: asyncio.Task | None = None
    blocked: bool = False


class LineageQueue:
    def __init__(self, operations: OperationStore, connector: Any, *, queue_size: int = 256,
                 idle_timeout_seconds: float = 60.0) -> None:
        self._operations = operations
        self._connector = connector
        self._queue_size = queue_size
        self._idle_timeout = idle_timeout_seconds
        self._lineages: dict[str, _Lineage] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def submit(self, mapping: SessionMapping, operation: Operation) -> AsyncIterator[TurnEvent]:
        if mapping.openwebui_chat_id != operation.chat_id or operation.lineage_key != mapping.lineage_key:
            raise ValueError("operation does not belong to mapping")
        follower: asyncio.Queue[Any] = asyncio.Queue(self._queue_size)
        async with self._lock:
            existing_lineage = self._lineages.get(mapping.lineage_key)
            existing = existing_lineage.jobs.get(operation.id) if existing_lineage else None
            blocked = bool(existing_lineage and existing_lineage.blocked and existing is None)
            if existing is not None and existing.terminal is not None:
                existing = None
            elif existing is not None:
                self._attach(existing, follower)
        if blocked:
            raise DeliveryUncertain("lineage is blocked pending reconciliation")
        if existing is not None:
            async for event in self._follow(existing, follower):
                yield event
            return
        if operation.state is OperationState.COMPLETED:
            if operation.error_code:
                raise HermesTurnFailed(operation.error_message or operation.error_code)
            yield TurnEvent(f"replay:{operation.id}", mapping.lineage_key,
                            operation.last_event_seq or 0, "message.complete",
                            text=operation.result_text or "", operation_id=operation.id)
            return
        if operation.state is OperationState.REJECTED:
            error = DesktopOffline if operation.error_code == "hermes_desktop_offline" else OperationRejected
            raise error(operation.error_message or operation.error_code or "operation rejected")
        if operation.state is OperationState.DELIVERY_UNCERTAIN:
            raise DeliveryUncertain(operation.error_message or "delivery is uncertain")
        if operation.state is not OperationState.PENDING:
            operation = await self._mark_uncertain(operation, "bridge_restarted", "active operation requires reconciliation")
            raise DeliveryUncertain(operation.error_message or "delivery is uncertain")

        async with self._lock:
            if self._closed:
                raise RuntimeError("lineage queue is closed")
            lineage = self._lineages.get(mapping.lineage_key)
            if lineage is None:
                lineage = _Lineage(mapping, asyncio.Queue(self._queue_size))
                self._lineages[mapping.lineage_key] = lineage
            elif lineage.blocked:
                raise DeliveryUncertain("lineage is blocked pending reconciliation")
            else:
                lineage.mapping = mapping
            job = lineage.jobs.setdefault(operation.id, _Job(operation))
            self._attach(job, follower)
            if (lineage.worker is None or lineage.worker.done()
                    or lineage.worker.cancelling()):
                lineage.worker = asyncio.create_task(self._run(mapping.lineage_key, lineage))
            try:
                lineage.wake.put_nowait(None)
            except asyncio.QueueFull:
                pass
        async for event in self._follow(job, follower):
            yield event

    def _attach(self, job: _Job, follower: asyncio.Queue[Any]) -> None:
        job.followers.add(follower)
        for event in job.history:
            follower.put_nowait(event)
        if job.terminal is not None:
            follower.put_nowait(job.terminal)

    async def _follow(self, job: _Job, follower: asyncio.Queue[Any]) -> AsyncIterator[TurnEvent]:
        try:
            while True:
                item = await follower.get()
                if item is DONE:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            async with self._lock:
                job.followers.discard(follower)

    async def _run(self, lineage_key: str, lineage: _Lineage) -> None:
        try:
            while not self._closed:
                try:
                    await asyncio.wait_for(lineage.wake.get(), self._idle_timeout)
                except TimeoutError:
                    return
                while not lineage.blocked:
                    pending = await self._operations.list_pending(lineage_key)
                    if not pending:
                        break
                    operation = pending[0]
                    async with self._lock:
                        job = lineage.jobs.setdefault(operation.id, _Job(operation))
                    await self._process(lineage, job)
        except asyncio.CancelledError:
            raise
        finally:
            async with self._lock:
                current = asyncio.current_task()
                if self._lineages.get(lineage_key) is not lineage or lineage.worker is not current:
                    return
                lineage.worker = None
                if not self._closed and not lineage.blocked and not lineage.wake.empty():
                    lineage.worker = asyncio.create_task(self._run(lineage_key, lineage))
                elif not lineage.blocked and lineage.wake.empty():
                    self._lineages.pop(lineage_key, None)

    async def _process(self, lineage: _Lineage, job: _Job) -> None:
        operation = job.operation
        if not self._connector.connected:
            job.operation = await self._operations.transition(
                operation.id, OperationState.REJECTED, error_code="hermes_desktop_offline",
                error_message="Hermes Desktop connector is offline")
            await self._finish(job, DesktopOffline("Hermes Desktop connector is offline"))
            return
        route = self._route(lineage.mapping)
        if route is None:
            job.operation = await self._operations.transition(
                operation.id, OperationState.REJECTED, error_code="route_unavailable",
                error_message="Hermes Desktop route is unavailable")
            await self._finish(job, OperationRejected("Hermes Desktop route is unavailable"))
            return

        try:
            job.operation = await self._operations.transition(operation.id, OperationState.OFFERED)
        except InvalidTransition:
            await self._block_lineage(lineage, "lineage is blocked pending reconciliation")
            return
        command = SubmitFrame.model_validate({
            "protocol": 1, "kind": "submit", "id": str(uuid4()),
            "correlation_id": str(operation.id), "sent_at": datetime.now(timezone.utc),
            "payload": {"operation_id": str(operation.id), "route": route,
                        "stored_session_id": lineage.mapping.stored_session_id,
                        "text": operation.text, "queued": True},
        })
        accepted = False
        runtime_session_id: str | None = None
        terminal = False
        deltas: list[str] = []
        try:
            async for frame in self._connector.dispatch(command):
                if isinstance(frame, AcceptedFrame):
                    self._validate_operation(frame.payload.operation_id, operation.id)
                    job.operation = await self._operations.transition(operation.id, OperationState.ACCEPTED)
                    accepted = True
                    runtime_session_id = frame.payload.runtime_session_id
                elif isinstance(frame, CommandErrorFrame):
                    self._validate_operation(frame.payload.operation_id, operation.id)
                    if frame.payload.acceptance_unknown:
                        raise DeliveryUncertain(frame.payload.message)
                    job.operation = await self._operations.transition(
                        operation.id, OperationState.REJECTED, error_code=frame.payload.code,
                        error_message=frame.payload.message)
                    await self._finish(job, OperationRejected(frame.payload.message))
                    terminal = True
                elif isinstance(frame, HermesEventFrame):
                    self._validate_event(frame, operation, lineage.mapping, runtime_session_id)
                    kind = frame.payload.event_type
                    if kind == "message.delta":
                        if not accepted:
                            raise DeliveryUncertain("event arrived before acceptance")
                        if job.operation.state is OperationState.ACCEPTED:
                            job.operation = await self._operations.transition(operation.id, OperationState.STREAMING)
                        text = self._text(frame.payload.data)
                        if text:
                            deltas.append(text)
                            await self._publish(job, self._event(frame, lineage.mapping, operation.id, kind, text))
                    elif kind == "message.complete":
                        if not accepted:
                            raise DeliveryUncertain("completion arrived before acceptance")
                        if frame.payload.data.get("status") == "error":
                            message = self._error_text(frame.payload.data)
                            job.operation = await self._operations.transition(
                                operation.id, OperationState.COMPLETED, error_code="hermes_error",
                                error_message=message, last_event_seq=frame.payload.seq)
                            await self._finish(job, HermesTurnFailed(message))
                            terminal = True
                            return
                        result = self._text(frame.payload.data) or "".join(deltas)
                        job.operation = await self._operations.transition(
                            operation.id, OperationState.COMPLETED, result_text=result,
                            last_event_seq=frame.payload.seq)
                        await self._publish(job, self._event(frame, lineage.mapping, operation.id, kind, result))
                        await self._finish(job, DONE)
                        terminal = True
                    elif kind == "error":
                        if not accepted:
                            raise DeliveryUncertain("error arrived before acceptance")
                        message = self._error_text(frame.payload.data)
                        job.operation = await self._operations.transition(
                            operation.id, OperationState.COMPLETED, error_code="hermes_error",
                            error_message=message, last_event_seq=frame.payload.seq)
                        await self._finish(job, HermesTurnFailed(message))
                        terminal = True
                if terminal:
                    return
            if not terminal:
                raise DeliveryUncertain("connector ended before a terminal event")
        except (ConnectorOffline, ConnectorDisconnected, DeliveryUncertain) as error:
            if job.operation.state not in {OperationState.DELIVERY_UNCERTAIN, OperationState.COMPLETED, OperationState.REJECTED}:
                job.operation = await self._mark_uncertain(job.operation, "connector_disconnected", str(error))
            await self._block_lineage(lineage, str(error))
        except Exception as error:
            if job.operation.state not in {OperationState.DELIVERY_UNCERTAIN, OperationState.COMPLETED, OperationState.REJECTED}:
                job.operation = await self._mark_uncertain(job.operation, "protocol_error", "connector response was invalid")
            await self._block_lineage(lineage, "connector response was invalid")

    async def _publish(self, job: _Job, event: TurnEvent) -> None:
        async with self._lock:
            if event.kind == "message.delta" and job.history and job.history[-1].kind == "message.delta":
                previous = job.history[-1]
                job.history[-1] = replace(event, text=(previous.text or "") + (event.text or ""))
            else:
                job.history.append(event)
            for follower in tuple(job.followers):
                self._offer(job, follower, event)

    async def _finish(self, job: _Job, terminal: BaseException | _Done) -> None:
        async with self._lock:
            job.terminal = terminal
            for follower in tuple(job.followers):
                self._offer(job, follower, terminal)

    async def _block_lineage(self, lineage: _Lineage, message: str) -> None:
        async with self._lock:
            lineage.blocked = True
            for pending_job in lineage.jobs.values():
                if pending_job.terminal is not None:
                    continue
                terminal = DeliveryUncertain(message)
                pending_job.terminal = terminal
                for follower in tuple(pending_job.followers):
                    self._offer(pending_job, follower, terminal)

    @staticmethod
    def _offer(job: _Job, follower: asyncio.Queue[Any], item: Any) -> None:
        try:
            follower.put_nowait(item)
        except asyncio.QueueFull:
            job.followers.discard(follower)
            while True:
                try:
                    follower.get_nowait()
                except asyncio.QueueEmpty:
                    break
            follower.put_nowait(OperationRejected("stream consumer is too slow"))

    async def _mark_uncertain(self, operation: Operation, code: str, message: str) -> Operation:
        return await self._operations.transition(operation.id, OperationState.DELIVERY_UNCERTAIN,
                                                 error_code=code, error_message=message)

    def _route(self, mapping: SessionMapping) -> ProfileRoute | None:
        matches = [route for route in self._connector.routes
                   if route.connection_id == mapping.connection_id and route.profile == mapping.profile]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _validate_operation(value: str, expected: UUID) -> None:
        if value != str(expected): raise ValueError("operation correlation mismatch")

    @staticmethod
    def _validate_event(frame: HermesEventFrame, operation: Operation, mapping: SessionMapping,
                        runtime_session_id: str | None) -> None:
        if frame.payload.operation_id != str(operation.id): raise ValueError("operation correlation mismatch")
        if frame.payload.connection_id != mapping.connection_id or frame.payload.profile != mapping.profile:
            raise ValueError("event route mismatch")
        if runtime_session_id is None or frame.payload.session_id != runtime_session_id:
            raise ValueError("event runtime session mismatch")

    @staticmethod
    def _text(data: dict[str, Any]) -> str:
        return data.get("text") if isinstance(data.get("text"), str) else ""

    @staticmethod
    def _error_text(data: dict[str, Any]) -> str:
        for key in ("message", "error", "text"):
            if isinstance(data.get(key), str) and data[key]: return data[key]
            if isinstance(data.get(key), dict):
                nested = data[key].get("message")
                if isinstance(nested, str) and nested: return nested
        return "Hermes turn failed"

    @staticmethod
    def _event(frame: HermesEventFrame, mapping: SessionMapping, operation_id: UUID,
               kind: str, text: str) -> TurnEvent:
        return TurnEvent(frame.id, mapping.lineage_key, frame.payload.seq or 0, kind, text=text,
                         operation_id=operation_id, stored_session_id=frame.payload.session_id,
                         occurred_at=frame.sent_at)

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            tasks = [lineage.worker for lineage in self._lineages.values() if lineage.worker]
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
