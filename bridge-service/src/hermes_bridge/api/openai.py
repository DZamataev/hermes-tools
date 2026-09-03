"""Strict OpenAI-compatible surface consumed by OpenWebUI."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from hermes_bridge.domain.models import OperationState, TurnRequest
from hermes_bridge.services.queue import DeliveryUncertain, DesktopOffline, HermesTurnFailed, OperationRejected


SSE_HEARTBEAT_SECONDS = 10.0


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "invalid_request_error", "code": code}}, status_code=status)


def _authorized(header: str | None, secret: str) -> bool:
    token = ""
    if header and header.startswith("Bearer "):
        token = header[7:]
    return hmac.compare_digest(token.encode(), secret.encode())


def _text_request(body: object) -> tuple[str | None, str | None]:
    if not isinstance(body, dict): return None, "request body must be an object"
    if body.get("model") != "hermes-live": return None, "model must be hermes-live"
    if body.get("stream") is not True: return None, "stream must be true"
    if "tools" in body or "tool_choice" in body: return None, "tools are not supported"
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages: return None, "messages must be a non-empty array"
    for message in messages:
        if not isinstance(message, dict): return None, "every message must be an object"
        if message.get("role") in {"tool", "function"} or "tool_calls" in message or "function_call" in message:
            return None, "tool messages are not supported"
        content = message.get("content")
        if isinstance(content, list):
            if any(not isinstance(part, dict) or part.get("type") not in {"text", "input_text"}
                   or not isinstance(part.get("text"), str) for part in content):
                return None, "only text content is supported"
        elif content is not None and not isinstance(content, str):
            return None, "only text content is supported"
    newest = next((message for message in reversed(messages)
                   if isinstance(message, dict) and message.get("role") == "user"), None)
    if newest is None: return None, "a user message is required"
    content = newest.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in {"text", "input_text"} or not isinstance(part.get("text"), str):
                return None, "only text content is supported"
            parts.append(part["text"])
        text = "".join(parts).strip()
    else:
        return None, "only text content is supported"
    return (text, None) if text else (None, "the newest user message must be non-empty")


def _chunk(completion_id: str, created: int, delta: dict, finish_reason=None) -> str:
    payload = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
               "model": "hermes-live", "choices": [{"index": 0, "delta": delta,
                                                       "finish_reason": finish_reason}]}
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def _stream(events: AsyncIterator, completion_id: str) -> AsyncIterator[str]:
    created = int(time.time())
    yield _chunk(completion_id, created, {"role": "assistant"})
    emitted = False
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=SSE_HEARTBEAT_SECONDS)
            if not done:
                yield ": keep-alive\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            if event.kind == "message.delta" and event.text:
                emitted = True
                yield _chunk(completion_id, created, {"content": event.text})
            elif event.kind == "message.complete":
                if event.text and not emitted:
                    yield _chunk(completion_id, created, {"content": event.text})
                break
        yield _chunk(completion_id, created, {}, "stop")
        yield "data: [DONE]\n\n"
    except (DesktopOffline, OperationRejected, DeliveryUncertain, HermesTurnFailed) as error:
        code = "delivery_uncertain" if isinstance(error, DeliveryUncertain) else "hermes_turn_failed"
        yield f"data: {json.dumps({'error': {'message': str(error), 'type': 'hermes_error', 'code': code}}, separators=(',', ':'))}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


def create_openai_router(bridge_secret: str) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/models")
    async def models(request: Request):
        if not _authorized(request.headers.get("Authorization"), bridge_secret):
            return _error(401, "invalid_api_key", "invalid bearer token")
        return {"object": "list", "data": [{"id": "hermes-live", "object": "model", "owned_by": "hermes"}]}

    @router.post("/v1/chat/completions")
    async def completions(request: Request):
        if not _authorized(request.headers.get("Authorization"), bridge_secret):
            return _error(401, "invalid_api_key", "invalid bearer token")
        names = ("X-Hermes-Chat-Id", "X-Hermes-User-Message-Id", "X-Hermes-Assistant-Message-Id")
        values = [request.headers.get(name, "").strip() for name in names]
        if not all(values): return _error(400, "invalid_request", "all Hermes identity headers are required")
        try:
            body = await request.json()
        except Exception:
            return _error(400, "invalid_request", "request body must be JSON")
        text, problem = _text_request(body)
        if problem: return _error(400, "invalid_request", problem)
        mapping = await request.app.state.mapping_repository.by_chat_id(values[0])
        if mapping is None: return _error(409, "hermes_session_not_mapped", "OpenWebUI chat is not mapped to Hermes")
        operations = request.app.state.operation_repository
        operation, created = await operations.create_or_get(TurnRequest(values[0], values[1], values[2], text))
        if created and not request.app.state.connector_hub.connected:
            operation = await operations.transition(operation.id, OperationState.REJECTED, error_code="hermes_desktop_offline",
                                                    error_message="Hermes Desktop connector is offline")
            return _error(503, "hermes_desktop_offline", "Hermes Desktop connector is offline")
        events = request.app.state.lineage_queue.submit(mapping, operation)
        return StreamingResponse(_stream(events, f"chatcmpl-{uuid4().hex}"), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
