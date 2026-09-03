"""Read-only client for Hermes's authoritative session HTTP API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


_SESSIONS_PAGE_SIZE = 100
_MESSAGES_PAGE_SIZE = 500
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


class HermesReadError(RuntimeError):
    """Raised when Hermes's read API cannot provide a valid response."""


@dataclass(frozen=True)
class HermesSession:
    """A user-visible Hermes session, including its stable lineage identity."""

    id: str
    lineage_root_id: str
    title: str
    message_count: int | None = None
    last_active: int | float | str | None = None

    @property
    def revision(self) -> str | None:
        if self.message_count is None and self.last_active is None:
            return None
        return f"{self.message_count if self.message_count is not None else ''}:{self.last_active if self.last_active is not None else ''}"


@dataclass(frozen=True)
class HermesMessage:
    """A visible stored Hermes message in authoritative history order."""

    id: str
    role: str
    content: str
    created_at: int | float | str
    bridge_operation_id: str | None = None


class HermesReadClient:
    """Authenticated, paginated access to Hermes session and message snapshots."""

    def __init__(
        self,
        base_url: str,
        api_server_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_server_key}"},
            timeout=_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client when this client is no longer needed."""
        await self._client.aclose()

    async def iter_sessions(self, profile: str) -> AsyncIterator[HermesSession]:
        """Yield every non-archived session visible to a Hermes profile."""
        offset = 0
        while True:
            page = await self._read_page(
                "/api/sessions",
                {
                    "profile": profile,
                    "archived": "exclude",
                    "order": "recent",
                    "limit": _SESSIONS_PAGE_SIZE,
                    "offset": offset,
                },
                "sessions",
            )
            sessions = [_session_from_payload(item) for item in page]
            for session in sessions:
                yield session
            if len(page) < _SESSIONS_PAGE_SIZE:
                return
            offset += _SESSIONS_PAGE_SIZE

    async def read_messages(self, session_id: str, profile: str) -> list[HermesMessage]:
        """Return all visible session messages in Hermes's oldest-first order."""
        messages: list[HermesMessage] = []
        offset = 0
        encoded_session_id = quote(session_id, safe="")
        endpoint = f"/api/sessions/{encoded_session_id}/messages"
        while True:
            page = await self._read_page(
                endpoint,
                {
                    "profile": profile,
                    "include_compacted": "true",
                    "order": "oldest",
                    "limit": _MESSAGES_PAGE_SIZE,
                    "offset": offset,
                },
                "messages",
            )
            for item in page:
                message = _message_from_payload(item)
                if message is not None:
                    messages.append(message)
            if len(page) < _MESSAGES_PAGE_SIZE:
                return messages
            offset += _MESSAGES_PAGE_SIZE

    async def _read_page(
        self, endpoint: str, params: Mapping[str, str | int], response_key: str
    ) -> list[Mapping[str, Any]]:
        try:
            response = await self._client.get(endpoint, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise HermesReadError(
                f"Hermes API request failed with status {status} at {endpoint}"
            ) from error
        except httpx.RequestError as error:
            raise HermesReadError(f"Hermes API request failed at {endpoint}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise HermesReadError(f"invalid JSON response from {endpoint}") from error

        if isinstance(payload, list):
            page = payload
        elif isinstance(payload, Mapping) and response_key in payload:
            page = payload[response_key]
        elif isinstance(payload, Mapping) and "data" in payload:
            page = payload["data"]
        else:
            raise HermesReadError(f"invalid response shape from {endpoint}")

        if not isinstance(page, list) or not all(isinstance(item, Mapping) for item in page):
            raise HermesReadError(f"invalid response shape from {endpoint}")
        return page


def _session_from_payload(payload: Mapping[str, Any]) -> HermesSession:
    session_id = _required_string(payload, "id", "session")
    lineage_root_id = (
        payload.get("lineage_root_id")
        or payload.get("_lineage_root_id")
        or session_id
    )
    if not isinstance(lineage_root_id, str) or not lineage_root_id:
        raise HermesReadError("invalid session response shape: lineage_root_id must be a non-empty string")
    title = payload.get("title", "")
    if not isinstance(title, str):
        raise HermesReadError("invalid session response shape: title must be a string")
    message_count = payload.get("message_count")
    if isinstance(message_count, bool) or not isinstance(message_count, int):
        message_count = None
    last_active = payload.get("last_active")
    if isinstance(last_active, bool) or not isinstance(last_active, int | float | str):
        last_active = None
    return HermesSession(
        id=session_id,
        lineage_root_id=lineage_root_id,
        title=title,
        message_count=message_count,
        last_active=last_active,
    )


def _message_from_payload(payload: Mapping[str, Any]) -> HermesMessage | None:
    if payload.get("display_kind") == "hidden":
        return None
    message_id = _required_string(payload, "id", "message")
    role = _required_string(payload, "role", "message")
    content = payload.get("display_content", payload.get("content"))
    if not isinstance(content, str):
        raise HermesReadError("invalid message response shape: content must be a string")
    created_at = payload.get("created_at", payload.get("timestamp"))
    if isinstance(created_at, bool) or not isinstance(created_at, int | float | str):
        raise HermesReadError("invalid message response shape: created_at must be a timestamp")
    bridge_operation_id = payload.get("bridge_operation_id")
    if not isinstance(bridge_operation_id, str) or not bridge_operation_id:
        bridge_operation_id = None
    return HermesMessage(
        id=message_id,
        role=role,
        content=content,
        created_at=created_at,
        bridge_operation_id=bridge_operation_id,
    )


def _required_string(payload: Mapping[str, Any], field: str, resource: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise HermesReadError(
            f"invalid {resource} response shape: {field} must be a non-empty string"
        )
    return value
