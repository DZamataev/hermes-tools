#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TEST_TMP="$(mktemp -d)"
TEST_PROJECT="hermes-bridge-test-${$}-${RANDOM}"
RUNTIME_IMAGE="${HERMES_BRIDGE_TEST_RUNTIME_IMAGE:-hermes-open-webui:local}"
COMPOSE_FILE="$TEST_TMP/compose.yaml"
HERMES_ENV_FILE="$TEST_TMP/hermes.env"
LOCAL_ENV_FILE="$TEST_TMP/local.env"
COMPOSE=(docker compose -p "$TEST_PROJECT" --env-file "$HERMES_ENV_FILE" --env-file "$LOCAL_ENV_FILE" -f "$COMPOSE_FILE")

fail() { print -u2 -- "FAIL: $*"; exit 1; }

cleanup() {
  "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$TEST_TMP"
}
trap cleanup EXIT INT TERM

command -v docker >/dev/null || fail "docker is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is not available"
docker image inspect "$RUNTIME_IMAGE" >/dev/null 2>&1 ||
  fail "runtime image $RUNTIME_IMAGE is missing; build the Hermes stack first"

TEST_API_KEY="fake-hermes-api-key"
TEST_OPENWEBUI_KEY="fake-openwebui-api-key"
TEST_BRIDGE_SECRET="fake-bridge-secret-with-at-least-32-characters"
print -r -- "API_SERVER_KEY=$TEST_API_KEY" > "$HERMES_ENV_FILE"
{
  print -r -- "OPENWEBUI_API_KEY=$TEST_OPENWEBUI_KEY"
  print -r -- "HERMES_BRIDGE_SECRET=$TEST_BRIDGE_SECRET"
} > "$LOCAL_ENV_FILE"
chmod 600 "$HERMES_ENV_FILE" "$LOCAL_ENV_FILE"

cat > "$TEST_TMP/fake_http.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/folders/"):
            self.respond([{"id": "folder-hermes", "name": "Hermes", "parent_id": None}])
        elif self.path.startswith("/api/sessions"):
            self.respond({"sessions": []})
        else:
            self.send_error(404)

    def respond(self, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
PY

cat > "$TEST_TMP/fake_connector.py" <<'PY'
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


def now():
    return datetime.now(timezone.utc).isoformat()


async def connect():
    secret = os.environ["HERMES_BRIDGE_SECRET"]
    while True:
        try:
            async with websockets.connect("ws://bridge-service:8787/connector") as socket:
                challenge = json.loads(await socket.recv())
                timestamp = int(time.time())
                message = f'{challenge["payload"]["nonce"]}\n{timestamp}'.encode()
                mac = base64.urlsafe_b64encode(
                    hmac.new(secret.encode(), message, hashlib.sha256).digest()
                ).rstrip(b"=").decode()
                frame_id = str(uuid4())
                await socket.send(json.dumps({
                    "protocol": 1,
                    "kind": "hello",
                    "id": frame_id,
                    "correlation_id": challenge["id"],
                    "sent_at": now(),
                    "payload": {
                        "timestamp": timestamp,
                        "mac": mac,
                        "connector_version": "stack-test-1.0",
                        "capabilities": ["replay_complete"],
                        "routes": [{
                            "connection_id": "stack-test",
                            "profile": "default",
                            "target_profile": "default",
                        }],
                    },
                }))
                while True:
                    await asyncio.sleep(5)
                    heartbeat_id = str(uuid4())
                    await socket.send(json.dumps({
                        "protocol": 1,
                        "kind": "heartbeat",
                        "id": heartbeat_id,
                        "correlation_id": heartbeat_id,
                        "sent_at": now(),
                        "payload": {"epoch": frame_id},
                    }))
        except Exception:
            await asyncio.sleep(0.5)


asyncio.run(connect())
PY

cat > "$COMPOSE_FILE" <<YAML
services:
  bridge-service:
    image: $RUNTIME_IMAGE
    command: ["python", "-m", "uvicorn", "hermes_bridge.app:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8787"]
    ports:
      - "127.0.0.1::8787"
    environment:
      API_SERVER_KEY: \${API_SERVER_KEY:?API_SERVER_KEY is required}
      OPENWEBUI_API_KEY: \${OPENWEBUI_API_KEY:?OPENWEBUI_API_KEY is required}
      HERMES_BRIDGE_SECRET: \${HERMES_BRIDGE_SECRET:?HERMES_BRIDGE_SECRET is required}
      HERMES_BASE_URL: http://fake-hermes:8080
      OPENWEBUI_BASE_URL: http://fake-openwebui:8080
      BRIDGE_DATA_DIR: /data
      BRIDGE_SYNC_INTERVAL_SECONDS: "1"
      PYTHONPATH: /bridge-src
    volumes:
      - bridge-data:/data
      - $ROOT/bridge-service/src:/bridge-src:ro
  fake-hermes:
    image: $RUNTIME_IMAGE
    command: ["python", "/fixtures/fake_http.py"]
    volumes:
      - $TEST_TMP:/fixtures:ro
  fake-openwebui:
    image: $RUNTIME_IMAGE
    command: ["python", "/fixtures/fake_http.py"]
    volumes:
      - $TEST_TMP:/fixtures:ro
  fake-connector:
    image: $RUNTIME_IMAGE
    command: ["python", "/fixtures/fake_connector.py"]
    environment:
      HERMES_BRIDGE_SECRET: \${HERMES_BRIDGE_SECRET:?HERMES_BRIDGE_SECRET is required}
    volumes:
      - $TEST_TMP:/fixtures:ro
    depends_on:
      - bridge-service
volumes:
  bridge-data:
YAML

"${COMPOSE[@]}" up -d >/dev/null || fail "isolated bridge stack did not start"

published="$("${COMPOSE[@]}" port bridge-service 8787 | tail -n 1)"
[[ "$published" == 127.0.0.1:* ]] || fail "bridge test port is not loopback-bound"
bridge_url="http://$published"

ready=0
for _attempt in {1..90}; do
  if curl --fail --silent --output /dev/null --max-time 2 "$bridge_url/health/ready"; then
    ready=1
    break
  fi
  sleep 1
done
[[ $ready -eq 1 ]] || fail "bridge did not become connector-ready"

models="$TEST_TMP/models.json"
curl --fail --silent --show-error --max-time 5 \
  -H "Authorization: Bearer $TEST_BRIDGE_SECRET" \
  "$bridge_url/v1/models" > "$models" || fail "authenticated /v1/models request failed"
grep -Fq '"id":"hermes-live"' "$models" ||
  grep -Eq '"id"[[:space:]]*:[[:space:]]*"hermes-live"' "$models" ||
  fail "/v1/models did not expose hermes-live"

[[ "$("${COMPOSE[@]}" ps --services --status running | sort)" == $'bridge-service\nfake-connector\nfake-hermes\nfake-openwebui' ]] ||
  fail "not all isolated test services are running"
