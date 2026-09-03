#!/usr/bin/env python3
"""Minimal readiness/replay connector; it never submits a Hermes prompt."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import websockets


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frame(kind: str, correlation_id: str, payload: dict) -> dict:
    return {
        "protocol": 1,
        "kind": kind,
        "id": str(uuid4()),
        "correlation_id": correlation_id,
        "sent_at": _now(),
        "payload": payload,
    }


async def _heartbeat(socket, epoch: str) -> None:
    while True:
        await asyncio.sleep(5)
        await socket.send(json.dumps(_frame("heartbeat", epoch, {"epoch": epoch})))


async def _serve(socket) -> None:
    async for raw in socket:
        command = json.loads(raw)
        operation_id = str((command.get("payload") or {}).get("operation_id") or "unknown")
        if command.get("kind") == "replay":
            await socket.send(
                json.dumps(
                    _frame(
                        "replay_complete",
                        command["correlation_id"],
                        {
                            "operation_id": operation_id,
                            "after_seq": command["payload"]["after_seq"],
                        },
                    )
                )
            )
        elif command.get("kind") == "submit":
            await socket.send(
                json.dumps(
                    _frame(
                        "command_error",
                        command.get("correlation_id") or operation_id,
                        {
                            "operation_id": operation_id,
                            "code": "fake_connector_no_submit",
                            "message": "fake connector does not submit prompts",
                            "acceptance_unknown": False,
                        },
                    )
                )
            )


async def _connect(url: str, secret: str) -> None:
    async with websockets.connect(url, max_size=1024 * 1024) as socket:
        challenge = json.loads(await socket.recv())
        timestamp = int(time.time())
        signed = f'{challenge["payload"]["nonce"]}\n{timestamp}'.encode()
        mac = base64.urlsafe_b64encode(
            hmac.new(secret.encode(), signed, hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        epoch = str(uuid4())
        hello = _frame(
            "hello",
            challenge["id"],
            {
                "timestamp": timestamp,
                "mac": mac,
                "connector_version": "fake-1.1.0",
                "capabilities": ["replay_complete"],
                "routes": [
                    {
                        "connection_id": "fake",
                        "profile": "default",
                        "target_profile": "default",
                    }
                ],
            },
        )
        hello["id"] = epoch
        await socket.send(json.dumps(hello))
        heartbeat = asyncio.create_task(_heartbeat(socket, epoch))
        try:
            await _serve(socket)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)


async def main() -> None:
    url = os.environ.get("HERMES_BRIDGE_CONNECTOR_URL", "ws://127.0.0.1:8787/connector")
    secret = os.environ.get("HERMES_BRIDGE_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("HERMES_BRIDGE_SECRET must contain at least 32 characters")
    while True:
        try:
            await _connect(url, secret)
        except (OSError, websockets.ConnectionClosed):
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
