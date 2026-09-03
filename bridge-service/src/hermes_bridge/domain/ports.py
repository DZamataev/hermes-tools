"""Persistence boundaries consumed by bridge services."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from hermes_bridge.domain.models import (
    Operation,
    OperationState,
    SessionIdentity,
    SessionMapping,
    TurnEvent,
    TurnRequest,
)


class MappingStore(Protocol):
    async def upsert_session(self, session: SessionIdentity) -> SessionMapping: ...

    async def attach_chat(self, lineage_key: str, chat_id: str) -> SessionMapping: ...

    async def by_chat_id(self, chat_id: str) -> SessionMapping | None: ...

    async def by_lineage_key(self, lineage_key: str) -> SessionMapping | None: ...

    async def update_snapshot(
        self,
        lineage_key: str,
        *,
        last_hermes_message_id: str | None,
        snapshot_hash: str,
    ) -> SessionMapping: ...


class OperationStore(Protocol):
    async def create_or_get(self, request: TurnRequest) -> tuple[Operation, bool]: ...

    async def transition(
        self, operation_id: UUID, target: OperationState, **fields: object
    ) -> Operation: ...


class EventStore(Protocol):
    async def record(self, event: TurnEvent) -> bool: ...
