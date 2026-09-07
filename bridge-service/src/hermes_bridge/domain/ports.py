"""Persistence boundaries consumed by bridge services."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from hermes_bridge.domain.models import (
    ApprovalState,
    Operation,
    OperationState,
    PendingApproval,
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

    async def by_route_and_stored_id(
        self, connection_id: str, profile: str, stored_session_id: str
    ) -> SessionMapping | None: ...

    async def list_all(self) -> list[SessionMapping]: ...

    async def update_snapshot(
        self,
        lineage_key: str,
        *,
        last_hermes_message_id: str | None,
        snapshot_hash: str,
    ) -> SessionMapping: ...

    async def update_source_revision(
        self, lineage_key: str, source_revision: str
    ) -> SessionMapping: ...


class OperationStore(Protocol):
    async def create_or_get(self, request: TurnRequest) -> tuple[Operation, bool]: ...

    async def transition(
        self, operation_id: UUID, target: OperationState, **fields: object
    ) -> Operation: ...

    async def list_pending(self, lineage_key: str) -> list[Operation]: ...

    async def list_incomplete(self) -> list[Operation]: ...

    async def get(self, operation_id: UUID) -> Operation | None: ...

    async def count_by_state(self) -> dict[str, int]: ...


class EventStore(Protocol):
    async def record(self, event: TurnEvent) -> bool: ...


class ApprovalStore(Protocol):
    async def upsert_pending(self, approval: PendingApproval) -> PendingApproval: ...

    async def get(self, approval_id: str) -> PendingApproval | None: ...

    async def by_route_request(
        self, connection_id: str, profile: str, target_profile: str,
        stored_session_id: str, request_id: str,
    ) -> PendingApproval | None: ...

    async def list_for_route(
        self, connection_id: str, profile: str, target_profile: str,
    ) -> list[PendingApproval]: ...

    async def list_reconcilable(self) -> list[PendingApproval]: ...

    async def transition(
        self, approval_id: str, expected: frozenset[ApprovalState], target: ApprovalState,
        *, resolved_choice: str | None = None,
    ) -> PendingApproval: ...

    async def recover_in_flight(self) -> int: ...

    async def count_by_state(self) -> dict[str, int]: ...
