"""Bridge-owned SQLite database lifecycle and schema initialization."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


class Database:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._write_lock = asyncio.Lock()
        self._is_open = True

    @property
    def connection(self) -> aiosqlite.Connection:
        return self._connection

    @classmethod
    async def open(cls, path: Path) -> "Database":
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        database = cls(connection)
        await database._initialize()
        return database

    async def _initialize(self) -> None:
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._connection.execute("PRAGMA busy_timeout=5000")
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS session_mapping (
                connection_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                lineage_root_id TEXT NOT NULL,
                stored_session_id TEXT NOT NULL,
                title TEXT NOT NULL,
                openwebui_chat_id TEXT UNIQUE,
                last_hermes_message_id TEXT,
                last_event_seq INTEGER,
                last_snapshot_hash TEXT,
                last_source_revision TEXT,
                last_event_epoch TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(connection_id, profile, lineage_root_id)
            );

            CREATE TABLE IF NOT EXISTS operation (
                operation_order INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                chat_id TEXT NOT NULL,
                lineage_key TEXT,
                user_message_id TEXT NOT NULL,
                assistant_message_id TEXT NOT NULL,
                text TEXT NOT NULL,
                state TEXT NOT NULL,
                result_text TEXT,
                error_code TEXT,
                error_message TEXT,
                last_event_seq INTEGER,
                runtime_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(chat_id, user_message_id)
            );

            CREATE TABLE IF NOT EXISTS turn_event (
                event_id TEXT PRIMARY KEY,
                lineage_key TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                text TEXT,
                operation_id TEXT,
                stored_session_id TEXT,
                connector_epoch TEXT,
                occurred_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_approval (
                approval_id TEXT PRIMARY KEY,
                connection_id TEXT NOT NULL,
                profile TEXT NOT NULL,
                target_profile TEXT NOT NULL,
                lineage_key TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                stored_session_id TEXT NOT NULL,
                runtime_session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                command TEXT NOT NULL,
                description TEXT NOT NULL,
                choices TEXT NOT NULL,
                state TEXT NOT NULL,
                resolved_choice TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(connection_id, profile, target_profile, stored_session_id, request_id)
            );

            CREATE INDEX IF NOT EXISTS pending_approval_route_state
            ON pending_approval(connection_id, profile, target_profile, state);

            CREATE INDEX IF NOT EXISTS pending_approval_lineage_state
            ON pending_approval(lineage_key, state);
            """
        )
        cursor = await self._connection.execute("PRAGMA table_info(operation)")
        operation_columns = {row["name"] for row in await cursor.fetchall()}
        cursor = await self._connection.execute("PRAGMA table_info(session_mapping)")
        mapping_columns = {row["name"] for row in await cursor.fetchall()}
        await self._connection.execute("BEGIN IMMEDIATE")
        try:
            if "lineage_key" not in operation_columns:
                await self._connection.execute("ALTER TABLE operation ADD COLUMN lineage_key TEXT")
            if "runtime_session_id" not in operation_columns:
                await self._connection.execute(
                    "ALTER TABLE operation ADD COLUMN runtime_session_id TEXT"
                )
            if "last_source_revision" not in mapping_columns:
                await self._connection.execute(
                    "ALTER TABLE session_mapping ADD COLUMN last_source_revision TEXT"
                )
            if "last_event_epoch" not in mapping_columns:
                await self._connection.execute(
                    "ALTER TABLE session_mapping ADD COLUMN last_event_epoch TEXT"
                )
            cursor = await self._connection.execute("PRAGMA table_info(turn_event)")
            event_columns = {row["name"] for row in await cursor.fetchall()}
            if "connector_epoch" not in event_columns:
                await self._connection.execute(
                    "ALTER TABLE turn_event ADD COLUMN connector_epoch TEXT"
                )
            await self._connection.execute(
                """
                UPDATE operation
                SET lineage_key = (
                    SELECT connection_id || ':' || profile || ':' || lineage_root_id
                    FROM session_mapping
                    WHERE session_mapping.openwebui_chat_id = operation.chat_id
                )
                WHERE lineage_key IS NULL
                  AND EXISTS (
                    SELECT 1 FROM session_mapping
                    WHERE session_mapping.openwebui_chat_id = operation.chat_id
                )
                """
            )
            await self._connection.execute("DROP INDEX IF EXISTS operation_one_active_per_lineage")
            await self._connection.execute(
                """
                UPDATE operation AS later
                SET state = ?, updated_at = ?
                WHERE later.lineage_key IS NOT NULL
                  AND later.state IN ('offered', 'accepted', 'streaming')
                  AND EXISTS (
                    SELECT 1 FROM operation AS earlier
                    WHERE earlier.lineage_key = later.lineage_key
                      AND earlier.state IN ('offered', 'accepted', 'streaming')
                      AND earlier.operation_order < later.operation_order
                  )
                """,
                ("delivery_uncertain", datetime.now(timezone.utc).isoformat()),
            )
            await self._connection.execute(
                """
                CREATE UNIQUE INDEX operation_one_active_per_lineage
                ON operation(lineage_key)
                WHERE lineage_key IS NOT NULL
                  AND state IN ('offered', 'accepted', 'streaming')
                """
            )
        except BaseException:
            await self._connection.rollback()
            raise
        else:
            await self._connection.commit()
        await self._connection.execute(
            "INSERT OR IGNORE INTO schema_version(version) VALUES (?)", (1,)
        )
        await self._connection.commit()

    @asynccontextmanager
    async def write_transaction(self, *, immediate: bool = False) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            await self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._connection
            except BaseException:
                await self._connection.rollback()
                raise
            else:
                await self._connection.commit()

    async def close(self) -> None:
        await self._connection.close()
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open
