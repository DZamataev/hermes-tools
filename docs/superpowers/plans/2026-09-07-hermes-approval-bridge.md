# Hermes Approval Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every pending Hermes Desktop approval visible and actionable in its mirrored OpenWebUI chat, with all Hermes-supported choices and automatic settlement from either surface.

**Architecture:** The installed Hermes application remains untouched. Its existing Desktop plugin advertises `approvals_v1`, forwards sanitized approval events, performs bounded live-session scans, and executes only the fixed `approval.respond` RPC; the bridge persists and reconciles authoritative approval state and projects a dedicated provider-marked output item; the OpenWebUI fork renders that item and delegates choices to an authenticated bridge-only endpoint without running a local tool or starting model completion.

**Tech Stack:** Hermes Desktop plugin SDK (JavaScript/Node test runner), FastAPI/Pydantic/aiosqlite/httpx/pytest, OpenWebUI Python backend, Svelte 5/TypeScript/Vitest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-07-hermes-approval-bridge-design.md`

## Global Constraints

- Do not patch the installed Hermes application or Hermes Python source; all Hermes-side behavior belongs in `hermes-plugin/`.
- Do not read Hermes or OpenWebUI SQLite directly; only the bridge-owned SQLite database may be accessed by bridge repositories.
- Never call `session.resume` while discovering or reconciling approvals; use only `session.active_list` followed by `approval.pending` for live rows whose status is `waiting`.
- The connector must expose no generic RPC proxy; approval commands may call only `session.active_list`, `approval.pending`, and `approval.respond`.
- Preserve the exact route descriptor (`connection_id`, `profile`, `target_profile`) end-to-end.
- Accept only Hermes-redacted description/command fields, discard unknown payload fields, and never log approval text, arguments, request IDs, session content, API keys, or the bridge secret.
- Allowed choices are exactly `once`, `session`, `always`, and `deny`; `always` is accepted only when Hermes explicitly advertises it and `allow_permanent` is true.
- Failed, partial, timed-out, disconnected, and route-missing scans must never be interpreted as an empty successful snapshot.
- Do not automatically resolve the real pending approval in stored session `20260907_133115_1d5b61`; acceptance stops after proving that its card is visible and waits for the user to choose.
- No OpenWebUI history reset is required or permitted as part of this rollout.
- Use Peekaboo as the computer-use driver for the final visual acceptance check.

---

### Task 1: Desktop Connector Approval Protocol

**Files:**
- Modify: `hermes-plugin/src/connector-core.js`
- Modify: `hermes-plugin/tests/connector-core.test.mjs`

**Interfaces:**
- Consumes: Hermes plugin host methods `profileRoutes()`, `requestProfile(route, method, params, timeout)`, and wildcard `onEvent('*', listener)`.
- Produces: connector capability `approvals_v1`; outgoing frames `approval_request`, `approval_snapshot`, `approval_resolved`; incoming commands `approval_scan`, `resolve_approval`.
- Produces: `approval_scan.payload = { operation_id, route }` and `resolve_approval.payload = { operation_id, approval_id, route, runtime_session_id, stored_session_id, request_id, choice }`.

- [ ] **Step 1: Add failing event-sanitization and capability tests**

Add tests that authenticate the connector, assert `hello.payload.capabilities` is `['replay_complete', 'approvals_v1']`, emit an `approval.request`, and compare the complete outbound payload so extra host fields cannot leak:

```js
test('approval.request is forwarded with only the approved redacted fields', async () => {
  const { connector, host, socket } = await connect()
  host.emit({
    connectionId: 'local', profile: 'default', session_id: 'runtime-1', seq: 17,
    type: 'approval.request',
    payload: {
      request_id: 'request-1', stored_session_id: 'stored-1',
      description: 'Run a command', command: 'git status',
      allow_permanent: true, smart_denied: false,
      choices: ['once', 'session', 'always', 'deny'], secret_argument: 'must-not-cross'
    }
  })
  await Promise.resolve()
  assert.deepEqual(frames(socket, 'approval_request')[0].payload, {
    route: WIRE_ROUTE, runtime_session_id: 'runtime-1', stored_session_id: 'stored-1',
    seq: 17, request_id: 'request-1', description: 'Run a command',
    command: 'git status', allow_permanent: true, smart_denied: false,
    choices: ['once', 'session', 'always', 'deny']
  })
  assert.equal(JSON.stringify(socket.sent).includes('secret_argument'), false)
  connector.stop()
})
```

- [ ] **Step 2: Run the focused connector test and verify red**

Run: `cd hermes-plugin && npm test`

Expected: FAIL because `approval.request` is not forwarded and `approvals_v1` is absent from the hello frame.

- [ ] **Step 3: Add failing bounded-scan and overlap-coalescing tests**

Use a deferred `session.active_list` response to send two `approval_scan` commands for the same route. Assert exactly one active scan runs, only waiting sessions receive `approval.pending`, and both correlations receive a complete sanitized snapshot:

```js
assert.deepEqual(host.calls.map(([method]) => method), [
  'session.active_list', 'approval.pending'
])
assert.deepEqual(snapshot.payload.sessions, [
  { runtime_session_id: 'runtime-waiting', stored_session_id: 'stored-waiting', status: 'waiting' }
])
assert.deepEqual(snapshot.payload.approvals[0], {
  runtime_session_id: 'runtime-waiting', stored_session_id: 'stored-waiting',
  request_id: 'request-1', description: 'Run a command', command: 'git status',
  allow_permanent: true, smart_denied: false,
  choices: ['once', 'session', 'always', 'deny']
})
```

Also make `approval.pending` throw and assert a `command_error` with `code: 'approval_scan_failed'`, not an empty `approval_snapshot`.

- [ ] **Step 4: Run the connector test and verify the scan tests fail**

Run: `cd hermes-plugin && npm test`

Expected: FAIL because `approval_scan` is ignored.

- [ ] **Step 5: Implement sanitized event forwarding and coalesced route scans**

In `connector-core.js`, add:

```js
const CONNECTOR_VERSION = '1.2.0'
const CONNECTOR_CAPABILITIES = ['replay_complete', 'approvals_v1']
const APPROVAL_CHOICES = new Set(['once', 'session', 'always', 'deny'])
const scansByRoute = new Map()

function sanitizeApproval(raw, session = {}) {
  const choices = Array.isArray(raw?.choices)
    ? [...new Set(raw.choices.filter(choice => APPROVAL_CHOICES.has(choice)))]
    : []
  return {
    runtime_session_id: safeString(raw?.runtime_session_id || session.runtime_session_id),
    stored_session_id: safeString(raw?.stored_session_id || session.stored_session_id),
    request_id: safeString(raw?.request_id),
    description: safeString(raw?.description),
    command: safeString(raw?.command),
    allow_permanent: raw?.allow_permanent === true,
    smart_denied: raw?.smart_denied === true,
    choices: choices.filter(choice => choice !== 'always' || raw?.allow_permanent === true)
  }
}
```

Add `approval.request` to the explicit event allowlist but route it through `approval_request`, never through the generic `data: eventData(event.payload)` path. Implement `scanRoute(route)` with one promise per wire-route key, fixed calls to `session.active_list` and `approval.pending`, strict output validation, and a `finally` that deletes the coalescing entry. Fan the completed sanitized result to every waiting command correlation.

- [ ] **Step 6: Add failing fixed-resolution tests**

Test all four choices, exact route and IDs, plus rejection before RPC for an unknown route, mismatched retained request, unsupported choice, and `always` when `allow_permanent` is false:

```js
assert.deepEqual(host.calls.at(-1), [
  'approval.respond', SDK_ROUTE,
  { session_id: 'runtime-waiting', stored_session_id: 'stored-waiting', request_id: 'request-1', choice: 'session' },
  60_000
])
assert.deepEqual(frames(socket, 'approval_resolved')[0].payload, {
  operation_id: 'approval-operation-1', approval_id: 'bridge-approval-1',
  choice: 'session', accepted: true
})
```

- [ ] **Step 7: Implement the fixed `resolve_approval` handler**

Retain only approval identities learned from a successful scan/event, keyed by exact route + runtime ID + stored ID + request ID. Validate the requested choice against that retained record, invoke only `approval.respond`, and return `approval_resolved`. Convert stale Hermes rejection to `command_error` with `code: 'approval_stale'`; set `acceptance_unknown: true` only for transport failures after the request was offered.

- [ ] **Step 8: Run and commit the connector slice**

Run: `cd hermes-plugin && npm test && npm run build`

Expected: all connector tests PASS and the plugin bundle builds.

Commit only these files:

```bash
git add hermes-plugin/src/connector-core.js hermes-plugin/tests/connector-core.test.mjs
git commit -m "feat: bridge Hermes approval RPCs"
```

### Task 2: Typed Bridge Connector Frames and Dispatch

**Files:**
- Modify: `bridge-service/src/hermes_bridge/connector/protocol.py`
- Modify: `bridge-service/src/hermes_bridge/connector/hub.py`
- Modify: `bridge-service/tests/connector/test_protocol.py`
- Modify: `bridge-service/tests/connector/test_hub.py`
- Modify: `bridge-service/tests/connector/test_websocket.py`

**Interfaces:**
- Consumes: Task 1 wire frames and existing `ConnectorHub.dispatch(command)` correlation semantics.
- Produces: `APPROVALS_CAPABILITY = 'approvals_v1'`, `ApprovalChoice`, strict Pydantic payload/frame classes, and terminal dispatch semantics for snapshots/resolution results.

- [ ] **Step 1: Add failing strict protocol tests**

Construct valid frames for all new kinds and assert `parse_incoming()` yields the matching classes. Add rejection cases for unknown choice, duplicate choices, missing target profile, missing stored session, negative sequence, unknown payload field, and an approval command larger than 1 MiB.

```python
frame = parse_incoming(json.dumps({
    "protocol": 1, "kind": "approval_snapshot", "id": "event-1",
    "correlation_id": "scan-1", "sent_at": NOW,
    "payload": {
        "operation_id": "scan-operation-1", "route": ROUTE,
        "sessions": [{"runtime_session_id": "runtime-1", "stored_session_id": "stored-1", "status": "waiting"}],
        "approvals": [{
            "runtime_session_id": "runtime-1", "stored_session_id": "stored-1",
            "request_id": "request-1", "description": "Run a command", "command": "git status",
            "allow_permanent": True, "smart_denied": False,
            "choices": ["once", "session", "always", "deny"],
        }],
    },
}))
assert isinstance(frame, ApprovalSnapshotFrame)
```

- [ ] **Step 2: Run protocol tests and verify red**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/connector/test_protocol.py -q`

Expected: collection/import FAIL because the approval frame classes do not exist.

- [ ] **Step 3: Implement strict frame types and capability verification**

Define:

```python
ApprovalChoice = Literal["once", "session", "always", "deny"]
APPROVALS_CAPABILITY = "approvals_v1"

class LiveSessionIdentity(StrictModel):
    runtime_session_id: str = Field(min_length=1)
    stored_session_id: str = Field(min_length=1)
    status: Literal["waiting"]

class ApprovalRecordPayload(StrictModel):
    runtime_session_id: str = Field(min_length=1)
    stored_session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    description: str
    command: str
    allow_permanent: bool
    smart_denied: bool
    choices: list[ApprovalChoice] = Field(min_length=1)

class ApprovalScanFrame(BaseFrame):
    kind: Literal["approval_scan"] = "approval_scan"
    payload: ApprovalScanPayload

class ApprovalSnapshotFrame(BaseFrame):
    kind: Literal["approval_snapshot"] = "approval_snapshot"
    payload: ApprovalSnapshotPayload

class ResolveApprovalFrame(BaseFrame):
    kind: Literal["resolve_approval"] = "resolve_approval"
    payload: ResolveApprovalPayload

class ApprovalResolvedFrame(BaseFrame):
    kind: Literal["approval_resolved"] = "approval_resolved"
    payload: ApprovalResolvedPayload
```

Add `approval_request` with the same sanitized record plus route and optional non-negative sequence. Validate unique choices and reject `always` unless `allow_permanent` is true. Extend `ConnectorCommand`, `ConnectorEvent`, and `IncomingFrame`. Require both `replay_complete` and `approvals_v1` in `verify_hello()`.

- [ ] **Step 4: Add failing hub terminal-dispatch tests**

Assert an `approval_snapshot`, `approval_resolved`, or correlated approval `command_error` ends one dispatch iterator and removes its pending queue, while an unsolicited `approval_request` reaches the observer without creating a pending dispatch.

- [ ] **Step 5: Implement hub terminal rules and capability access**

Make `_terminal()` return true for `ApprovalSnapshotFrame` and `ApprovalResolvedFrame`. Add:

```python
@property
def capabilities(self) -> frozenset[str]:
    return self._capabilities
```

Set capabilities from authenticated hello, clear them on disconnect, and preserve all existing replacement/overflow behavior.

- [ ] **Step 6: Run and commit the protocol slice**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/connector/test_protocol.py tests/connector/test_hub.py tests/connector/test_websocket.py -q`

Expected: all selected tests PASS.

```bash
git add bridge-service/src/hermes_bridge/connector/protocol.py bridge-service/src/hermes_bridge/connector/hub.py bridge-service/tests/connector/test_protocol.py bridge-service/tests/connector/test_hub.py bridge-service/tests/connector/test_websocket.py
git commit -m "feat: type approval connector frames"
```

### Task 3: Durable Approval Domain and Repository

**Files:**
- Modify: `bridge-service/src/hermes_bridge/domain/models.py`
- Modify: `bridge-service/src/hermes_bridge/domain/ports.py`
- Modify: `bridge-service/src/hermes_bridge/persistence/database.py`
- Modify: `bridge-service/src/hermes_bridge/persistence/repositories.py`
- Modify: `bridge-service/tests/persistence/test_repositories.py`
- Create: `bridge-service/tests/domain/test_approvals.py`

**Interfaces:**
- Consumes: strict route/choice values from Task 2.
- Produces: `ApprovalState`, immutable `PendingApproval`, `stable_approval_id(connection_id, profile, target_profile, lineage_root_id, request_id) -> str`, `ApprovalStore`, and `ApprovalRepository`.

- [ ] **Step 1: Add failing state-machine and stable-ID tests**

```python
def test_approval_identity_is_stable_and_excludes_sensitive_text():
    first = stable_approval_id("local", "default", "backend-default", "lineage-1", "request-1")
    second = stable_approval_id("local", "default", "backend-default", "lineage-1", "request-1")
    assert first == second
    assert "request-1" not in first
    assert "git status" not in first

def test_only_declared_approval_transitions_are_valid():
    assert approval_transition_is_valid(ApprovalState.PENDING, ApprovalState.RESOLVING)
    assert approval_transition_is_valid(ApprovalState.RESOLVING, ApprovalState.DELIVERY_UNCERTAIN)
    assert not approval_transition_is_valid(ApprovalState.RESOLVED_EXTERNAL, ApprovalState.PENDING)
```

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/domain/test_approvals.py -q`

Expected: collection/import FAIL because the domain types do not exist.

- [ ] **Step 2: Implement approval domain types**

Define states `pending`, `resolving`, `delivery_uncertain`, `resolved`, `resolved_external`, `expired`; allow the edges in the approved spec, including known non-acceptance back to pending. Define `PendingApproval` with exact route, lineage/chat/message IDs, stored/runtime/request IDs, redacted strings, a tuple of choices, state, optional resolved choice, and timezone-aware timestamps. Generate the stable ID with a domain-separated SHA-256 digest:

```python
def stable_approval_id(connection_id: str, profile: str, target_profile: str,
                       lineage_root_id: str, request_id: str) -> str:
    material = "\x1f".join(("hermes-approval-v1", connection_id, profile,
                             target_profile, lineage_root_id, request_id)).encode()
    return f"ha_{hashlib.sha256(material).hexdigest()[:32]}"
```

- [ ] **Step 3: Add failing repository migration, identity, transition, and recovery tests**

Open both a new database and a fixture created with the current v1 schema. Assert initialization creates/migrates `pending_approval`; upsert is idempotent; route + stored + request identity cannot collide; choice order is preserved as JSON; `resolve` transitions compare current state atomically; `list_reconcilable()` returns pending/resolving/uncertain rows; and repository startup changes persisted `resolving` rows to `delivery_uncertain`.

```python
stored = await approvals.upsert_pending(candidate)
again = await approvals.upsert_pending(candidate)
assert stored.id == again.id
assert stored.choices == ("once", "session", "always", "deny")
with pytest.raises(InvalidTransition):
    await approvals.transition(stored.id, ApprovalState.RESOLVED)
```

- [ ] **Step 4: Implement schema and repository with atomic transitions**

Create `pending_approval` with primary key `approval_id`, unique `(connection_id, profile, target_profile, stored_session_id, request_id)`, indexed route/state and lineage/state columns, JSON choices, and all fields from the spec. Extend `ApprovalStore` with exact signatures:

The exact protocol method signatures are:

- `upsert_pending(self, approval: PendingApproval) -> PendingApproval`
- `get(self, approval_id: str) -> PendingApproval | None`
- `by_route_request(self, connection_id: str, profile: str, target_profile: str, stored_session_id: str, request_id: str) -> PendingApproval | None`
- `list_for_route(self, connection_id: str, profile: str, target_profile: str) -> list[PendingApproval]`
- `list_reconcilable(self) -> list[PendingApproval]`
- `transition(self, approval_id: str, expected: frozenset[ApprovalState], target: ApprovalState, *, resolved_choice: str | None = None) -> PendingApproval`
- `recover_in_flight(self) -> int`
- `count_by_state(self) -> dict[str, int]`

- [ ] **Step 5: Run and commit the durable-state slice**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/domain/test_approvals.py tests/persistence/test_repositories.py -q`

Expected: all selected tests PASS.

```bash
git add bridge-service/src/hermes_bridge/domain/models.py bridge-service/src/hermes_bridge/domain/ports.py bridge-service/src/hermes_bridge/persistence/database.py bridge-service/src/hermes_bridge/persistence/repositories.py bridge-service/tests/domain/test_approvals.py bridge-service/tests/persistence/test_repositories.py
git commit -m "feat: persist Hermes approvals"
```

### Task 4: Approval Discovery, Reconciliation, and Resolution Service

**Files:**
- Create: `bridge-service/src/hermes_bridge/services/approvals.py`
- Create: `bridge-service/tests/services/test_approvals.py`
- Modify: `bridge-service/src/hermes_bridge/services/sync.py`
- Modify: `bridge-service/tests/services/test_sync.py`

**Interfaces:**
- Consumes: `ConnectorHub`, Task 2 frames, `MappingStore`, `ApprovalStore`, and a projection callback `project(approval: PendingApproval) -> Awaitable[None]`.
- Produces: `ApprovalService.start()`, `close()`, `handle_connector_event(frame)`, `resolve(request)`, and aggregate `status_snapshot()`.

- [ ] **Step 1: Add failing successful-snapshot reconciliation tests**

Build a fake mapping for exact route + stored session. Feed one complete snapshot and assert a stable pending row is persisted/projected. Feed the same snapshot and assert idempotence. Feed a later complete snapshot where the live session still exists but the request is absent and assert `resolved_external`. Feed a snapshot where the runtime is absent and assert `expired`.

```python
await service.handle_connector_event(snapshot(approvals=[approval], sessions=[waiting_session]))
pending = await store.by_route_request("local", "default", "backend-default", "stored-1", "request-1")
assert pending.state is ApprovalState.PENDING

await service.handle_connector_event(snapshot(approvals=[], sessions=[waiting_session]))
assert (await store.get(pending.id)).state is ApprovalState.RESOLVED_EXTERNAL
```

Add negative cases: command error, timeout, connector loss, another route's empty snapshot, and partial invalid data leave the pending state unchanged.

- [ ] **Step 2: Run reconciliation tests and verify red**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/services/test_approvals.py -q`

Expected: collection/import FAIL because `ApprovalService` does not exist.

- [ ] **Step 3: Implement snapshot reconciliation without guessing**

For each approval, look up `MappingStore.by_route_and_stored_id(connection_id, profile, stored_session_id)`. If absent, enqueue one normal inventory scan through `SyncService.request_scan()` and keep the sanitized candidate in a bounded per-route buffer of 256 entries; never attach by runtime ID alone. On a successful complete snapshot, reconcile only persisted approvals matching the same exact route. Project after each persisted state change outside the database write transaction.

- [ ] **Step 4: Add failing periodic/immediate scan tests**

Use fake timers/connector to assert:

- authenticated connect with `approvals_v1` schedules an immediate `approval_scan` per advertised route;
- scan interval is exactly 2 seconds while connected;
- `approval_request` triggers immediate same-route scan;
- disconnected/unsupported connectors send no scans and expose controls as unavailable;
- overlapping ticks do not dispatch a second scan for that route.

- [ ] **Step 5: Implement scan lifecycle and SyncService wiring**

Add `scan_interval_seconds: float = 2.0` to `ApprovalService`. Register a single connector observer in `SyncService` that forwards ordinary frames to the existing handler and approval frames to `ApprovalService`; do not overwrite one observer with another. Start the approval loop after `recover_in_flight()`, cancel it on close, and use a UUID correlation/operation ID per scan.

- [ ] **Step 6: Add failing resolution state/race tests**

Test `once`, `session`, `always`, `deny`; disallowed choice; route unavailable; already resolving/settled; known rejection returns to pending; stale request settles consistently; transport timeout becomes `delivery_uncertain`; success stores the exact bridge-delivered choice; and two concurrent calls emit exactly one connector command.

```python
result = await service.resolve(ResolveApprovalRequest(
    chat_id="chat-1", message_id="approval-message-1",
    approval_id=pending.id, choice="session",
))
assert result.state is ApprovalState.RESOLVED
assert result.resolved_choice == "session"
assert connector.resolve_calls == 1
```

- [ ] **Step 7: Implement fail-closed resolution**

Define service exceptions `ApprovalNotFound`, `ApprovalIdentityMismatch`, `ApprovalAlreadySettled`, `ApprovalChoiceRejected`, `ApprovalRouteUnavailable`, `ApprovalGone`, and `ApprovalDeliveryUncertain`. Load all routing and Hermes identity data from the repository record; the request carries only chat ID, message ID, approval ID, and choice. Atomically claim `pending -> resolving`, dispatch `ResolveApprovalFrame`, then persist exactly one of:

- `resolved` after `ApprovalResolvedFrame(accepted=True)`;
- `pending` after known non-acceptance;
- `delivery_uncertain` after timeout/disconnect/acceptance-unknown;
- `resolved_external` when Hermes proves the request is stale/gone.

- [ ] **Step 8: Run and commit the service slice**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/services/test_approvals.py tests/services/test_sync.py -q`

Expected: all selected tests PASS.

```bash
git add bridge-service/src/hermes_bridge/services/approvals.py bridge-service/src/hermes_bridge/services/sync.py bridge-service/tests/services/test_approvals.py bridge-service/tests/services/test_sync.py
git commit -m "feat: reconcile live Hermes approvals"
```

### Task 5: OpenWebUI Approval Projection and Synthetic Message Lifecycle

**Files:**
- Modify: `bridge-service/src/hermes_bridge/hermes/projection.py`
- Modify: `bridge-service/src/hermes_bridge/openwebui/mirror.py`
- Modify: `bridge-service/src/hermes_bridge/openwebui/client.py`
- Modify: `bridge-service/tests/hermes/test_projection.py`
- Modify: `bridge-service/tests/openwebui/test_mirror.py`
- Modify: `bridge-service/tests/openwebui/test_client.py`

**Interfaces:**
- Consumes: `PendingApproval` and canonical Hermes projection/mirror APIs.
- Produces: `approval_message_id(approval_id)`, `approval_output_item(approval)`, and `MirrorService.project_approval(approval)`.

- [ ] **Step 1: Add failing pure projection tests**

Assert deterministic assistant message/output structure, full and reduced choices, and all display states. The pending output item must be exactly provider-marked and contain no raw route/runtime/request identifiers:

```python
assert item == {
    "type": "function_call",
    "id": pending.id,
    "call_id": pending.id,
    "name": "Hermes approval",
    "arguments": {"description": "Run a command", "command": "git status"},
    "status": "pending",
    "provider": "hermes",
    "hermes_approval": {
        "id": pending.id,
        "allowed_choices": ["once", "session", "always", "deny"],
        "state": "pending",
        "resolved_choice": None,
    },
}
```

For `resolved_external`, expect `status: 'completed'`, `state: 'resolved_external'`, no resolved choice, and `done: true` on the synthetic assistant node. For a denied bridge choice, expose display state `rejected` without inserting a generic OpenWebUI tool result.

- [ ] **Step 2: Run projection tests and verify red**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/hermes/test_projection.py -q`

Expected: FAIL because approval projection helpers do not exist.

- [ ] **Step 3: Implement deterministic approval projection**

Use ID `hermes-approval-{approval.id}` for the assistant node and the stable approval ID for call IDs. Keep `done: false` only for `pending`, `resolving`, and `delivery_uncertain`. Map settled states to `completed`, except a known bridge `deny` maps to `rejected`. Include only `provider`, bridge approval ID, redacted display fields, allowed choices, state, and resolved choice.

- [ ] **Step 4: Add failing mirror lifecycle tests**

Assert `project_approval()`:

- inserts/updates the stable assistant node under the correct mapped chat;
- adds its ID to `chat.variables.hermes_bridge_message_ids`;
- preserves unrelated local nodes and existing bridge-owned canonical nodes;
- emits one `chat:reload` after update;
- updates a pending card in place when state settles;
- never removes a still-pending card because canonical history omitted it;
- removes a settled synthetic card only after canonical terminal history represents the tool outcome.

- [ ] **Step 5: Implement approval-aware mirror reconciliation**

Add `OpenWebUIClient.emit_chat_reload(chat_id, message_id)` if not already independently callable. In `MirrorService`, merge the synthetic node after canonical projection and before ownership cleanup. Treat its stable ID as bridge-owned. Add a narrow `canonical_represents_approval(history, approval)` predicate that requires the settled request's canonical tool identity; omission alone returns false.

- [ ] **Step 6: Run and commit the projection slice**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/hermes/test_projection.py tests/openwebui/test_mirror.py tests/openwebui/test_client.py -q`

Expected: all selected tests PASS.

```bash
git add bridge-service/src/hermes_bridge/hermes/projection.py bridge-service/src/hermes_bridge/openwebui/mirror.py bridge-service/src/hermes_bridge/openwebui/client.py bridge-service/tests/hermes/test_projection.py bridge-service/tests/openwebui/test_mirror.py bridge-service/tests/openwebui/test_client.py
git commit -m "feat: project Hermes approval cards"
```

### Task 6: Authenticated Bridge Approval HTTP Endpoint and Health

**Files:**
- Create: `bridge-service/src/hermes_bridge/api/approvals.py`
- Create: `bridge-service/tests/api/test_approvals.py`
- Modify: `bridge-service/src/hermes_bridge/app.py`
- Modify: `bridge-service/src/hermes_bridge/api/health.py`
- Modify: `bridge-service/tests/test_health.py`

**Interfaces:**
- Consumes: `ApprovalService.resolve()` and existing `HERMES_BRIDGE_SECRET` bearer credential.
- Produces: `POST /internal/approvals/{approval_id}/resolve` with body `{chat_id, message_id, choice}` and safe status mapping.

- [ ] **Step 1: Add failing endpoint authorization/identity/status tests**

Use FastAPI's test client with a fake approval service. Assert missing/wrong bearer secret is 401; valid resolution is 200; chat/message mismatch is 404; already resolving/settled is 409; gone is 410; disallowed choice is 422; unavailable route is 503; timeout/uncertain delivery is 504. Assert no response body contains the bridge secret, route, runtime ID, stored ID, Hermes request ID, command, or description.

```python
response = client.post(
    f"/internal/approvals/{APPROVAL_ID}/resolve",
    headers={"Authorization": f"Bearer {settings.bridge_secret}"},
    json={"chat_id": "chat-1", "message_id": "approval-message-1", "choice": "once"},
)
assert response.status_code == 200
assert response.json() == {"approval_id": APPROVAL_ID, "state": "resolved", "resolved_choice": "once"}
```

- [ ] **Step 2: Run endpoint tests and verify red**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest tests/api/test_approvals.py -q`

Expected: 404 because the internal approvals router is not registered.

- [ ] **Step 3: Implement endpoint and composition-root wiring**

Authenticate with constant-time comparison using the same bearer parsing policy as `/v1`. Define `ResolveApprovalForm` with forbidden extra fields. Instantiate `ApprovalRepository` and `ApprovalService` in lifespan, inject mirror projection, expose the service only on `app.state`, and register `create_approval_router(settings.bridge_secret)`. Never publish a new host port.

- [ ] **Step 4: Add failing aggregate-health tests**

Assert `/health/status` includes only:

```json
{
  "approvals": {
    "pending": 1,
    "resolving": 0,
    "delivery_uncertain": 0,
    "resolved_external": 2,
    "failed_reconciliation_count": 0,
    "last_successful_scan": "2026-09-07T12:00:00+00:00"
  }
}
```

Assert the response excludes descriptions, commands, request IDs, session IDs, chat IDs, and secrets; readiness reports approval controls unavailable when the connected plugin lacks `approvals_v1`.

- [ ] **Step 5: Implement health aggregation and run the bridge suite**

Run: `cd bridge-service && uv run --python 3.12 --extra test pytest -q`

Expected: all bridge tests PASS.

```bash
git add bridge-service/src/hermes_bridge/api/approvals.py bridge-service/src/hermes_bridge/app.py bridge-service/src/hermes_bridge/api/health.py bridge-service/tests/api/test_approvals.py bridge-service/tests/test_health.py
git commit -m "feat: expose secure approval resolution"
```

### Task 7: OpenWebUI Backend Delegation Without Local Tool Execution

**Files:**
- Modify: `open-webui/backend/open_webui/utils/tool_approval.py`
- Create: `open-webui/backend/open_webui/utils/hermes_approval.py`
- Create: `open-webui/backend/tests/test_hermes_approval.py`
- Modify: `open-webui/backend/open_webui/main.py`

**Interfaces:**
- Consumes: Task 5 provider-marked output item and Task 6 internal endpoint.
- Produces: `HermesResolveForm(choice)` accepted by the existing chat-message resolve route when the canonical item has `provider == 'hermes'`.

- [ ] **Step 1: Add failing canonical-validation and delegation tests**

Patch `Chats` accessors, the bridge httpx transport, `chat_completion`, and local tool-resolution helpers. Test owner/admin access, non-owner rejection, missing message/call, forged provider marker, mismatched bridge approval ID, settled status, choice not in canonical `allowed_choices`, bridge 409/410/422/503/504 propagation, and successful delegation. On success assert exactly one bridge call and zero calls to `resolve_tool_call_output`, `build_tool_approval_resume_payload`, and `chat_completion`.

```python
assert bridge_request.json() == {
    "chat_id": "chat-1", "message_id": "approval-message-1", "choice": "always"
}
assert bridge_request.headers["Authorization"] == "Bearer test-bridge-secret"
assert local_tool_runner.call_count == 0
assert model_completion.call_count == 0
```

- [ ] **Step 2: Run backend test and verify red**

Run: `cd open-webui && PYTHONPATH=backend pytest backend/tests/test_hermes_approval.py -q`

Expected: collection/import FAIL because `hermes_approval.py` does not exist.

- [ ] **Step 3: Implement a narrow internal bridge client and canonical item validator**

Read `HERMES_BRIDGE_INTERNAL_URL` and `HERMES_BRIDGE_SECRET` only from backend environment. Validate this exact canonical shape before delegating:

```python
marker = function_call.get("hermes_approval")
is_hermes = (
    function_call.get("provider") == "hermes"
    and isinstance(marker, dict)
    and marker.get("id") == call_id
    and function_call.get("call_id") == call_id
)
```

Require `function_call.status == 'pending'`, `marker.state == 'pending'`, and the choice in a unique subset of the four literals. Use a short `httpx.AsyncClient` timeout and safe fixed error text; never log request/response bodies.

- [ ] **Step 4: Branch the existing endpoint before ordinary resolution**

Extend the request form with optional `choice: Literal['once', 'session', 'always', 'deny']`. In `resolve_chat_message_tool_call`, load/authorize the canonical call first. If and only if it validates as Hermes, require `choice`, call the bridge, emit `chat:reload`, and return without building a resume payload. Preserve the existing `approve`/`reject`/`answer` path byte-for-byte for ordinary tools.

- [ ] **Step 5: Verify ordinary OpenWebUI approvals remain unchanged**

Add a regression test passing a normal `function_call` and assert the existing resolver and completion pipeline are called once with the original action semantics.

- [ ] **Step 6: Run and commit the backend slice**

Run: `cd open-webui && PYTHONPATH=backend pytest backend/tests/test_hermes_approval.py -q`

Expected: all selected tests PASS.

```bash
git add open-webui/backend/open_webui/utils/tool_approval.py open-webui/backend/open_webui/utils/hermes_approval.py open-webui/backend/open_webui/main.py open-webui/backend/tests/test_hermes_approval.py
git commit -m "feat: delegate Hermes approvals from OpenWebUI"
```

### Task 8: Full Four-Choice OpenWebUI Approval Card

**Files:**
- Modify: `open-webui/src/lib/components/chat/Messages/structuredOutput.ts`
- Create: `open-webui/src/lib/components/chat/Messages/structuredOutput.test.ts`
- Create: `open-webui/src/lib/components/common/hermesApproval.ts`
- Create: `open-webui/src/lib/components/common/hermesApproval.test.ts`
- Modify: `open-webui/src/lib/components/common/ToolCallDisplay.svelte`
- Modify: `open-webui/src/lib/components/chat/Messages/StructuredOutputRenderer.svelte`
- Modify: `open-webui/src/lib/apis/chats/index.ts`

**Interfaces:**
- Consumes: canonical Hermes output metadata and existing resolve endpoint.
- Produces: typed `HermesApprovalView`, pure `getHermesApprovalView(item)`, and `resolveChatMessageHermesApproval(token, chatId, messageId, callId, choice)`.

- [ ] **Step 1: Add failing pure metadata/parser tests**

Test full choices, smart-denied reduced choices, forged/unknown provider data, duplicate/unknown choices, `always` with false permanent flag, resolving/uncertain disabled controls, known settled choice, and external settlement label.

```ts
expect(getHermesApprovalView(fullItem)).toEqual({
  approvalId: 'ha_123',
  allowedChoices: ['once', 'session', 'always', 'deny'],
  pending: true,
  disabled: false,
  settledLabel: ''
})
expect(getHermesApprovalView(externalItem)?.settledLabel).toBe('Resolved in Hermes Desktop')
```

Run: `cd open-webui && npm run test:frontend -- src/lib/components/common/hermesApproval.test.ts src/lib/components/chat/Messages/structuredOutput.test.ts --run`

Expected: FAIL because the helper/test target does not exist.

- [ ] **Step 2: Implement typed parser and preserve metadata through structured output**

Extend `OutputDetailToken.attributes` with `provider?: string` and `hermesApproval?: HermesApprovalView`. In `buildToolCallToken`, pass validated provider metadata through `getHermesApprovalView(item)`; never stringify it into visible raw JSON. Keep ordinary tool tokens unchanged.

- [ ] **Step 3: Add the four-choice API method**

Implement:

```ts
export const resolveChatMessageHermesApproval = async (
  token: string, id: string, messageId: string, callId: string,
  choice: 'once' | 'session' | 'always' | 'deny'
) => resolveChatMessageToolCallRequest(token, id, messageId, {
  call_id: callId, action: choice === 'deny' ? 'reject' : 'approve', choice
});
```

Extract the shared fetch body from the existing function without changing its public signature or ordinary action payload.

- [ ] **Step 4: Render the dedicated Hermes controls**

Change `ToolCallDisplay` callback to accept `boolean | HermesApprovalChoice`, or add a separate `onHermesResolve(choice)` prop. For a valid pending Hermes view render:

- primary button `Allow once` only when `once` is advertised;
- menu items `Allow for this session` and `Always allow` only when advertised;
- destructive secondary button `Deny` only when advertised;
- all controls disabled while resolving, `delivery_uncertain`, or route unavailable;
- settled text containing the known choice, or `Resolved in Hermes Desktop` for external settlement.

Use `on:click|stopPropagation` for every control so opening the details card does not submit. Do not render ordinary Allow/Deny buttons for Hermes items.

- [ ] **Step 5: Wire renderer choice submission and reload behavior**

Change `resolvingCallId` handling to call `resolveChatMessageHermesApproval` for Hermes metadata, call the old resolver for normal tools, keep controls actionable after a safe backend failure, and invoke `onToolCallResolved(res)` only after success. A subsequent bridge `chat:reload` supplies canonical settled state.

- [ ] **Step 6: Run frontend tests, type check, and commit**

Run:

```bash
cd open-webui
npm run test:frontend -- src/lib/components/common/hermesApproval.test.ts src/lib/components/chat/Messages/structuredOutput.test.ts --run
npm run check
```

Expected: Vitest tests PASS and Svelte/type checks finish with no new errors.

```bash
git add open-webui/src/lib/components/chat/Messages/structuredOutput.ts open-webui/src/lib/components/chat/Messages/structuredOutput.test.ts open-webui/src/lib/components/common/hermesApproval.ts open-webui/src/lib/components/common/hermesApproval.test.ts open-webui/src/lib/components/common/ToolCallDisplay.svelte open-webui/src/lib/components/chat/Messages/StructuredOutputRenderer.svelte open-webui/src/lib/apis/chats/index.ts
git commit -m "feat: render Hermes approval choices"
```

### Task 9: Stack Configuration, Contract Coverage, and Operations Documentation

**Files:**
- Modify: `compose.yaml`
- Modify: `tests/test_bridge_stack.sh`
- Modify: `tests/contract/test_openwebui_live.py`
- Modify: `runner/stack.sh`
- Modify: `docs/openwebui-bridge-setup.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed connector, bridge endpoint, and OpenWebUI fork.
- Produces: container-only OpenWebUI-to-bridge URL, approval-capable readiness, isolated fake approval flow, and safe operator instructions.

- [ ] **Step 1: Add failing Compose/config assertions**

Extend the repository/stack test to render Compose config and assert the OpenWebUI service receives:

```yaml
HERMES_BRIDGE_INTERNAL_URL: http://bridge-service:8787
HERMES_BRIDGE_SECRET: ${HERMES_BRIDGE_SECRET:?HERMES_BRIDGE_SECRET is required}
```

Assert the bridge still has only its existing loopback host binding and no new approval port. Run `make test`; expected FAIL because these variables are absent.

- [ ] **Step 2: Wire container environment and approval-aware readiness**

Add the two environment variables to `open-webui`. Update runner readiness text to require both `replay_complete` and `approvals_v1`. Preserve `.env.local` as the ignored secret source and `.env.local.example` as the tracked blank example; do not duplicate or print the secret.

- [ ] **Step 3: Extend the isolated stack fake connector contract**

Advertise both capabilities. Make the fake connector respond to `approval_scan` with one complete snapshot and to `resolve_approval` by incrementing a persisted counter and returning `approval_resolved`. Make fake OpenWebUI store chat updates/events in memory. Assert rediscovery after connector restart, one bridge projection, each synthetic choice reaching fake Hermes exactly once, and a later empty successful snapshot settling the card externally. Use only synthetic text such as `safe fixture command`.

- [ ] **Step 4: Extend the live OpenWebUI contract without touching real approval state**

Create an owner-scoped temporary chat containing a synthetic Hermes approval item. Verify the backend accepts each choice against a fake/internal bridge transport in the isolated contract, the output shape renders through the built frontend, and cleanup deletes only the temporary chat/folder. The live test for `20260907_133115_1d5b61` must only poll for a provider-marked pending card and assert it stays pending; it must never call the resolve endpoint.

- [ ] **Step 5: Document rollout and operator-visible failure states**

Document these exact steps: build images, reinstall/reload plugin by disabling then enabling it, run `make start`, check `/health/status` aggregates, open the already mirrored chat without history reset, and wait for its approval card. Document that `delivery_uncertain` disables controls until a successful snapshot, Desktop resolution becomes `Resolved in Hermes Desktop`, and plugin versions without `approvals_v1` make approval controls unavailable.

- [ ] **Step 6: Run and commit stack/docs slice**

Run:

```bash
make test
make bridge-stack-test
make contract-test
```

Expected: all repository, isolated-stack, and live OpenWebUI contracts PASS; if `OPENWEBUI_API_KEY` is intentionally absent, only the existing documented live contract skip is allowed.

```bash
git add compose.yaml tests/test_bridge_stack.sh tests/contract/test_openwebui_live.py runner/stack.sh docs/openwebui-bridge-setup.md README.md
git commit -m "test: cover Hermes approval bridge rollout"
```

### Task 10: Full Verification and Safe Real-Session Acceptance

**Files:**
- Verify only; do not modify the real Hermes session or reset OpenWebUI history.

**Interfaces:**
- Consumes: all previous tasks and stored session `20260907_133115_1d5b61`.
- Produces: evidence that the full stack discovers the current pending approval and waits for the user's explicit choice.

- [ ] **Step 1: Run all local suites from a clean dependency state**

```bash
cd hermes-plugin && npm test && npm run build
cd ../bridge-service && uv run --python 3.12 --extra test pytest -q
cd ../open-webui && npm run test:frontend -- --run && npm run check
cd .. && make test && make bridge-stack-test
```

Expected: every command PASS with no newly introduced warnings treated as errors by its suite.

- [ ] **Step 2: Rebuild and roll out without history reset**

Run:

```bash
make install-plugin
make start
make status
```

Then disable and re-enable the OpenWebUI Bridge plugin in Hermes Desktop. Do not run `make reset-history`.

- [ ] **Step 3: Verify secret-free health and approval capability**

Read `/health/status` and assert Desktop connector is ready, `approvals_v1` is available, `last_successful_scan` is non-null, failed reconciliation count is zero, and only aggregate approval counts are exposed. Search container logs for the real stored session ID, request ID, command text, and bridge secret using locally held expected values without printing matches; the check must return no matches.

- [ ] **Step 4: Verify the current real approval visually with Peekaboo**

Using Peekaboo, open the mirrored OpenWebUI chat for stored session `20260907_133115_1d5b61`. Verify a native-looking pending Hermes card is present, only Hermes-advertised choices are visible, controls are enabled when the exact route is connected, and the transcript layout remains formatted. Do not click any approval choice.

- [ ] **Step 5: Wait for explicit user choice, then verify cross-surface settlement**

Ask the user to choose in either OpenWebUI or Hermes Desktop. After the user acts, verify the other surface settles within one successful two-second scan and the Hermes turn continues under the same runtime/stored session without reopening it. If the user chose in OpenWebUI, verify the fake/aggregate evidence shows one `approval.respond`, never two; do not expose its sensitive payload.

- [ ] **Step 6: Record final verification commit only if verification required fixes**

If verification changed tracked files, stage each verified file explicitly and commit:

```bash
git commit -m "fix: complete Hermes approval acceptance"
```

If no files changed, do not create an empty commit. Report the exact passing commands, the visible real card state, and whether the user has completed the final manual choice.

## Plan Self-Review

- Spec coverage: Tasks 1-2 cover live forwarding, fixed RPCs, complete-vs-failed scans, overlap coalescing, capability negotiation, and route fidelity. Tasks 3-6 cover durable identity/state, restart recovery, reconciliation, safe mirror ownership, authenticated resolution, failure codes, and secret-free health. Tasks 7-8 preserve ordinary OpenWebUI behavior while adding canonical Hermes delegation and all four UI choices. Tasks 9-10 cover Docker wiring, restart rediscovery, external settlement, no-reset rollout, and the real-session acceptance target.
- Security coverage: no installed-Hermes patch, no generic RPC, no source SQLite reads, no session resume during discovery, no unredacted payload logging, no secret reflection, no extra host port, exact identity validation, and no automatic real approval choice.
- Type consistency: wire choices, domain choices, HTTP form choices, and TypeScript choices all use `once | session | always | deny`; approval states consistently use `pending | resolving | delivery_uncertain | resolved | resolved_external | expired`; provider marker is consistently `provider: 'hermes'` with nested `hermes_approval.id` equal to `call_id`.
- Placeholder scan: this plan contains no deferred implementation markers; every task names its files, interfaces, red/green command, expected result, implementation boundary, and explicit commit scope.
