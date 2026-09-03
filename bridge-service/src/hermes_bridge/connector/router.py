"""FastAPI WebSocket endpoint for the outbound Desktop connector."""

from __future__ import annotations

import asyncio
import base64
import secrets
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hermes_bridge.connector.hub import ConnectorHub
from hermes_bridge.connector.protocol import (
    AuthenticationError,
    Challenge,
    HeartbeatFrame,
    HelloFrame,
    ProtocolError,
    parse_incoming,
    utc_timestamp,
    verify_hello,
)
from hermes_bridge.config import Settings


def create_connector_router(settings: Settings, hub: ConnectorHub) -> APIRouter:
    router = APIRouter()

    @router.websocket("/connector")
    async def connector(socket: WebSocket) -> None:
        await socket.accept()
        nonce = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        challenge = Challenge(nonce)
        frame_id = str(uuid4())
        await socket.send_json(
            {
                "protocol": 1, "kind": "challenge", "id": frame_id,
                "correlation_id": frame_id, "sent_at": utc_timestamp(),
                "payload": {"nonce": nonce},
            }
        )
        try:
            raw = await socket.receive_text()
            frame = parse_incoming(raw)
            if not isinstance(frame, HelloFrame):
                raise AuthenticationError("hello required")
            verify_hello(frame, challenge, settings)
            await hub.connect(socket, frame)
            while True:
                raw = await asyncio.wait_for(
                    socket.receive_text(),
                    timeout=settings.connector_heartbeat_timeout_seconds,
                )
                parsed = parse_incoming(raw)
                if isinstance(parsed, HeartbeatFrame):
                    await hub.heartbeat(parsed, socket)
                elif isinstance(parsed, HelloFrame):
                    raise ProtocolError("hello already completed")
                else:
                    await hub.receive(parsed, socket)
        except AuthenticationError:
            await _safe_error(socket, "authentication_failed", "connector authentication failed")
            await socket.close(code=1008)
        except ProtocolError:
            await _safe_error(socket, "invalid_frame", "invalid connector frame")
            await socket.close(code=1003)
        except TimeoutError:
            await socket.close(code=1011, reason="connector heartbeat timed out")
        except WebSocketDisconnect:
            pass
        except Exception:
            # A replaced connector is closed by the new owner. Other unexpected
            # transport failures also fail closed without reflecting details.
            await socket.close(code=1011, reason="connector disconnected")
        finally:
            await hub.disconnect(socket)

    return router


async def _safe_error(socket: WebSocket, code: str, message: str) -> None:
    frame_id = str(uuid4())
    await socket.send_json(
        {
            "protocol": 1, "kind": "error", "id": frame_id,
            "correlation_id": frame_id, "sent_at": utc_timestamp(),
            "payload": {"code": code, "message": message},
        }
    )
