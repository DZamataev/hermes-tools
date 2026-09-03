"""Narrow authenticated client for supported OpenWebUI chat APIs."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from hermes_bridge.hermes.projection import ChatProjection


_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)
_CHAT_PAGE_SIZE = 60


class OpenWebUIError(RuntimeError):
    """A safe error from the owner-scoped OpenWebUI API."""


class OpenWebUIClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ensure_folder(self, name: str) -> str:
        folders = await self._request("GET", "/api/v1/folders/")
        if not isinstance(folders, list):
            raise OpenWebUIError("invalid response shape from /api/v1/folders/")
        for folder in folders:
            if (
                isinstance(folder, Mapping)
                and folder.get("name") == name
                and folder.get("parent_id") is None
            ):
                return _required_id(folder, "/api/v1/folders/")
        created = await self._request(
            "POST", "/api/v1/folders/", json={"name": name, "parent_id": None}
        )
        return _required_id(created, "/api/v1/folders/")

    async def iter_chats(self, include_folders: bool = True) -> AsyncIterator[dict[str, Any]]:
        page = 1
        while True:
            rows = await self._request(
                "GET",
                "/api/v1/chats/",
                params={"page": page, "include_folders": str(include_folders).lower()},
            )
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise OpenWebUIError("invalid response shape from /api/v1/chats/")
            for row in rows:
                _required_id(row, "/api/v1/chats/")
                yield row
            if len(rows) < _CHAT_PAGE_SIZE:
                return
            page += 1

    async def create_chat(self, projection: ChatProjection, folder_id: str, *, lineage_key: str = "") -> str:
        result = await self._request(
            "POST",
            "/api/v1/chats/new",
            json=_projection_payload(projection, folder_id, lineage_key),
        )
        return _required_id(result, "/api/v1/chats/new")

    async def read_chat(self, chat_id: str) -> dict[str, Any]:
        result = await self._request("GET", f"/api/v1/chats/{chat_id}")
        if not isinstance(result, dict) or result.get("id") != chat_id:
            raise OpenWebUIError(f"invalid response shape from /api/v1/chats/{chat_id}")
        return result

    async def update_chat(self, chat_id: str, payload: dict[str, Any]) -> None:
        result = await self._request("POST", f"/api/v1/chats/{chat_id}", json=payload)
        if not isinstance(result, Mapping) or result.get("id") != chat_id:
            raise OpenWebUIError(f"invalid response shape from /api/v1/chats/{chat_id}")

    async def emit_reload(self, chat_id: str, message_id: str) -> bool:
        result = await self._request(
            "POST",
            f"/api/v1/chats/{chat_id}/messages/{message_id}/event",
            json={"type": "chat:reload", "data": {}},
        )
        if result is not True and result is not False and result is not None:
            raise OpenWebUIError("invalid reload-event response shape")
        return result is True

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, endpoint, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise OpenWebUIError(
                f"OpenWebUI request failed with status {error.response.status_code} at {endpoint}"
            ) from error
        except httpx.RequestError as error:
            raise OpenWebUIError(f"OpenWebUI request failed at {endpoint}") from error
        try:
            return response.json()
        except ValueError as error:
            raise OpenWebUIError(f"invalid JSON response from {endpoint}") from error


def _projection_payload(
    projection: ChatProjection, folder_id: str, lineage_key: str
) -> dict[str, Any]:
    return {
        "chat": {
            "title": projection.title,
            "models": ["hermes-live"],
            "history": {
                "currentId": projection.current_id,
                "messages": projection.history,
            },
        },
        "variables": {"hermes_lineage_key": lineage_key},
        "folder_id": folder_id,
    }


def _required_id(value: object, endpoint: str) -> str:
    if not isinstance(value, Mapping):
        raise OpenWebUIError(f"invalid response shape from {endpoint}")
    item_id = value.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise OpenWebUIError(f"invalid response shape from {endpoint}")
    return item_id
