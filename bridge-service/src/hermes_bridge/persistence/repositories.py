"""SQLite-backed bridge repositories."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID, uuid4

import aiosqlite

from hermes_bridge.domain.models import (
    ApprovalState,
    InvalidTransition,
    Operation,
    OperationState,
    PendingApproval,
    SessionIdentity,
    SessionMapping,
    TurnEvent,
    TurnRequest,
    approval_transition_is_valid,
    transition_is_valid,
    utc_now,
)
from hermes_bridge.persistence.database import Database


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _read_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _approval(row: aiosqlite.Row) -> PendingApproval:
    return PendingApproval(
        id=row["approval_id"],
        connection_id=row["connection_id"],
        profile=row["profile"],
        target_profile=row["target_profile"],
        lineage_key=row["lineage_key"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        stored_session_id=row["stored_session_id"],
        runtime_session_id=row["runtime_session_id"],
        request_id=row["request_id"],
        command=row["command"],
        description=row["description"],
        choices=tuple(json.loads(row["choices"])),
        state=ApprovalState(row["state"]),
        resolved_choice=row["resolved_choice"],
        created_at=_read_timestamp(row["created_at"]),
        updated_at=_read_timestamp(row["updated_at"]),
    )


def _mapping(row: aiosqlite.Row) -> SessionMapping:
    return SessionMapping(
        connection_id=row["connection_id"],
        profile=row["profile"],
        lineage_root_id=row["lineage_root_id"],
        stored_session_id=row["stored_session_id"],
        title=row["title"],
        openwebui_chat_id=row["openwebui_chat_id"],
        last_hermes_message_id=row["last_hermes_message_id"],
        last_event_seq=row["last_event_seq"],
        last_snapshot_hash=row["last_snapshot_hash"],
        created_at=_read_timestamp(row["created_at"]),
        updated_at=_read_timestamp(row["updated_at"]),
        last_source_revision=row["last_source_revision"],
        last_event_epoch=row["last_event_epoch"],
    )


def _operation(row: aiosqlite.Row) -> Operation:
    return Operation(
        id=UUID(row["operation_id"]),
        chat_id=row["chat_id"],
        lineage_key=row["lineage_key"],
        user_message_id=row["user_message_id"],
        assistant_message_id=row["assistant_message_id"],
        text=row["text"],
        state=OperationState(row["state"]),
        result_text=row["result_text"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        last_event_seq=row["last_event_seq"],
        created_at=_read_timestamp(row["created_at"]),
        updated_at=_read_timestamp(row["updated_at"]),
        runtime_session_id=row["runtime_session_id"],
    )


class ApprovalRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_pending(self, approval: PendingApproval) -> PendingApproval:
        if approval.state is not ApprovalState.PENDING or approval.resolved_choice is not None:
            raise ValueError("upsert requires a pending approval without a resolved choice")
        async with self._database.write_transaction(immediate=True) as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM pending_approval
                WHERE approval_id = ? OR (
                    connection_id = ? AND profile = ? AND target_profile = ?
                    AND stored_session_id = ? AND request_id = ?
                )
                """,
                (approval.id, approval.connection_id, approval.profile,
                 approval.target_profile, approval.stored_session_id, approval.request_id),
            )
            rows = await cursor.fetchall()
            if rows:
                current = _approval(rows[0])
                identity_fields = (
                    "id", "connection_id", "profile", "target_profile", "lineage_key",
                    "stored_session_id", "request_id",
                )
                if len(rows) != 1 or any(
                    getattr(current, name) != getattr(approval, name)
                    for name in identity_fields
                ):
                    raise ValueError("approval identity conflicts with an existing record")
                if current.state is not ApprovalState.PENDING:
                    return current
                metadata = (
                    "chat_id", "message_id", "runtime_session_id", "command",
                    "description", "choices",
                )
                if all(getattr(current, name) == getattr(approval, name) for name in metadata):
                    return current
                await connection.execute(
                    """
                    UPDATE pending_approval
                    SET chat_id = ?, message_id = ?, runtime_session_id = ?, command = ?,
                        description = ?, choices = ?, updated_at = ?
                    WHERE approval_id = ?
                    """,
                    (approval.chat_id, approval.message_id, approval.runtime_session_id,
                     approval.command, approval.description, json.dumps(approval.choices),
                     _timestamp(utc_now()), approval.id),
                )
            else:
                await connection.execute(
                    """
                    INSERT INTO pending_approval (
                        approval_id, connection_id, profile, target_profile, lineage_key,
                        chat_id, message_id, stored_session_id, runtime_session_id,
                        request_id, command, description, choices, state, resolved_choice,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (approval.id, approval.connection_id, approval.profile,
                     approval.target_profile, approval.lineage_key, approval.chat_id,
                     approval.message_id, approval.stored_session_id,
                     approval.runtime_session_id, approval.request_id, approval.command,
                     approval.description, json.dumps(approval.choices), approval.state.value,
                     approval.resolved_choice, _timestamp(approval.created_at),
                     _timestamp(approval.updated_at)),
                )
            cursor = await connection.execute(
                "SELECT * FROM pending_approval WHERE approval_id = ?", (approval.id,)
            )
            row = await cursor.fetchone()
        assert row is not None
        return _approval(row)

    async def get(self, approval_id: str) -> PendingApproval | None:
        cursor = await self._database.connection.execute(
            "SELECT * FROM pending_approval WHERE approval_id = ?", (approval_id,)
        )
        row = await cursor.fetchone()
        return _approval(row) if row is not None else None

    async def by_route_request(
        self, connection_id: str, profile: str, target_profile: str,
        stored_session_id: str, request_id: str,
    ) -> PendingApproval | None:
        cursor = await self._database.connection.execute(
            """
            SELECT * FROM pending_approval
            WHERE connection_id = ? AND profile = ? AND target_profile = ?
                AND stored_session_id = ? AND request_id = ?
            """,
            (connection_id, profile, target_profile, stored_session_id, request_id),
        )
        row = await cursor.fetchone()
        return _approval(row) if row is not None else None

    async def list_for_route(
        self, connection_id: str, profile: str, target_profile: str,
    ) -> list[PendingApproval]:
        cursor = await self._database.connection.execute(
            """
            SELECT * FROM pending_approval
            WHERE connection_id = ? AND profile = ? AND target_profile = ?
            ORDER BY created_at, approval_id
            """,
            (connection_id, profile, target_profile),
        )
        return [_approval(row) for row in await cursor.fetchall()]

    async def list_reconcilable(self) -> list[PendingApproval]:
        cursor = await self._database.connection.execute(
            """
            SELECT * FROM pending_approval
            WHERE state IN ('pending', 'resolving', 'delivery_uncertain')
            ORDER BY created_at, approval_id
            """
        )
        return [_approval(row) for row in await cursor.fetchall()]

    async def transition(
        self, approval_id: str, expected: frozenset[ApprovalState], target: ApprovalState,
        *, resolved_choice: str | None = None,
    ) -> PendingApproval:
        async with self._database.write_transaction(immediate=True) as connection:
            cursor = await connection.execute(
                "SELECT * FROM pending_approval WHERE approval_id = ?", (approval_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise LookupError("approval does not exist")
            current = _approval(row)
            if current.state not in expected or not approval_transition_is_valid(current.state, target):
                raise InvalidTransition(f"cannot transition {current.state} to {target}")
            choice = resolved_choice if resolved_choice is not None else current.resolved_choice
            if choice is not None and choice not in current.choices:
                raise ValueError("resolution choice is not an advertised choice")
            if target is ApprovalState.RESOLVED and choice is None:
                raise ValueError("a resolved approval requires a known choice")
            if target in {
                ApprovalState.PENDING, ApprovalState.RESOLVED_EXTERNAL, ApprovalState.EXPIRED,
            }:
                choice = None
            cursor = await connection.execute(
                """
                UPDATE pending_approval
                SET state = ?, resolved_choice = ?, updated_at = ?
                WHERE approval_id = ? AND state = ?
                """,
                (target.value, choice, _timestamp(utc_now()), approval_id, current.state.value),
            )
            if cursor.rowcount != 1:
                raise InvalidTransition("approval state changed before transition")
            cursor = await connection.execute(
                "SELECT * FROM pending_approval WHERE approval_id = ?", (approval_id,)
            )
            row = await cursor.fetchone()
        assert row is not None
        return _approval(row)

    async def recover_in_flight(self) -> int:
        """Call once at service startup, before scans or resolution controls start."""
        async with self._database.write_transaction(immediate=True) as connection:
            cursor = await connection.execute(
                """
                UPDATE pending_approval SET state = ?, updated_at = ? WHERE state = ?
                """,
                (ApprovalState.DELIVERY_UNCERTAIN.value, _timestamp(utc_now()),
                 ApprovalState.RESOLVING.value),
            )
            return cursor.rowcount

    async def count_by_state(self) -> dict[str, int]:
        counts = {state.value: 0 for state in ApprovalState}
        cursor = await self._database.connection.execute(
            "SELECT state, COUNT(*) AS count FROM pending_approval GROUP BY state"
        )
        for row in await cursor.fetchall():
            counts[row["state"]] = row["count"]
        return counts


class MappingRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_session(self, session: SessionIdentity) -> SessionMapping:
        now = _timestamp(utc_now())
        async with self._database.write_transaction() as connection:
            await connection.execute(
                """
                INSERT INTO session_mapping (
                    connection_id, profile, lineage_root_id, stored_session_id, title,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id, profile, lineage_root_id) DO UPDATE SET
                    stored_session_id = excluded.stored_session_id,
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """,
                (
                    session.connection_id,
                    session.profile,
                    session.lineage_root_id,
                    session.stored_session_id,
                    session.title,
                    now,
                    now,
                ),
            )
            row = await self._fetch_mapping_by_lineage(connection, session.lineage_key)
        assert row is not None
        return _mapping(row)

    async def attach_chat(self, lineage_key: str, chat_id: str) -> SessionMapping:
        now = _timestamp(utc_now())
        async with self._database.write_transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE session_mapping
                SET openwebui_chat_id = ?, updated_at = ?
                WHERE connection_id || ':' || profile || ':' || lineage_root_id = ?
                """,
                (chat_id, now, lineage_key),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"no session mapping for lineage {lineage_key}")
            row = await self._fetch_mapping_by_lineage(connection, lineage_key)
        assert row is not None
        return _mapping(row)

    async def by_chat_id(self, chat_id: str) -> SessionMapping | None:
        cursor = await self._database.connection.execute(
            "SELECT * FROM session_mapping WHERE openwebui_chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        return _mapping(row) if row is not None else None

    async def by_lineage_key(self, lineage_key: str) -> SessionMapping | None:
        row = await self._fetch_mapping_by_lineage(self._database.connection, lineage_key)
        return _mapping(row) if row is not None else None

    async def by_route_and_stored_id(
        self, connection_id: str, profile: str, stored_session_id: str
    ) -> SessionMapping | None:
        cursor = await self._database.connection.execute(
            """
            SELECT * FROM session_mapping
            WHERE connection_id = ? AND profile = ? AND stored_session_id = ?
            """,
            (connection_id, profile, stored_session_id),
        )
        row = await cursor.fetchone()
        return _mapping(row) if row is not None else None

    async def list_all(self) -> list[SessionMapping]:
        cursor = await self._database.connection.execute(
            "SELECT * FROM session_mapping ORDER BY connection_id, profile, lineage_root_id"
        )
        return [_mapping(row) for row in await cursor.fetchall()]

    async def update_snapshot(
        self,
        lineage_key: str,
        *,
        last_hermes_message_id: str | None,
        snapshot_hash: str,
    ) -> SessionMapping:
        now = _timestamp(utc_now())
        async with self._database.write_transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE session_mapping
                SET last_hermes_message_id = ?, last_snapshot_hash = ?, updated_at = ?
                WHERE connection_id || ':' || profile || ':' || lineage_root_id = ?
                """,
                (last_hermes_message_id, snapshot_hash, now, lineage_key),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"no session mapping for lineage {lineage_key}")
            row = await self._fetch_mapping_by_lineage(connection, lineage_key)
        assert row is not None
        return _mapping(row)

    async def update_source_revision(
        self, lineage_key: str, source_revision: str
    ) -> SessionMapping:
        now = _timestamp(utc_now())
        async with self._database.write_transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE session_mapping
                SET last_source_revision = ?, updated_at = ?
                WHERE connection_id || ':' || profile || ':' || lineage_root_id = ?
                """,
                (source_revision, now, lineage_key),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"no session mapping for lineage {lineage_key}")
            row = await self._fetch_mapping_by_lineage(connection, lineage_key)
        assert row is not None
        return _mapping(row)

    @staticmethod
    async def _fetch_mapping_by_lineage(
        connection: aiosqlite.Connection, lineage_key: str
    ) -> aiosqlite.Row | None:
        cursor = await connection.execute(
            """
            SELECT * FROM session_mapping
            WHERE connection_id || ':' || profile || ':' || lineage_root_id = ?
            """,
            (lineage_key,),
        )
        return await cursor.fetchone()


class OperationRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_or_get(self, request: TurnRequest) -> tuple[Operation, bool]:
        async with self._database.write_transaction(immediate=True) as connection:
            cursor = await connection.execute(
                "SELECT * FROM operation WHERE chat_id = ? AND user_message_id = ?",
                (request.chat_id, request.user_message_id),
            )
            row = await cursor.fetchone()
            if row is not None:
                return _operation(row), False

            operation_id = uuid4()
            now = _timestamp(request.created_at)
            lineage_key = await self._lineage_for_chat(connection, request.chat_id)
            await connection.execute(
                """
                INSERT INTO operation (
                    operation_id, chat_id, lineage_key, user_message_id, assistant_message_id, text,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(operation_id),
                    request.chat_id,
                    lineage_key,
                    request.user_message_id,
                    request.assistant_message_id,
                    request.text,
                    OperationState.PENDING.value,
                    now,
                    now,
                ),
            )
            cursor = await connection.execute(
                "SELECT * FROM operation WHERE operation_id = ?", (str(operation_id),)
            )
            row = await cursor.fetchone()
        assert row is not None
        return _operation(row), True

    @staticmethod
    async def _lineage_for_chat(
        connection: aiosqlite.Connection, chat_id: str
    ) -> str | None:
        cursor = await connection.execute(
            """
            SELECT connection_id || ':' || profile || ':' || lineage_root_id AS lineage_key
            FROM session_mapping
            WHERE openwebui_chat_id = ?
            """,
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row["lineage_key"] if row is not None else None

    async def transition(
        self, operation_id: UUID, target: OperationState, **fields: object
    ) -> Operation:
        allowed_fields = {
            "result_text", "error_code", "error_message", "last_event_seq",
            "runtime_session_id",
        }
        unknown_fields = set(fields) - allowed_fields
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise TypeError(f"unsupported operation fields: {names}")

        async with self._database.write_transaction(immediate=True) as connection:
            cursor = await connection.execute(
                "SELECT * FROM operation WHERE operation_id = ?", (str(operation_id),)
            )
            row = await cursor.fetchone()
            if row is None:
                raise LookupError(f"operation {operation_id} does not exist")
            current = _operation(row)
            if not transition_is_valid(current.state, target):
                raise InvalidTransition(f"cannot transition {current.state} to {target}")

            lineage_key = current.lineage_key
            if target is OperationState.OFFERED:
                lineage_key = await self._lineage_for_chat(connection, current.chat_id)
                if lineage_key is None:
                    raise LookupError(f"no session mapping for chat {current.chat_id}")
                await connection.execute(
                    """
                    UPDATE operation
                    SET lineage_key = ?
                    WHERE chat_id = ? AND lineage_key IS NULL
                    """,
                    (lineage_key, current.chat_id),
                )
                cursor = await connection.execute(
                    """
                    SELECT operation_id FROM operation
                    WHERE lineage_key = ? AND state = ?
                    ORDER BY operation_order
                    LIMIT 1
                    """,
                    (lineage_key, OperationState.PENDING.value),
                )
                first_pending = await cursor.fetchone()
                if first_pending is None or first_pending["operation_id"] != str(operation_id):
                    raise InvalidTransition("only the first pending operation may be offered")
                cursor = await connection.execute(
                    """
                    SELECT operation_id FROM operation
                    WHERE lineage_key = ? AND operation_id != ?
                      AND state IN (?, ?, ?, ?)
                    LIMIT 1
                    """,
                    (
                        lineage_key,
                        str(operation_id),
                        OperationState.OFFERED.value,
                        OperationState.ACCEPTED.value,
                        OperationState.STREAMING.value,
                        OperationState.DELIVERY_UNCERTAIN.value,
                    ),
                )
                if await cursor.fetchone() is not None:
                    raise InvalidTransition("another operation is active for this lineage")

            next_values = {
                "result_text": current.result_text,
                "error_code": current.error_code,
                "error_message": current.error_message,
                "last_event_seq": current.last_event_seq,
                "runtime_session_id": current.runtime_session_id,
            }
            next_values.update(fields)
            now = _timestamp(utc_now())
            await connection.execute(
                """
                UPDATE operation
                SET state = ?, result_text = ?, error_code = ?, error_message = ?,
                    last_event_seq = ?, runtime_session_id = ?, lineage_key = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    target.value,
                    next_values["result_text"],
                    next_values["error_code"],
                    next_values["error_message"],
                    next_values["last_event_seq"],
                    next_values["runtime_session_id"],
                    lineage_key,
                    now,
                    str(operation_id),
                ),
            )
            cursor = await connection.execute(
                "SELECT * FROM operation WHERE operation_id = ?", (str(operation_id),)
            )
            row = await cursor.fetchone()
        assert row is not None
        return _operation(row)

    async def list_incomplete(self) -> list[Operation]:
        cursor = await self._database.connection.execute(
            """
            SELECT * FROM operation
            WHERE state IN (?, ?, ?, ?, ?)
            ORDER BY operation_order
            """,
            (
                OperationState.PENDING.value,
                OperationState.OFFERED.value,
                OperationState.ACCEPTED.value,
                OperationState.STREAMING.value,
                OperationState.DELIVERY_UNCERTAIN.value,
            ),
        )
        return [_operation(row) for row in await cursor.fetchall()]

    async def get(self, operation_id: UUID) -> Operation | None:
        cursor = await self._database.connection.execute(
            "SELECT * FROM operation WHERE operation_id = ?", (str(operation_id),)
        )
        row = await cursor.fetchone()
        return _operation(row) if row is not None else None

    async def count_by_state(self) -> dict[str, int]:
        counts = {state.value: 0 for state in OperationState}
        cursor = await self._database.connection.execute(
            "SELECT state, COUNT(*) AS count FROM operation GROUP BY state"
        )
        for row in await cursor.fetchall():
            counts[row["state"]] = row["count"]
        return counts

    async def list_pending(self, lineage_key: str) -> list[Operation]:
        cursor = await self._database.connection.execute(
            """
            SELECT operation.*
            FROM operation
            JOIN session_mapping ON session_mapping.openwebui_chat_id = operation.chat_id
            WHERE session_mapping.connection_id || ':' || session_mapping.profile || ':' ||
                  session_mapping.lineage_root_id = ?
              AND operation.state = ?
            ORDER BY operation.operation_order
            """,
            (lineage_key, OperationState.PENDING.value),
        )
        return [_operation(row) for row in await cursor.fetchall()]


class EventRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(self, event: TurnEvent) -> bool:
        async with self._database.write_transaction() as connection:
            mapping = await MappingRepository._fetch_mapping_by_lineage(
                connection, event.lineage_key
            )
            if mapping is None:
                raise LookupError(f"no session mapping for lineage {event.lineage_key}")
            if event.operation_id is not None:
                cursor = await connection.execute(
                    """
                    SELECT operation.lineage_key, session_mapping.connection_id || ':' ||
                           session_mapping.profile || ':' || session_mapping.lineage_root_id
                           AS mapped_lineage_key
                    FROM operation
                    JOIN session_mapping
                      ON session_mapping.openwebui_chat_id = operation.chat_id
                    WHERE operation.operation_id = ?
                    """,
                    (str(event.operation_id),),
                )
                operation = await cursor.fetchone()
                if operation is None:
                    raise LookupError(f"no mapped operation for event {event.event_id}")
                if operation["mapped_lineage_key"] != event.lineage_key:
                    raise ValueError("event operation belongs to a different lineage")
                if (
                    operation["lineage_key"] is not None
                    and operation["lineage_key"] != event.lineage_key
                ):
                    raise ValueError("operation lineage does not match its mapping")
            cursor = await connection.execute(
                """
                INSERT INTO turn_event (
                    event_id, lineage_key, sequence, kind, text, operation_id,
                    stored_session_id, connector_epoch, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    event.event_id,
                    event.lineage_key,
                    event.sequence,
                    event.kind,
                    event.text,
                    str(event.operation_id) if event.operation_id is not None else None,
                    event.stored_session_id,
                    event.connector_epoch,
                    _timestamp(event.occurred_at),
                ),
            )
            if cursor.rowcount != 1:
                return False

            now = _timestamp(utc_now())
            await connection.execute(
                """
                UPDATE session_mapping
                SET last_event_seq = CASE
                        WHEN ? IS NOT NULL AND last_event_epoch IS NOT ? THEN ?
                        WHEN last_event_seq IS NULL OR last_event_seq < ? THEN ?
                        ELSE last_event_seq
                    END,
                    last_event_epoch = CASE
                        WHEN ? IS NOT NULL THEN ?
                        ELSE last_event_epoch
                    END,
                    updated_at = ?
                WHERE connection_id || ':' || profile || ':' || lineage_root_id = ?
                """,
                (
                    event.connector_epoch,
                    event.connector_epoch,
                    event.sequence,
                    event.sequence,
                    event.sequence,
                    event.connector_epoch,
                    event.connector_epoch,
                    now,
                    event.lineage_key,
                ),
            )
            if event.operation_id is not None:
                await connection.execute(
                    """
                    UPDATE operation
                    SET last_event_seq = CASE
                            WHEN last_event_seq IS NULL OR last_event_seq < ? THEN ?
                            ELSE last_event_seq
                        END,
                        updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (event.sequence, event.sequence, now, str(event.operation_id)),
                )
        return True
