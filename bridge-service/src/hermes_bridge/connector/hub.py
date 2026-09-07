"""Ownership and bounded correlated dispatch for one Desktop connector."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from hermes_bridge.connector.protocol import (
    ApprovalResolvedFrame,
    ApprovalSnapshotFrame,
    CommandErrorFrame,
    ConnectorCommand,
    ConnectorEvent,
    HeartbeatFrame,
    HelloFrame,
    HermesEventFrame,
    ReplayCompleteFrame,
    ReplayGapFrame,
)


class ConnectorOffline(RuntimeError):
    pass


class ConnectorDisconnected(RuntimeError):
    pass


class ConnectorHub:
    def __init__(self, heartbeat_timeout_seconds: float, *, queue_size: int = 256) -> None:
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._queue_size = queue_size
        self._socket: Any | None = None
        self._routes = ()
        self._capabilities: frozenset[str] = frozenset()
        self._epoch: str | None = None
        self._version: str | None = None
        self._last_heartbeat = 0.0
        self._pending: dict[str, asyncio.Queue[ConnectorEvent | BaseException]] = {}
        self._lock = asyncio.Lock()
        self._on_connect: Callable[[HelloFrame], None] | None = None
        self._on_event: Callable[[ConnectorEvent], None] | None = None

    def set_observer(
        self,
        *,
        on_connect: Callable[[HelloFrame], None] | None,
        on_event: Callable[[ConnectorEvent], None] | None,
    ) -> None:
        """Set bounded service callbacks; observers must return immediately."""
        self._on_connect = on_connect
        self._on_event = on_event

    @property
    def connected(self) -> bool:
        return self._socket is not None and time.monotonic() - self._last_heartbeat <= self._heartbeat_timeout

    @property
    def routes(self):
        return self._routes

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def epoch(self) -> str | None:
        return self._epoch

    @property
    def connector_version(self) -> str | None:
        return self._version

    async def connect(self, socket: Any, hello: HelloFrame) -> None:
        old = None
        replaced_pending = []
        async with self._lock:
            old = self._socket
            if old is not None and old is not socket:
                replaced_pending = list(self._pending.values())
                self._pending.clear()
            self._socket = socket
            self._routes = tuple(hello.payload.routes)
            self._capabilities = frozenset(hello.payload.capabilities)
            self._epoch = hello.id
            self._version = hello.payload.connector_version
            self._last_heartbeat = time.monotonic()
        if old is not None and old is not socket:
            for queue in replaced_pending:
                _put_or_replace(
                    queue,
                    ConnectorDisconnected("Desktop connector was replaced"),
                )
            await old.close(code=1012, reason="replaced by newer connector")
        if self._on_connect is not None:
            self._on_connect(hello)

    async def heartbeat(self, frame: HeartbeatFrame, socket: Any | None = None) -> None:
        if (socket is not None and socket is not self._socket) or frame.payload.epoch != self._epoch:
            raise ConnectorDisconnected("heartbeat epoch mismatch")
        self._last_heartbeat = time.monotonic()

    async def disconnect(self, socket: Any) -> None:
        async with self._lock:
            if socket is not self._socket:
                return
            self._socket = None
            self._routes = ()
            self._capabilities = frozenset()
            pending = list(self._pending.values())
            self._pending.clear()
        for queue in pending:
            _put_or_replace(queue, ConnectorDisconnected("Desktop connector disconnected"))

    async def dispatch(self, command: ConnectorCommand) -> AsyncIterator[ConnectorEvent]:
        if not self.connected or self._socket is None:
            raise ConnectorOffline("Hermes Desktop connector is offline")
        correlation_id = command.correlation_id
        queue: asyncio.Queue[ConnectorEvent | BaseException] = asyncio.Queue(self._queue_size)
        if correlation_id in self._pending:
            raise ValueError(f"duplicate correlation id {correlation_id}")
        self._pending[correlation_id] = queue
        try:
            await self._socket.send_json(command.model_dump(mode="json"))
            while True:
                item = await queue.get()
                if isinstance(item, BaseException):
                    raise item
                yield item
                if _terminal(item):
                    return
        finally:
            self._pending.pop(correlation_id, None)

    async def receive(self, event: ConnectorEvent, socket: Any | None = None) -> None:
        if socket is not None and socket is not self._socket:
            raise ConnectorDisconnected("event came from a replaced connector")
        queue = self._pending.get(event.correlation_id)
        if queue is not None:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                socket = self._socket
                if socket is not None:
                    await socket.close(code=1009, reason="connector event queue overflow")
                    await self.disconnect(socket)
                return
        if self._on_event is not None:
            self._on_event(event)


def _terminal(event: ConnectorEvent) -> bool:
    return (
        isinstance(
            event,
            (ApprovalResolvedFrame, ApprovalSnapshotFrame, CommandErrorFrame, ReplayGapFrame, ReplayCompleteFrame),
        )
        or isinstance(event, HermesEventFrame)
        and event.payload.event_type in {"message.complete", "error"}
    )


def _put_or_replace(queue: asyncio.Queue, item: BaseException) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        queue.put_nowait(item)
