# Hermes–OpenWebUI Live Session Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically mirror every normal Hermes Desktop session into the first administrator's native OpenWebUI chat list and continue the same Hermes lineage through an update-safe Desktop plugin.

**Architecture:** A Python bridge service runs beside OpenWebUI in Docker Compose. It reads persisted Hermes history through the authenticated Hermes HTTP API, projects it through supported OpenWebUI chat APIs, and exposes an OpenAI-compatible streaming endpoint. An external Hermes Desktop plugin connects outbound to the bridge and uses the Desktop-owned gateway for queued submit, live events, and replay; bridge SQLite state provides mapping, ordering, and at-most-once recovery.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic v2, HTTPX, aiosqlite, pytest, Node.js 22 ESM, esbuild, Docker Compose, Hermes Desktop plugin SDK, OpenWebUI HTTP API.

**Spec:** `docs/superpowers/specs/2026-09-03-hermes-openwebui-bridge-design.md`

## Global Constraints

- Hermes Desktop is required for live submit; bridge must not start an independent Hermes runtime.
- Never open a raw second WebSocket to the ephemeral Hermes gateway or read either application's SQLite database directly.
- Hermes is authoritative for lineage, stored history, titles, and completed turn content.
- OpenWebUI chats belong to the API-key owner, which is the first administrator for this single-user MVP.
- New OpenWebUI chats must not create Hermes sessions in this MVP.
- Input is text-only; reject unsupported multimodal/tool-call input explicitly.
- Persist an operation before dispatch and serialize operations per Hermes lineage.
- Never blindly retry a submit whose acceptance is ambiguous.
- Bind the Desktop connector to `127.0.0.1` and keep all long-lived credentials out of Git and logs.
- Preserve the existing Compose project name `hermes`, OpenWebUI volume `hermes_open-webui`, and shutdown behavior `docker compose stop`.
- Do not patch the installed Hermes application. Install only through `~/.hermes/desktop-plugins/openwebui-bridge/plugin.js`.
- Do not add another OpenWebUI source patch in the MVP; use its supported API key, chat, folder, event, and connection configuration.

## Planned File Structure

```text
bridge-service/
├── Dockerfile
├── pyproject.toml
├── uv.lock
├── src/hermes_bridge/
│   ├── __init__.py
│   ├── app.py                    # composition root and lifespan
│   ├── config.py                 # validated environment settings
│   ├── api/
│   │   ├── health.py             # liveness/readiness/status
│   │   └── openai.py             # /v1/models and /v1/chat/completions
│   ├── connector/
│   │   ├── protocol.py           # versioned frames and HMAC challenge
│   │   ├── hub.py                # one authenticated Desktop connection
│   │   └── router.py             # FastAPI WebSocket endpoint
│   ├── domain/
│   │   ├── models.py             # sessions, mappings, operations, events
│   │   └── ports.py              # Protocol interfaces used by services
│   ├── hermes/
│   │   ├── client.py             # read-only persisted session API
│   │   ├── events.py             # live event normalization
│   │   └── projection.py         # deterministic OpenWebUI message graph
│   ├── openwebui/
│   │   ├── client.py             # supported chat/folder/event API
│   │   └── mirror.py             # idempotent native-chat projection
│   ├── persistence/
│   │   ├── database.py           # schema initialization and transactions
│   │   └── repositories.py       # mappings, operations, event watermarks
│   └── services/
│       ├── queue.py              # one FIFO worker per lineage
│       └── sync.py               # discovery, reconciliation, recovery
└── tests/                         # mirrors src responsibilities

hermes-plugin/
├── package.json
├── package-lock.json
├── scripts/
│   ├── build.mjs
│   └── install.sh
├── src/
│   ├── connector-core.js         # testable protocol and command handling
│   └── plugin.js                 # Hermes plugin registration
└── tests/
    └── connector-core.test.mjs

runner/
└── bootstrap-local-env.sh

tests/
├── contract/
│   └── test_openwebui_live.py
├── fakes/
│   └── fake_desktop_connector.py
└── test_bridge_stack.sh
```

---

### Task 1: Bridge Foundation and Local Configuration

**Files:**
- Create: `.env.example`
- Create: `bridge-service/pyproject.toml`
- Create: `bridge-service/Dockerfile`
- Create: `bridge-service/src/hermes_bridge/__init__.py`
- Create: `bridge-service/src/hermes_bridge/config.py`
- Create: `bridge-service/src/hermes_bridge/app.py`
- Create: `bridge-service/src/hermes_bridge/api/health.py`
- Create: `bridge-service/tests/test_config.py`
- Create: `bridge-service/tests/test_health.py`
- Create: `bridge-service/tests/conftest.py`
- Create: `runner/bootstrap-local-env.sh`
- Modify: `.gitignore`
- Modify: `Makefile`

**Interfaces:**
- Produces: `Settings.from_env(env: Mapping[str, str] | None = None) -> Settings`
- Produces: `create_app(settings: Settings) -> FastAPI`
- Produces: `GET /health/live -> {"status": "ok"}`
- Produces: `runner/bootstrap-local-env.sh`, which creates or repairs `.env.local` without sourcing it as shell code.

- [ ] **Step 1: Write failing settings and health tests**

```python
# bridge-service/tests/test_config.py
import pytest
from hermes_bridge.config import Settings


def test_settings_require_all_three_credentials(tmp_path):
    with pytest.raises(ValueError, match="OPENWEBUI_API_KEY"):
        Settings.from_env({
            "API_SERVER_KEY": "hermes-key",
            "HERMES_BRIDGE_SECRET": "x" * 32,
            "BRIDGE_DATA_DIR": str(tmp_path),
        })


def test_settings_accept_explicit_local_configuration(tmp_path):
    settings = Settings.from_env({
        "API_SERVER_KEY": "hermes-key",
        "OPENWEBUI_API_KEY": "sk-openwebui",
        "HERMES_BRIDGE_SECRET": "x" * 32,
        "BRIDGE_DATA_DIR": str(tmp_path),
    })
    assert settings.hermes_base_url == "http://host.docker.internal:8642"
    assert settings.openwebui_base_url == "http://open-webui:8080"
    assert settings.database_path == tmp_path / "bridge.sqlite3"
```

```python
# bridge-service/tests/test_health.py
from fastapi.testclient import TestClient
from hermes_bridge.app import create_app


def test_liveness_does_not_expose_configuration(settings):
    response = TestClient(create_app(settings)).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert settings.openwebui_api_key not in response.text
```

- [ ] **Step 2: Run the tests and verify the imports fail**

Run:

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/test_config.py tests/test_health.py -q
```

Expected: FAIL because `hermes_bridge.config` and `hermes_bridge.app` do not exist.

- [ ] **Step 3: Add the package manifest and minimal configuration implementation**

Use this dependency boundary in `bridge-service/pyproject.toml`:

```toml
[project]
name = "hermes-openwebui-bridge"
version = "0.1.0"
requires-python = ">=3.12,<3.15"
dependencies = [
  "aiosqlite>=0.21,<1",
  "fastapi>=0.116,<1",
  "httpx>=0.28,<1",
  "pydantic>=2.11,<3",
  "uvicorn[standard]>=0.35,<1",
]

[project.optional-dependencies]
test = ["pytest>=8.4,<9", "pytest-asyncio>=1.1,<2"]

[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/hermes_bridge"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
```

Implement an immutable `Settings` dataclass. Trim values, reject missing keys, require at least 32 characters for `HERMES_BRIDGE_SECRET`, parse `BRIDGE_HOST_PORT` as `1..65535`, and default `BRIDGE_DATA_DIR` to `/data`, log level to `info`, sync interval to 30 seconds, and connector heartbeat timeout to 20 seconds.

```python
# bridge-service/src/hermes_bridge/config.py
@dataclass(frozen=True)
class Settings:
    api_server_key: str
    openwebui_api_key: str
    bridge_secret: str
    data_dir: Path
    hermes_base_url: str = "http://host.docker.internal:8642"
    openwebui_base_url: str = "http://open-webui:8080"
    bridge_host_port: int = 8787
    log_level: str = "info"
    sync_interval_seconds: float = 30.0
    connector_heartbeat_timeout_seconds: float = 20.0

    @property
    def database_path(self) -> Path:
        return self.data_dir / "bridge.sqlite3"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings": ...
```

The method body must use the supplied mapping or `os.environ`, never `source` an env file, and name the missing variable in every validation error.

Define the `settings` fixture in `tests/conftest.py` with non-secret test-only values and `tmp_path`; no test reads the developer's real environment.

- [ ] **Step 4: Add the FastAPI composition root, liveness route, and production image**

`create_app` must include the health router without reading global settings. Use a Python 3.12 slim runtime, install the locked project, create `/data`, run as a non-root UID, and start `uvicorn hermes_bridge.app:create_app_from_env --factory --host 0.0.0.0 --port 8787`.

- [ ] **Step 5: Add safe `.env.local` bootstrap**

Track only:

```dotenv
# .env.example
OPENWEBUI_API_KEY=
HERMES_BRIDGE_SECRET=
BRIDGE_HOST_PORT=8787
BRIDGE_LOG_LEVEL=info
```

`runner/bootstrap-local-env.sh` must:

1. create `.env.local` with mode `0600` if absent;
2. preserve any existing `OPENWEBUI_API_KEY` byte-for-byte;
3. generate `HERMES_BRIDGE_SECRET` with `openssl rand -hex 32` only when empty;
4. add default non-secret variables when absent;
5. never print any value.

Add `make local-env` and call it from `make bootstrap`. Extend the repository layout test to assert `.env.local` is ignored and `.env.example` has empty credentials.

- [ ] **Step 6: Lock dependencies and run foundation tests**

Run:

```bash
cd bridge-service
uv lock --python 3.12
uv run --python 3.12 --extra test pytest tests/test_config.py tests/test_health.py -q
cd ..
make test
```

Expected: all commands exit 0; `.env.local` exists with mode `600`; no secret value appears in output.

- [ ] **Step 7: Commit the foundation**

```bash
git add .env.example .gitignore Makefile runner/bootstrap-local-env.sh \
  tests/test_repository_layout.sh bridge-service
git commit -m "feat: scaffold Hermes session bridge service"
```

---

### Task 2: Durable Mapping, Operation, and Event State

**Files:**
- Create: `bridge-service/src/hermes_bridge/domain/models.py`
- Create: `bridge-service/src/hermes_bridge/domain/ports.py`
- Create: `bridge-service/src/hermes_bridge/persistence/database.py`
- Create: `bridge-service/src/hermes_bridge/persistence/repositories.py`
- Create: `bridge-service/tests/persistence/test_repositories.py`

**Interfaces:**
- Produces: `Database.open(path: Path) -> Database`, `Database.close() -> None`
- Produces: `MappingRepository.upsert_session(session: SessionIdentity) -> SessionMapping`
- Produces: `MappingRepository.attach_chat(lineage_key: str, chat_id: str) -> SessionMapping`
- Produces: `MappingRepository.by_chat_id(chat_id: str) -> SessionMapping | None`
- Produces: `OperationRepository.create_or_get(request: TurnRequest) -> tuple[Operation, bool]`
- Produces: `OperationRepository.transition(operation_id: UUID, target: OperationState, **fields) -> Operation`
- Produces: `EventRepository.record(event: TurnEvent) -> bool`, returning `False` for a duplicate event ID.

- [ ] **Step 1: Write failing persistence tests**

```python
async def test_mapping_survives_reopen_and_rotates_tip(tmp_path):
    database = await Database.open(tmp_path / "bridge.db")
    mappings = MappingRepository(database)
    first = await mappings.upsert_session(SessionIdentity(
        connection_id="local", profile="default",
        lineage_root_id="root-1", stored_session_id="tip-1", title="Chat",
    ))
    await mappings.attach_chat(first.lineage_key, "ow-chat-1")
    await database.close()

    database = await Database.open(tmp_path / "bridge.db")
    mappings = MappingRepository(database)
    await mappings.upsert_session(SessionIdentity(
        connection_id="local", profile="default",
        lineage_root_id="root-1", stored_session_id="tip-2", title="Chat",
    ))
    restored = await mappings.by_chat_id("ow-chat-1")
    assert restored is not None
    assert restored.stored_session_id == "tip-2"
```

```python
async def test_operation_key_is_idempotent_and_transition_is_monotonic(repositories):
    request = TurnRequest(
        chat_id="chat-1", user_message_id="user-1",
        assistant_message_id="assistant-1", text="hello",
    )
    first, created = await repositories.operations.create_or_get(request)
    duplicate, created_again = await repositories.operations.create_or_get(request)
    assert created is True
    assert created_again is False
    assert duplicate.id == first.id
    await repositories.operations.transition(first.id, OperationState.ACCEPTED)
    with pytest.raises(InvalidTransition):
        await repositories.operations.transition(first.id, OperationState.PENDING)
```

- [ ] **Step 2: Run tests and verify missing domain types fail**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/persistence/test_repositories.py -q`

Expected: FAIL on imports from `domain.models` and `persistence.database`.

- [ ] **Step 3: Define immutable domain models and valid state transitions**

Define:

```python
class OperationState(StrEnum):
    PENDING = "pending"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    STREAMING = "streaming"
    COMPLETED = "completed"
    DELIVERY_UNCERTAIN = "delivery_uncertain"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SessionIdentity:
    connection_id: str
    profile: str
    lineage_root_id: str
    stored_session_id: str
    title: str

    @property
    def lineage_key(self) -> str:
        return f"{self.connection_id}:{self.profile}:{self.lineage_root_id}"
```

`TurnRequest`, `SessionMapping`, `Operation`, and `TurnEvent` must use explicit fields and UTC timestamps. Permit only these transitions:

```text
pending -> offered | rejected
offered -> accepted | delivery_uncertain | rejected
accepted -> streaming | completed | delivery_uncertain
streaming -> completed | delivery_uncertain
delivery_uncertain -> accepted | completed | rejected
```

- [ ] **Step 4: Implement schema initialization and repositories**

Create tables `schema_version`, `session_mapping`, `operation`, and `turn_event`. Required uniqueness constraints:

```sql
UNIQUE(connection_id, profile, lineage_root_id)
UNIQUE(openwebui_chat_id)
UNIQUE(chat_id, user_message_id)
UNIQUE(event_id)
```

Use `BEGIN IMMEDIATE` for create-or-get and state transitions. Enable `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, and `PRAGMA busy_timeout=5000`. Never interpolate identifiers or values into SQL strings.

- [ ] **Step 5: Add crash-recovery and duplicate-event tests**

Verify that `offered`, `accepted`, and `streaming` operations are returned by `list_incomplete()`, that `pending` operations preserve FIFO order per lineage, and that inserting the same event twice returns `True` then `False` without changing the watermark.

- [ ] **Step 6: Run the persistence suite and commit**

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/persistence -q
cd ..
git add bridge-service/src/hermes_bridge/domain bridge-service/src/hermes_bridge/persistence \
  bridge-service/tests/persistence
git commit -m "feat: persist bridge mappings and turn operations"
```

---

### Task 3: Hermes Read API and Deterministic Transcript Projection

**Files:**
- Create: `bridge-service/src/hermes_bridge/hermes/client.py`
- Create: `bridge-service/src/hermes_bridge/hermes/projection.py`
- Create: `bridge-service/tests/hermes/test_client.py`
- Create: `bridge-service/tests/hermes/test_projection.py`

**Interfaces:**
- Produces: `HermesReadClient.iter_sessions(profile: str) -> AsyncIterator[HermesSession]`
- Produces: `HermesReadClient.read_messages(session_id: str, profile: str) -> list[HermesMessage]`
- Produces: `project_history(identity: SessionIdentity, messages: Sequence[HermesMessage]) -> ChatProjection`
- Consumes: domain `SessionIdentity` from Task 2.

- [ ] **Step 1: Write failing pagination and projection tests**

```python
async def test_read_messages_pages_oldest_first_without_loss():
    requests = []
    transport = httpx.MockTransport(hermes_messages_handler(total=501, requests=requests))
    client = HermesReadClient("http://hermes", "secret", transport=transport)
    messages = await client.read_messages("session-1", "default")
    assert len(messages) == 501
    assert [message.content for message in messages[:2]] == ["message 0", "message 1"]
    assert [int(request.url.params["offset"]) for request in requests] == [0, 500]
```

```python
def test_projection_is_stable_and_linear():
    projection = project_history(SESSION, [
        HermesMessage(id="11", role="user", content="hello", created_at=100),
        HermesMessage(id="12", role="assistant", content="hi", created_at=101),
    ])
    user_id, assistant_id = projection.ordered_message_ids
    assert projection.history[assistant_id]["parentId"] == user_id
    assert projection.history[user_id]["childrenIds"] == [assistant_id]
    assert project_history(SESSION, projection.source_messages) == projection
```

- [ ] **Step 2: Run tests and verify client/projection imports fail**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/hermes -q`

Expected: FAIL because the Hermes client and projection modules do not exist.

- [ ] **Step 3: Implement the authenticated read client**

For sessions, request pages of 100 from:

```text
GET /api/sessions?profile={profile}&archived=exclude&order=recent&limit=100&offset={offset}
```

For history, request pages of 500 from:

```text
GET /api/sessions/{id}/messages?profile={profile}&include_compacted=true&order=oldest&limit=500&offset={offset}
```

Send `Authorization: Bearer <API_SERVER_KEY>`, set connect/read timeouts, call `raise_for_status()`, validate response shape, and stop when `returned < limit`. Use `display_content` when present, skip messages whose `display_kind` is `hidden`, retain the physical Hermes message ID, and support roles `user`, `assistant`, `system`, and `tool` without coercing unknown roles to user.

- [ ] **Step 4: Implement deterministic OpenWebUI projection**

Use UUIDv5 with a constant namespace committed in source:

```python
OPENWEBUI_MESSAGE_NAMESPACE = UUID("a65797e2-48df-58c4-9022-bc8e556249c2")


def openwebui_message_id(identity: SessionIdentity, hermes_message_id: str) -> str:
    key = f"{identity.connection_id}:{identity.profile}:{identity.lineage_root_id}:{hermes_message_id}"
    return str(uuid5(OPENWEBUI_MESSAGE_NAMESPACE, key))
```

Build a linear OpenWebUI `history.messages` graph with stable `parentId`, `childrenIds`, role, content, and timestamp. Record bridge-owned projected IDs separately so later reconciliation can distinguish them from unmatched local nodes.

- [ ] **Step 5: Cover malformed and compacted responses**

Add tests for 401, 404, invalid JSON shape, a hidden compaction carrier, `display_content`, empty sessions, and a session whose durable tip differs from its lineage root. HTTP errors must include status and endpoint but never the bearer value.

Define `hermes_messages_handler` and all fixture constants in the test module or `tests/conftest.py`; production classes must not expose test-only request logs.

- [ ] **Step 6: Run tests and commit**

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/hermes -q
cd ..
git add bridge-service/src/hermes_bridge/hermes bridge-service/tests/hermes
git commit -m "feat: read and project Hermes session history"
```

---

### Task 4: OpenWebUI Native Chat Client and Mirror Reconciler

**Files:**
- Create: `bridge-service/src/hermes_bridge/openwebui/client.py`
- Create: `bridge-service/src/hermes_bridge/openwebui/mirror.py`
- Create: `bridge-service/tests/openwebui/test_client.py`
- Create: `bridge-service/tests/openwebui/test_mirror.py`

**Interfaces:**
- Produces: `OpenWebUIClient.ensure_folder(name: str) -> str`
- Produces: `OpenWebUIClient.iter_chats(include_folders: bool = True) -> AsyncIterator[dict[str, Any]]`
- Produces: `OpenWebUIClient.create_chat(projection: ChatProjection, folder_id: str) -> str`
- Produces: `OpenWebUIClient.read_chat(chat_id: str) -> dict[str, Any]`
- Produces: `OpenWebUIClient.update_chat(chat_id: str, payload: dict[str, Any]) -> None`
- Produces: `OpenWebUIClient.emit_reload(chat_id: str, message_id: str) -> bool`
- Produces: `MirrorService.reconcile(identity: SessionIdentity, messages: Sequence[HermesMessage]) -> MirrorResult`
- Consumes: mapping repository from Task 2 and projection from Task 3.

- [ ] **Step 1: Write failing supported-API tests**

```python
async def test_client_creates_chat_as_api_key_owner(mock_transport):
    client = OpenWebUIClient("http://open-webui", "sk-owner", transport=mock_transport)
    chat_id = await client.create_chat(PROJECTION, folder_id="folder-hermes")
    assert chat_id == "ow-chat-1"
    request = mock_transport.requests[0]
    assert request.url.path == "/api/v1/chats/new"
    assert request.headers["authorization"] == "Bearer sk-owner"
    payload = json.loads(request.content)
    assert payload["folder_id"] == "folder-hermes"
    assert payload["chat"]["models"] == ["hermes-live"]
```

```python
async def test_reconcile_existing_chat_is_idempotent(mirror, owui_fake):
    first = await mirror.reconcile(SESSION, MESSAGES)
    second = await mirror.reconcile(SESSION, MESSAGES)
    assert first.chat_id == second.chat_id
    assert owui_fake.created_chat_count == 1
    assert owui_fake.reload_events[-1]["type"] == "chat:reload"
```

- [ ] **Step 2: Run tests and verify missing client/mirror fail**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/openwebui -q`

Expected: FAIL on imports from `openwebui.client` and `openwebui.mirror`.

- [ ] **Step 3: Implement the narrow OpenWebUI client**

Use only:

```text
GET  /api/v1/folders/
POST /api/v1/folders/
GET  /api/v1/chats/?page={page}&include_folders=true
POST /api/v1/chats/new
GET  /api/v1/chats/{id}
POST /api/v1/chats/{id}
POST /api/v1/chats/{id}/messages/{message_id}/event
```

Every call sends the owner API key, validates response shape, and uses bounded timeouts. `iter_chats` advances one-based pages until a page contains fewer than 60 rows. `ensure_folder("Hermes")` reuses an existing root folder with the exact name before creating one. `emit_reload` sends `{"type":"chat:reload","data":{}}` only after the durable chat update succeeds.

- [ ] **Step 4: Implement crash-safe mirror creation**

Before calling `/chats/new`, persist a mapping row with a null OpenWebUI ID. After success, attach the returned ID transactionally. When an incomplete mapping is recovered after a crash, scan chats in the `Hermes` folder and inspect their `variables.hermes_lineage_key` marker before creating anything. This closes the non-idempotent `/new` response-loss window.

Create payloads with:

```python
{
    "chat": {
        "title": projection.title,
        "models": ["hermes-live"],
        "history": {
            "currentId": projection.current_id,
            "messages": projection.history,
        },
    },
    "variables": {"hermes_lineage_key": identity.lineage_key},
    "folder_id": folder_id,
}
```

- [ ] **Step 5: Implement safe update semantics**

Overwrite title and all deterministic bridge-owned message nodes from the latest Hermes projection. Preserve unmatched OpenWebUI-only nodes and report them as drift; never delete user data automatically. Update mapping watermarks only after OpenWebUI confirms the write. Emit `chat:reload` using the current projected message ID, or the stable sentinel `bridge-sync` for an empty transcript, so an open browser reloads canonical history.

- [ ] **Step 6: Cover owner/auth and response-loss failures**

Add tests for invalid API key, duplicate `Hermes` folder, a lost `/new` response followed by recovery, a local renamed title, an edited bridge-owned message, an unknown OpenWebUI-only node, and reload-event failure after a successful persistent update. A reload failure is warning/retry state, not rollback of the persisted mirror.

- [ ] **Step 7: Run tests and commit**

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/openwebui -q
cd ..
git add bridge-service/src/hermes_bridge/openwebui bridge-service/tests/openwebui
git commit -m "feat: mirror Hermes sessions into native OpenWebUI chats"
```

---

### Task 5: Authenticated Desktop Connector Protocol

**Files:**
- Create: `bridge-service/src/hermes_bridge/connector/protocol.py`
- Create: `bridge-service/src/hermes_bridge/connector/hub.py`
- Create: `bridge-service/src/hermes_bridge/connector/router.py`
- Create: `bridge-service/tests/connector/test_protocol.py`
- Create: `bridge-service/tests/connector/test_websocket.py`
- Modify: `bridge-service/src/hermes_bridge/app.py`

**Interfaces:**
- Produces: `sign_challenge(secret: str, nonce: str, timestamp: int) -> str`
- Produces: `verify_hello(frame: HelloFrame, challenge: Challenge, settings: Settings) -> None`
- Produces: `ConnectorHub.connected -> bool`
- Produces: `ConnectorHub.routes -> tuple[ProfileRoute, ...]`
- Produces: `ConnectorHub.dispatch(command: ConnectorCommand) -> AsyncIterator[ConnectorEvent]`
- Produces: `WS /connector`, protocol major version 1.

- [ ] **Step 1: Write failing challenge and WebSocket tests**

```python
def test_challenge_signature_is_deterministic_and_url_safe():
    signature = sign_challenge("s" * 32, "nonce-1", 1_788_400_000)
    assert signature == sign_challenge("s" * 32, "nonce-1", 1_788_400_000)
    assert "+" not in signature and "/" not in signature
```

```python
def test_connector_rejects_wrong_secret(app):
    with TestClient(app) as client:
        with client.websocket_connect("/connector") as socket:
            challenge = socket.receive_json()
            socket.send_json(hello_for(challenge, secret="wrong" * 8))
            error = socket.receive_json()
            assert error["kind"] == "error"
            assert error["payload"]["code"] == "authentication_failed"
```

- [ ] **Step 2: Run tests and verify connector imports fail**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/connector -q`

Expected: FAIL because connector modules do not exist.

- [ ] **Step 3: Define versioned Pydantic frame models**

All frames contain `protocol`, `kind`, `id`, `correlation_id`, `sent_at`, and typed `payload`. Define these payloads:

```text
challenge {nonce}
hello {timestamp, mac, connector_version, routes[]}
heartbeat {epoch}
submit {operation_id, route, stored_session_id, text, queued:true}
replay {operation_id, route, runtime_session_id, after_seq}
replay_gap {operation_id, after_seq, oldest_available}
accepted {operation_id, runtime_session_id}
hermes_event {operation_id?, connection_id, profile, session_id, seq?, event_type, data}
command_error {operation_id, code, message, acceptance_unknown}
```

Reject unknown command kinds, payloads above 1 MiB, protocol majors other than 1, empty identifiers, `queued:false`, and arbitrary RPC method names.

- [ ] **Step 4: Implement HMAC challenge-response and connection ownership**

Server sends a 32-byte random nonce. Hello MAC is base64url-without-padding HMAC-SHA256 over `nonce + "\n" + timestamp`. Accept timestamps within 30 seconds and consume each nonce once. Logs include connector epoch and version, never nonce, MAC, or secret.

`ConnectorHub` permits one authenticated Desktop connector. A valid new connection atomically replaces and closes the old one. Heartbeat timeout marks the hub offline and fails pending dispatch streams with a typed disconnect error.

- [ ] **Step 5: Implement correlated dispatch**

Assign one bridge command UUID and route received frames by `correlation_id` into a bounded `asyncio.Queue`. End an iterator on terminal event or `command_error`; enforce one maximum queue size and close a connector that exceeds it rather than growing memory without bound.

- [ ] **Step 6: Cover reconnect and redaction**

Add tests for expired/replayed challenge, incompatible protocol, second connector replacement, heartbeat timeout, response correlation, oversized frame, disconnect during dispatch, and absence of secret/MAC in captured logs.

- [ ] **Step 7: Run tests and commit**

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/connector -q
cd ..
git add bridge-service/src/hermes_bridge/connector bridge-service/src/hermes_bridge/app.py \
  bridge-service/tests/connector
git commit -m "feat: add authenticated Hermes Desktop connector"
```

---

### Task 6: Update-safe Hermes Desktop Plugin

**Files:**
- Create: `hermes-plugin/package.json`
- Create: `hermes-plugin/package-lock.json`
- Create: `hermes-plugin/scripts/build.mjs`
- Create: `hermes-plugin/scripts/install.sh`
- Create: `hermes-plugin/src/connector-core.js`
- Create: `hermes-plugin/src/plugin.js`
- Create: `hermes-plugin/tests/connector-core.test.mjs`
- Modify: `hermes-plugin/README.md`
- Modify: `.gitignore`
- Modify: `Makefile`
- Modify: `tests/test_repository_layout.sh`

**Interfaces:**
- Consumes: connector protocol v1 from Task 5.
- Produces: default Hermes plugin export `{id: "openwebui-bridge", register(ctx)}`.
- Produces: `createConnector({host, WebSocketImpl, cryptoImpl, url, secret, timers}) -> {start, stop}`.
- Produces: `make install-plugin`, installing mode-`0600` `~/.hermes/desktop-plugins/openwebui-bridge/plugin.js`.

- [ ] **Step 1: Write failing Node tests around a fake Hermes host**

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'
import { createConnector } from '../src/connector-core.js'

test('submit resumes and queues on the exact routed session', async () => {
  const host = fakeHost()
  const socket = fakeSocket()
  const connector = createConnector(dependencies({ host, socket }))
  await connector.handleCommand({
    kind: 'submit',
    payload: {
      operation_id: 'op-1', route: ROUTE,
      stored_session_id: 'stored-1', text: 'hello', queued: true
    }
  })
  assert.deepEqual(host.calls[0], ['session.resume', {
    session_id: 'stored-1', source: 'desktop', omit_messages: true
  }])
  assert.deepEqual(host.calls[1], ['prompt.submit', {
    session_id: 'runtime-1', text: 'hello', queued: true
  }])
})
```

- [ ] **Step 2: Run tests and verify the connector core is missing**

Run: `cd hermes-plugin && node --test tests/connector-core.test.mjs`

Expected: FAIL because `src/connector-core.js` does not exist.

- [ ] **Step 3: Implement connection, challenge signing, and reconnect**

Use browser Web Crypto for HMAC-SHA256. On open, wait for `challenge`, send `hello` with `await host.profileRoutes()`, then send heartbeats every 5 seconds. Reconnect with capped delays `250 ms, 500 ms, 1 s, 2 s, 5 s`; reset after successful hello. `stop()` clears timers, disposes `host.onEvent("*", ...)`, closes the socket, and prevents reconnect.

- [ ] **Step 4: Implement allowlisted submit and replay commands**

For submit:

1. match the exact `(connectionId, profile, targetProfile)` route;
2. acquire `await host.retainProfile(route)`;
3. call `host.requestProfile(route, "session.resume", {session_id, source:"desktop", omit_messages:true}, 60_000)`;
4. call `host.requestProfile(route, "prompt.submit", {session_id: runtimeId, text, queued:true}, 60_000)`;
5. send `accepted` only after `prompt.submit` acknowledges, including the runtime session ID;
6. forward matching `host.onEvent("*", event)` frames, including sequence and `session.info` stored tip;
7. release the retained route on terminal event or command error.

For replay, call only `session.events.since` with the provided runtime ID and sequence. No bridge frame may choose an arbitrary Hermes RPC name.

- [ ] **Step 5: Bundle a single runtime plugin and install without leaking the secret into Git**

`package.json` uses esbuild as a pinned dev dependency and keeps `@hermes/plugin-sdk` external. `scripts/build.mjs` requires `HERMES_BRIDGE_SECRET`, defines it into the bundle, and refuses an output containing the placeholder. `scripts/install.sh` parses the exact `HERMES_BRIDGE_SECRET=` and `BRIDGE_HOST_PORT=` lines from `.env.local` without sourcing the file, builds into a temporary directory, installs to the standalone Desktop plugin path, and applies mode `0600`.

Ignore `hermes-plugin/dist/` and assert no tracked file contains the actual `.env.local` secret.

- [ ] **Step 6: Add lifecycle and failure tests**

Cover valid challenge response, wrong route, resume failure, prompt rejection with `acceptance_unknown:false`, disconnect after prompt offer with `acceptance_unknown:true`, event filtering, release-on-terminal, duplicate terminal events, replay request, and reconnect timer disposal.

- [ ] **Step 7: Run plugin tests, build, install, and verify hot-load inventory**

```bash
cd hermes-plugin
npm ci
npm test
cd ..
make install-plugin
test -f /Users/frenzy/.hermes/desktop-plugins/openwebui-bridge/plugin.js
test "$(stat -f '%Lp' /Users/frenzy/.hermes/desktop-plugins/openwebui-bridge/plugin.js)" = 600
```

In Hermes Desktop run **Reload desktop plugins** only if the filesystem watcher has not loaded the new folder within five seconds, then verify the plugin inventory reports `openwebui-bridge` as loaded.

- [ ] **Step 8: Commit the Desktop plugin**

```bash
git add hermes-plugin .gitignore Makefile tests/test_repository_layout.sh
git commit -m "feat: connect Hermes Desktop sessions to bridge"
```

---

### Task 7: Per-lineage Queue and OpenAI-compatible Streaming

**Files:**
- Create: `bridge-service/src/hermes_bridge/services/queue.py`
- Create: `bridge-service/src/hermes_bridge/api/openai.py`
- Create: `bridge-service/tests/services/test_queue.py`
- Create: `bridge-service/tests/api/test_openai.py`
- Modify: `bridge-service/src/hermes_bridge/app.py`

**Interfaces:**
- Produces: `LineageQueue.submit(mapping: SessionMapping, operation: Operation) -> AsyncIterator[TurnEvent]`
- Produces: `GET /v1/models`, returning model `hermes-live`.
- Produces: `POST /v1/chat/completions`, requiring three OpenWebUI identity headers.
- Consumes: repositories from Task 2 and `ConnectorHub.dispatch` from Task 5.

- [ ] **Step 1: Write failing queue ordering and duplicate-request tests**

```python
async def test_same_lineage_is_fifo_but_different_lineages_overlap(queue, connector):
    a1 = asyncio.create_task(collect(queue.submit(MAPPING_A, operation("a1"))))
    a2 = asyncio.create_task(collect(queue.submit(MAPPING_A, operation("a2"))))
    b1 = asyncio.create_task(collect(queue.submit(MAPPING_B, operation("b1"))))
    await connector.wait_until_started("a1")
    await connector.wait_until_started("b1")
    assert not connector.started("a2")
    connector.complete("a1")
    await connector.wait_until_started("a2")
    await finish(a1, a2, b1)
```

```python
def test_duplicate_completion_request_dispatches_once(client, connector, headers):
    first = client.post("/v1/chat/completions", headers=headers, json=text_request("hello"))
    second = client.post("/v1/chat/completions", headers=headers, json=text_request("hello"))
    assert first.status_code == second.status_code == 200
    assert connector.submit_count == 1
```

- [ ] **Step 2: Run tests and verify queue/OpenAI modules fail to import**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/services/test_queue.py tests/api/test_openai.py -q`

Expected: FAIL because queue and OpenAI routers do not exist.

- [ ] **Step 3: Implement the per-lineage worker**

Create one bounded `asyncio.Queue` and worker task per lineage key. Persist `pending` before enqueue. The worker transitions `pending -> offered`, dispatches to the exact mapped route/session, translates connector acknowledgements into operation states, stores the terminal result, then advances FIFO. Tear down an idle lineage worker after 60 seconds only when its queue is empty.

When the connector is offline before offer, reject with a typed offline error. When it disconnects after offer without definitive rejection, transition to `delivery_uncertain` and stop automatic processing for that lineage until Task 8 reconciles it.

- [ ] **Step 4: Implement strict OpenAI request parsing**

Require:

```text
X-Hermes-Chat-Id
X-Hermes-User-Message-Id
X-Hermes-Assistant-Message-Id
```

Require `model == "hermes-live"`, `stream == true`, exactly one newest non-empty text user message, and no image/file content parts, `tools`, or `tool_choice`. Resolve mapping by chat ID before creating an operation. Unmapped chats return HTTP 409 with code `hermes_session_not_mapped`; offline Desktop returns 503 with code `hermes_desktop_offline`.

Authenticate both OpenAI endpoints with `Authorization: Bearer <HERMES_BRIDGE_SECRET>` using constant-time comparison. A missing or wrong bearer returns 401 and is never logged. Add tests that `/v1/models` and `/v1/chat/completions` reject the OpenWebUI owner key and Hermes API key as well as an empty bearer.

- [ ] **Step 5: Implement OpenAI SSE translation**

Emit a role chunk, assistant text delta chunks, a terminal chunk with `finish_reason:"stop"`, then `data: [DONE]`. Send an SSE comment heartbeat every 10 seconds while queued or waiting. Do not expose raw Hermes event payloads. Duplicate requests follow the existing operation; completed duplicates replay the stored final text without dispatching.

- [ ] **Step 6: Cover errors and cancellation**

Add tests for missing headers, unmapped/new chat, non-stream request, multimodal input, tools, connector offline, queued heartbeat, Hermes error, client disconnect, duplicate completed operation, and an uncertain operation. A browser disconnect must not cancel an already accepted Hermes turn; its completion is recovered by reconciliation.

- [ ] **Step 7: Run tests and commit**

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/services/test_queue.py tests/api/test_openai.py -q
cd ..
git add bridge-service/src/hermes_bridge/services/queue.py \
  bridge-service/src/hermes_bridge/api/openai.py bridge-service/src/hermes_bridge/app.py \
  bridge-service/tests/services/test_queue.py bridge-service/tests/api/test_openai.py
git commit -m "feat: stream queued Hermes turns through OpenAI API"
```

---

### Task 8: Live Event Ingest, Full Synchronization, and Recovery

**Files:**
- Create: `bridge-service/src/hermes_bridge/hermes/events.py`
- Create: `bridge-service/src/hermes_bridge/services/sync.py`
- Create: `bridge-service/tests/hermes/test_events.py`
- Create: `bridge-service/tests/services/test_sync.py`
- Modify: `bridge-service/src/hermes_bridge/app.py`
- Modify: `bridge-service/src/hermes_bridge/api/health.py`

**Interfaces:**
- Produces: `normalize_event(frame: ConnectorEvent) -> TurnEvent | None`
- Produces: `SyncService.full_scan() -> SyncReport`
- Produces: `SyncService.reconcile_lineage(lineage_key: str) -> MirrorResult`
- Produces: `SyncService.recover_incomplete_operations() -> RecoveryReport`
- Produces: `GET /health/ready` and `GET /health/status`.
- Consumes: Hermes client, mirror, repositories, connector hub, and queue from Tasks 2–7.

- [ ] **Step 1: Write failing event and recovery tests**

```python
def test_session_info_rotates_stored_tip():
    event = normalize_event(connector_event(
        "session.info", seq=44,
        data={"session_id":"runtime-1", "stored_session_id":"tip-2"},
    ))
    assert event.kind is EventKind.SESSION_INFO
    assert event.stored_session_id == "tip-2"
```

```python
async def test_reconnect_replays_then_falls_back_to_snapshot(sync, connector, hermes):
    connector.replay_result = ReplayGap(after_seq=511, oldest_available=700)
    await sync.recover_incomplete_operations()
    assert hermes.snapshot_requests == [("tip-1", "default")]
    assert sync.operations["op-1"].state is OperationState.COMPLETED
```

- [ ] **Step 2: Run tests and verify event/sync modules fail to import**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/hermes/test_events.py tests/services/test_sync.py -q`

Expected: FAIL because event normalization and sync service do not exist.

- [ ] **Step 3: Normalize the Hermes event allowlist**

Accept and validate `message.start`, `message.delta`, `reasoning.delta`, `tool.start`, `tool.progress`, `tool.complete`, `message.complete`, `session.info`, and `error`. Deduplicate by stable source event ID when present, otherwise by connector epoch/session/sequence. Text deltas feed SSE; reasoning and tool events update status only; terminal events trigger a stored-history snapshot.

- [ ] **Step 4: Implement initial and periodic scan**

At application startup, wait for Hermes HTTP and OpenWebUI credentials independently, then scan every configured 30 seconds. Derive a unique `(connection_id, profile, target_profile)` set from `ConnectorHub.routes`; call `iter_sessions(target_profile)` for each route. For every session returned by the normal non-archived Hermes inventory:

1. upsert lineage/tip metadata;
2. compare stored watermark/hash;
3. fetch paginated history only when new or changed;
4. call `MirrorService.reconcile`;
5. record per-lineage success or error without aborting the scan.

Trigger the same reconciliation immediately after a terminal live event and after connector reconnect.

- [ ] **Step 5: Implement ambiguous-delivery recovery**

For every incomplete operation, request replay after its stored sequence. If replay proves acceptance/completion, advance state and reconcile. If replay reports a gap, read the authoritative stored history and match only a persisted bridge operation identity supplied by Hermes; text/time similarity alone is not proof. When no proof exists, keep `delivery_uncertain` and expose it in status without resubmitting.

- [ ] **Step 6: Implement health semantics**

`/health/live` remains process-only. `/health/ready` returns 200 only when database is open, OpenWebUI auth was verified, Hermes read API is reachable, and Desktop connector heartbeat is current; otherwise return 503 with non-secret component states. `/health/status` returns last scan time, connector version/epoch, profile count, queue counts, uncertain count, and per-chat failure summaries without prompt content.

- [ ] **Step 7: Cover concurrency and partial failure**

Add tests for duplicate/out-of-order events, one failing chat among three, title drift, Desktop-originated turn, periodic scan cancellation on shutdown, tip rotation, connector reconnect, replay success, replay gap, uncertain state retained, and health redaction.

- [ ] **Step 8: Run service tests and commit**

```bash
cd bridge-service
uv run --python 3.12 --extra test pytest tests/hermes tests/services tests/api -q
cd ..
git add bridge-service/src/hermes_bridge/hermes/events.py \
  bridge-service/src/hermes_bridge/services/sync.py bridge-service/src/hermes_bridge/app.py \
  bridge-service/src/hermes_bridge/api/health.py bridge-service/tests
git commit -m "feat: reconcile live Hermes events and session mirrors"
```

---

### Task 9: Compose, Runner, and Secret-safe Lifecycle Integration

**Files:**
- Modify: `compose.yaml`
- Modify: `runner/stack.sh`
- Modify: `runner/tests/test_stack.sh`
- Modify: `tests/test_repository_layout.sh`
- Modify: `Makefile`
- Modify: `README.md`
- Test: `tests/test_bridge_stack.sh`

**Interfaces:**
- Produces: Compose service `bridge-service` on internal port 8787 and host binding `127.0.0.1:${BRIDGE_HOST_PORT}:8787`.
- Produces: named volume `bridge-data` mounted at `/data`.
- Produces: runner layering `/Users/frenzy/.hermes/.env` then repository `.env.local` for every Compose command.
- Consumes: bridge image/application and installed plugin from previous tasks.

- [ ] **Step 1: Extend runner contract tests first**

Add assertions that every `docker compose` invocation contains both ordered env files, `stop` still has no `down` or `--volumes`, bridge readiness is checked on start, and status redacts both API keys and the connector secret.

```zsh
assert_contains "$docker_args" "--env-file $HERMES_ENV_FILE"
assert_contains "$docker_args" "--env-file $PROJECT_DIR/.env.local"
assert_before "$docker_args" "$HERMES_ENV_FILE" "$PROJECT_DIR/.env.local"
```

- [ ] **Step 2: Run runner/layout tests and verify failure**

Run: `make test`

Expected: FAIL because compose has no bridge service and stack.sh loads only the Hermes env file.

- [ ] **Step 3: Add bridge-service to Compose**

Use this topology:

```yaml
services:
  open-webui:
    environment:
      OPENAI_API_BASE_URL: http://bridge-service:8787/v1
      OPENAI_API_KEY: ${HERMES_BRIDGE_SECRET:?HERMES_BRIDGE_SECRET is required}

  bridge-service:
    build:
      context: ./bridge-service
    image: hermes-openwebui-bridge:local
    ports:
      - "127.0.0.1:${BRIDGE_HOST_PORT:-8787}:8787"
    environment:
      API_SERVER_KEY: ${API_SERVER_KEY:?API_SERVER_KEY is required}
      OPENWEBUI_API_KEY: ${OPENWEBUI_API_KEY:?OPENWEBUI_API_KEY is required}
      HERMES_BRIDGE_SECRET: ${HERMES_BRIDGE_SECRET:?HERMES_BRIDGE_SECRET is required}
      HERMES_BASE_URL: http://host.docker.internal:8642
      OPENWEBUI_BASE_URL: http://open-webui:8080
      BRIDGE_DATA_DIR: /data
    volumes:
      - bridge-data:/data
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always
```

Declare `bridge-data` without changing the existing volume name or Compose project name.

- [ ] **Step 4: Layer env files safely in the runner**

Replace repeated compose arguments with a zsh array constructed from fixed paths:

```zsh
COMPOSE_ENV_ARGS=(
  --env-file "$HERMES_ENV_FILE"
  --env-file "$LOCAL_ENV_FILE"
)
```

Validate required assignments with `awk`, never `source`. Extend redaction for `HERMES_BRIDGE_SECRET` and `OPENWEBUI_API_KEY`. Start waits for both OpenWebUI `/health` and bridge `/health/ready`; status distinguishes container-running from connector-ready.

- [ ] **Step 5: Add a fake-connector stack test**

`tests/test_bridge_stack.sh` must generate temporary test env files, start bridge with a fake Hermes/OpenWebUI HTTP pair and fake Desktop connector, call `/health/ready`, call `/v1/models`, and stop only its explicitly named test Compose project. It must trap cleanup and never use the production volumes or production project name.

- [ ] **Step 6: Run static and integration checks**

```bash
make test
docker compose --env-file /Users/frenzy/.hermes/.env --env-file .env.local config >/dev/null
/bin/zsh tests/test_bridge_stack.sh
```

Expected: exit 0; rendered Compose contains two services and two named volumes; no command output contains a secret value.

- [ ] **Step 7: Commit lifecycle integration**

```bash
git add compose.yaml runner Makefile README.md tests
git commit -m "feat: run session bridge with Hermes WebUI stack"
```

---

### Task 10: Pinned OpenWebUI Contract Test, Setup Guide, and Real Acceptance

**Files:**
- Create: `tests/contract/test_openwebui_live.py`
- Create: `tests/fakes/fake_desktop_connector.py`
- Create: `docs/openwebui-bridge-setup.md`
- Modify: `README.md`
- Modify: `Makefile`
- Modify: `bridge-service/README.md`
- Modify: `hermes-plugin/README.md`

**Interfaces:**
- Produces: `make contract-test`, an opt-in test against the locally running pinned OpenWebUI instance.
- Produces: exact one-time manual configuration and recovery instructions.
- Consumes: complete bridge stack from Tasks 1–9.

- [ ] **Step 1: Write the contract test before relying on live OpenWebUI**

The test reads `.env.local` without printing it, creates a uniquely named temporary folder through `/api/v1/folders/`, creates a two-message `hermes-live` chat through `/api/v1/chats/new`, updates one deterministic message through `/api/v1/chats/{id}`, sends `chat:reload`, verifies the full chat, then deletes only the exact chat and folder IDs it created in `finally`.

Skip with an actionable message when `OPENWEBUI_API_KEY` is empty; fail, do not skip, on a 401/403 from a non-empty key.

- [ ] **Step 2: Run the contract test and verify current configuration is reported accurately**

Run:

```bash
make contract-test
```

Expected before manual setup: one explicit skip naming the missing/disabled OpenWebUI API key. Expected after setup: PASS with all temporary records removed.

- [ ] **Step 3: Write the exact setup guide**

Document:

1. `make bootstrap` and the location/mode of `.env.local`;
2. the exact settings path in the pinned OpenWebUI revision for enabling API keys;
3. generating the first administrator's key and placing it under `OPENWEBUI_API_KEY=`;
4. configuring the OpenAI connection base URL to bridge `/v1` and its provider API key to the `HERMES_BRIDGE_SECRET` value from `.env.local`;
5. adding all three custom header templates;
6. `make install-plugin` and how to confirm the plugin is loaded;
7. `make start`, `make status`, `make stop`, and runner-app behavior;
8. key rotation, plugin reinstall after connector-secret rotation, and recovery from `delivery_uncertain`;
9. the fact that new OpenWebUI chats are intentionally unmapped in MVP.

Include no screenshots containing credentials and no real key-shaped example longer than `sk-…`.

- [ ] **Step 4: Run the real stack and inspect fresh evidence**

After the user has populated `.env.local` and configured OpenWebUI:

```bash
make install-plugin
make start
make status
curl --fail --silent http://localhost:11001/health
curl --fail --silent http://127.0.0.1:8787/health/ready
docker volume inspect hermes_open-webui --format '{{.Name}}'
docker volume inspect hermes_bridge-data --format '{{.Name}}'
```

Expected: both health calls succeed, bridge reports Desktop connector ready, and both named volumes exist.

- [ ] **Step 5: Perform the manual session-continuity acceptance sequence**

1. Record one existing Hermes lineage/session ID from bridge status without exposing content.
2. Confirm it appears once in OpenWebUI's `Hermes` folder.
3. Open that chat in both clients.
4. Submit a unique text from OpenWebUI while idle and verify the same lineage receives it.
5. Start a Desktop turn, submit another unique text from OpenWebUI while busy, and verify FIFO order.
6. Submit from Desktop and verify the open OpenWebUI chat receives `chat:reload` and displays the result.
7. Restart bridge-service and verify no duplicate chat or message appears.
8. Quit Hermes Desktop and verify OpenWebUI history remains readable while submit returns `hermes_desktop_offline`.
9. Reopen Hermes Desktop and verify connector readiness recovers without reopening the session manually.

- [ ] **Step 6: Run all automated verification**

```bash
make test
make contract-test
cd bridge-service && uv run --python 3.12 --extra test pytest -q
cd ../hermes-plugin && npm test
cd ..
git diff --check
git status --short
```

Expected: every test exits 0, `git diff --check` is silent, and status contains only the intended documentation changes for this task.

- [ ] **Step 7: Commit documentation and acceptance tooling**

```bash
git add README.md bridge-service/README.md hermes-plugin/README.md \
  docs/openwebui-bridge-setup.md Makefile tests/contract tests/fakes
git commit -m "docs: add Hermes bridge setup and acceptance checks"
```

## Completion Gate

Do not call the MVP complete until all of these are evidenced in one fresh run:

- all Python, Node, shell, layout, contract, and stack tests pass;
- OpenWebUI and bridge containers are running and healthy;
- Desktop connector is authenticated and current;
- existing `hermes_open-webui` data remains present;
- every normal Hermes session appears exactly once under `Hermes`;
- one idle and one busy queued OpenWebUI turn continue the mapped lineage;
- one Desktop-originated turn updates an already-open OpenWebUI chat;
- bridge restart creates no duplicate chat, message, or Hermes turn;
- Desktop-offline behavior fails explicitly without a shadow runtime;
- tracked files and logs contain none of the configured secret values.
