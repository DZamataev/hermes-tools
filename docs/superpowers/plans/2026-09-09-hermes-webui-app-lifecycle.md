# Hermes WebUI App Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the root OpenWebUI/Compose integration and make `Hermes WebUI.app` own a native Hermes WebUI process for the application's normal lifetime while preserving the existing LaunchAgent option.

**Architecture:** A focused Zsh controller starts `hermes-webui/start.sh` in foreground mode, records and validates the process it created, waits for health, and stops only that owned process. A thin stay-open AppleScript app delegates start and stop to the controller, while the existing launchd scripts remain independent and mutually exclusive on the same endpoint.

**Tech Stack:** Zsh, Bash, AppleScript (`osacompile`), macOS process tools, shell contract tests, Make, Git submodules

**Spec:** `/Users/frenzy/dev/hermes/hermes-tools/docs/superpowers/specs/2026-09-09-hermes-webui-app-lifecycle-design.md`

## Global Constraints

- Preserve the `hermes-webui` submodule and every Docker file inside it.
- Preserve `desktop-plugins/comp-count`, `restore-teamclaude.sh`, and the existing launchd runner.
- The Dock app defaults to `127.0.0.1:8787` and never enables, disables, or unloads launchd.
- The controller may signal only a PID whose live command identifies this checkout's `hermes-webui/start.sh` or `hermes-webui/bootstrap.py`.
- An external process already serving the configured endpoint is never adopted or stopped.
- Tests must use temporary state and fake commands; they must not touch the user's real Hermes state, LaunchAgent, Docker daemon, or WebUI processes.
- No new runtime dependency or framework is introduced.

## File Structure

- Modify `.gitmodules` to remove `open-webui`; preserve `hermes-webui` as its only entry.
- Delete `open-webui`, `bridge-service/`, `hermes-plugin/`, `compose.yaml`, `runner/stack.sh`, and `runner/tests/test_stack.sh`: obsolete OpenWebUI integration.
- Delete the four 2026-09-03 OpenWebUI/Docker runner specs and plans: obsolete active guidance.
- Create `runner/hermes-webui-app.sh`: sole owner of native app start, health, PID validation, status, and stop behavior.
- Create `runner/tests/test_webui_app.sh`: isolated behavioral contract for the lifecycle controller.
- Modify `runner/HermesWebUIRunner.applescript`: native controller and port 8787 UI wiring.
- Modify `runner/tests/test_app_bundle.sh`: source and compiled-bundle contract for the native application.
- Modify `runner/build-app.sh`: keep deterministic compilation but name native inputs explicitly.
- Modify `tests/test_repository_layout.sh`: assert the new root layout and absence of obsolete integration paths.
- Modify `Makefile`: remove Compose/OpenWebUI targets and run native app tests.
- Modify `README.md`: document the two native launch modes and their mutual exclusion.

---

### Task 1: Remove the obsolete OpenWebUI integration

**Files:**

- Modify: `tests/test_repository_layout.sh`
- Modify: `.gitmodules`
- Modify: `Makefile`
- Delete: `open-webui` Git submodule
- Delete: `bridge-service/README.md`
- Delete: `hermes-plugin/README.md`
- Delete: `compose.yaml`
- Delete: `runner/stack.sh`
- Delete: `runner/tests/test_stack.sh`
- Delete: `docs/superpowers/specs/2026-09-03-hermes-openwebui-bridge-design.md`
- Delete: `docs/superpowers/specs/2026-09-03-hermes-webui-runner-design.md`
- Delete: `docs/superpowers/plans/2026-09-03-hermes-openwebui-bridge.md`
- Delete: `docs/superpowers/plans/2026-09-03-hermes-webui-runner.md`

**Interfaces:**

- Consumes: the approved cleanup list in the design spec.
- Produces: a root repository with `hermes-webui` as its only Git submodule and no root OpenWebUI/Compose runtime commands.

- [ ] **Step 1: Replace the repository-layout test with the cleanup contract**

Keep the existing assertions for `README.md`, `Makefile`, `hermes-webui`, the
desktop plugin, restore script, and launchd files. Replace obsolete positive
assertions with these negative assertions:

```zsh
for obsolete in \
  "$ROOT/open-webui" \
  "$ROOT/bridge-service" \
  "$ROOT/hermes-plugin" \
  "$ROOT/compose.yaml" \
  "$ROOT/runner/stack.sh" \
  "$ROOT/runner/tests/test_stack.sh"; do
  [[ ! -e "$obsolete" ]] || fail "obsolete OpenWebUI path remains: $obsolete"
done

[[ -e "$ROOT/hermes-webui/.git" ]] || fail "hermes-webui submodule is missing"
[[ "$(git config -f "$ROOT/.gitmodules" --get-regexp '^submodule\..*\.path$' | wc -l | tr -d ' ')" == "1" ]] ||
  fail "hermes-webui must be the only registered submodule"
[[ "$(git config -f "$ROOT/.gitmodules" --get submodule.hermes-webui.path)" == "hermes-webui" ]] ||
  fail "hermes-webui submodule path is incorrect"
[[ "$(git config -f "$ROOT/.gitmodules" --get submodule.hermes-webui.url)" == "git@github.com:DZamataev/hermes-webui.git" ]] ||
  fail "hermes-webui submodule URL is incorrect"
```

Also assert that the four obsolete 2026-09-03 spec/plan paths do not exist.

- [ ] **Step 2: Run the layout test and verify RED**

Run:

```bash
/bin/zsh tests/test_repository_layout.sh
```

Expected: FAIL on the first obsolete path, `open-webui`.

- [ ] **Step 3: Remove tracked obsolete files and the OpenWebUI gitlink**

Use `git rm` with the exact approved paths:

```bash
git rm -f open-webui
git rm bridge-service/README.md hermes-plugin/README.md compose.yaml \
  runner/stack.sh runner/tests/test_stack.sh \
  docs/superpowers/specs/2026-09-03-hermes-openwebui-bridge-design.md \
  docs/superpowers/specs/2026-09-03-hermes-webui-runner-design.md \
  docs/superpowers/plans/2026-09-03-hermes-openwebui-bridge.md \
  docs/superpowers/plans/2026-09-03-hermes-webui-runner.md
```

Rewrite `.gitmodules` so it contains only:

```ini
[submodule "hermes-webui"]
	path = hermes-webui
	url = git@github.com:DZamataev/hermes-webui.git
	branch = master
```

- [ ] **Step 4: Remove obsolete Make targets without adding the new controller yet**

Make `.PHONY` contain only the current `bootstrap`, `test`, `app`, and
`webui-*` targets. Remove `runner/tests/test_stack.sh` from `test`; remove the
`start`, `stop`, `status`, and `openwebui-fetch` recipes. Keep `app` and all
launchd recipes unchanged for this task.

- [ ] **Step 5: Verify GREEN for the cleanup boundary**

Run:

```bash
/bin/zsh tests/test_repository_layout.sh
git submodule status
rg -n --hidden -g '!.git/**' -g '!hermes-webui/**' \
  'open-webui|openwebui|OpenWebUI|runner/stack\.sh|compose\.yaml' .
```

Expected: layout test PASS; submodule status lists only `hermes-webui`; `rg`
finds only the approved 2026-09-09 spec/plan descriptions of the cleanup.

- [ ] **Step 6: Commit the cleanup**

```bash
git add .gitmodules Makefile tests/test_repository_layout.sh
git commit -m "chore: remove obsolete OpenWebUI stack"
```

---

### Task 2: Add the app-owned native lifecycle controller

**Files:**

- Create: `runner/tests/test_webui_app.sh`
- Create: `runner/hermes-webui-app.sh`
- Modify: `Makefile`

**Interfaces:**

- Consumes: executable `hermes-webui/start.sh`, `hermes-webui/bootstrap.py`, and `hermes-webui/scripts/lib/health_probe.sh`.
- Produces: `runner/hermes-webui-app.sh {start|stop|status}`; exit 0 means the requested lifecycle state was reached.
- State: `HERMES_WEBUI_APP_PID_FILE` defaults to `~/.hermes/webui-app.pid`; `HERMES_WEBUI_APP_LOG_FILE` defaults to `~/.hermes/webui-app.log`.
- Test overrides: `HERMES_WEBUI_DIR`, `HERMES_WEBUI_USER_HOME`, `HERMES_WEBUI_PYTHON`, `HERMES_WEBUI_HOST`, `HERMES_WEBUI_PORT`, `HERMES_WEBUI_APP_PID_FILE`, `HERMES_WEBUI_APP_LOG_FILE`, `HERMES_WEBUI_APP_START_TIMEOUT`, `HERMES_WEBUI_APP_STOP_TIMEOUT`, `HERMES_WEBUI_APP_PS_BIN`, `HERMES_WEBUI_APP_CURL_BIN`, and `HERMES_WEBUI_APP_SLEEP_BIN`.

- [ ] **Step 1: Write the controller's failing contract test**

Create a temporary fake WebUI checkout and fake commands. The fake
`start.sh` records arguments, then stays alive; fake `curl` succeeds only when
the test creates `$FAKE_HEALTHY`; fake `ps` delegates to `/bin/ps` unless
`$FAKE_PS_OUTPUT` is set.

```zsh
#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
CONTROLLER="$ROOT/runner/hermes-webui-app.sh"
TEST_TMP="$(mktemp -d)"
trap '[[ -f "$TEST_TMP/app.pid" ]] && /bin/kill "$(<"$TEST_TMP/app.pid")" 2>/dev/null || true; /bin/rm -rf "$TEST_TMP"' EXIT

fail() { print -u2 -- "FAIL: $*"; exit 1; }
assert_contains() { [[ "$1" == *"$2"* ]] || fail "expected <$2> in <$1>"; }

[[ -x "$CONTROLLER" ]] || fail "native app controller is missing"
```

The test cases must assert observable behavior:

```zsh
output=$(run_controller start)
assert_contains "$output" "Started Hermes WebUI"
[[ -s "$TEST_TMP/app.pid" ]] || fail "start must persist a PID"
assert_contains "$(<"$FAKE_START_CALLS")" "--foreground --no-browser --host 127.0.0.1 8787"

first_pid="$(<"$TEST_TMP/app.pid")"
run_controller start >/dev/null
[[ "$(<"$TEST_TMP/app.pid")" == "$first_pid" ]] || fail "start must reattach instead of spawning a duplicate"

run_controller stop >/dev/null
/bin/kill -0 "$first_pid" 2>/dev/null && fail "stop must terminate the owned process"
[[ ! -e "$TEST_TMP/app.pid" ]] || fail "stop must clear owned state"
```

Add independent cases for malformed/stale PID cleanup, health timeout killing
the newly spawned PID, refusal when fake curl reports an external responder,
refusal to signal a live PID whose fake `ps` output does not contain the
configured `start.sh` or `bootstrap.py`, `status` output for owned/external/
stopped states, invalid port exit, unknown command exit 64, and log/PID modes
600. Each case resets temporary PID, log, fake health, and call files.

- [ ] **Step 2: Run the controller test and verify RED**

Run:

```bash
/bin/zsh runner/tests/test_webui_app.sh
```

Expected: FAIL with `native app controller is missing`.

- [ ] **Step 3: Implement configuration, validation, and ownership helpers**

Create `runner/hermes-webui-app.sh` with these public defaults and helpers:

```zsh
#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
WEBUI_DIR="${HERMES_WEBUI_DIR:-$ROOT/hermes-webui}"
USER_HOME="${HERMES_WEBUI_USER_HOME:-${HOME:?HOME is not set}}"
PYTHON_BIN="${HERMES_WEBUI_PYTHON:-$USER_HOME/.hermes/hermes-agent/venv/bin/python}"
HOST="${HERMES_WEBUI_HOST:-127.0.0.1}"
PORT="${HERMES_WEBUI_PORT:-8787}"
PID_FILE="${HERMES_WEBUI_APP_PID_FILE:-$USER_HOME/.hermes/webui-app.pid}"
LOG_FILE="${HERMES_WEBUI_APP_LOG_FILE:-$USER_HOME/.hermes/webui-app.log}"
START_TIMEOUT="${HERMES_WEBUI_APP_START_TIMEOUT:-30}"
STOP_TIMEOUT="${HERMES_WEBUI_APP_STOP_TIMEOUT:-10}"

die() { print -u2 -- "$1"; return 1; }
valid_pid() { [[ "${1:-}" == <1-> ]]; }
process_alive() { /bin/kill -0 "$1" >/dev/null 2>&1; }
process_args() { "$PS_BIN" -p "$1" -o command= 2>/dev/null; }
process_is_owned() {
  local args
  args="$(process_args "$1")" || return 1
  [[ "$args" == *"$WEBUI_DIR/start.sh"* || "$args" == *"$WEBUI_DIR/bootstrap.py"* ]]
}
```

Resolve `ps`, `curl`, and `sleep` from their explicit test overrides first and
then `/bin/ps`, `/usr/bin/curl`, and `/bin/sleep`. Validate executable inputs,
numeric bounded timeouts, and a port in `1..65535`. Create parent directories
with mode 700 and PID/log files with mode 600.

- [ ] **Step 4: Implement endpoint and saved-process classification**

Source `hermes-webui/scripts/lib/health_probe.sh` for the normal health check.
When `HERMES_WEBUI_APP_CURL_BIN` is set, use that injected executable with
`--fail --silent --show-error --max-time 2` against
`http://127.0.0.1:$PORT/health`; this is the deterministic test seam.

Implement `owned_pid` so it prints a PID only when the file contains a numeric,
live, owned process. Remove malformed or dead PID files. A live mismatched PID
returns a distinct error and leaves the file intact so it cannot be overwritten
and later signaled accidentally.

- [ ] **Step 5: Implement start, stop, and status**

Use the exact start invocation:

```zsh
HERMES_WEBUI_PYTHON="$PYTHON_BIN" \
  "$WEBUI_DIR/start.sh" --foreground --no-browser --host "$HOST" "$PORT" \
  >>"$LOG_FILE" 2>&1 &
pid=$!
```

Write `pid` before polling. On each one-second startup iteration, check
`process_alive "$pid"` before the health probe. On failure or timeout, signal
only the local `pid`, wait briefly, clear the PID file only if it still contains
that PID, and return non-zero. If a valid saved owned PID exists, skip spawning
and wait for its health instead.

Before a fresh spawn, probe the endpoint. If it responds without a valid owned
PID, fail with `another server is already responding`.

For stop, require `owned_pid`; send TERM, wait for `STOP_TIMEOUT`, then KILL only
if still alive and still owned. For a mismatched PID print `refusing to stop
unowned PID` and return non-zero. A missing or dead PID is an idempotent stopped
success.

`status` prints one of `owned and running`, `external server responding`,
`unsafe PID state`, or `stopped` and returns non-zero only for unsafe PID state.
Unknown commands print usage and exit 64.

- [ ] **Step 6: Run controller tests and shell syntax verification for GREEN**

Run:

```bash
/bin/zsh -n runner/hermes-webui-app.sh
/bin/zsh runner/tests/test_webui_app.sh
```

Expected: syntax PASS and every lifecycle case PASS without accessing real
state.

- [ ] **Step 7: Add the controller test to Make**

Add this line to the `test` recipe before bundle compilation:

```make
	/bin/zsh runner/tests/test_webui_app.sh
```

Run:

```bash
make test
```

Expected: existing repository, restore, plugin, and launchd tests plus the new
controller tests PASS. The old app bundle assertion may still use port 11001;
if it is part of `make test`, defer adding it to `make test` until Task 3.

- [ ] **Step 8: Commit the controller**

```bash
git add Makefile runner/hermes-webui-app.sh runner/tests/test_webui_app.sh
git commit -m "feat: add app-owned Hermes WebUI lifecycle"
```

---

### Task 3: Retarget the macOS application to native Hermes WebUI

**Files:**

- Modify: `runner/tests/test_app_bundle.sh`
- Modify: `runner/HermesWebUIRunner.applescript`
- Modify: `runner/build-app.sh`
- Modify: `Makefile`
- Regenerate, untracked build artifact: `Hermes WebUI.app`

**Interfaces:**

- Consumes: `runner/hermes-webui-app.sh start` and `runner/hermes-webui-app.sh stop`.
- Produces: a signed, single-instance, stay-open `Hermes WebUI.app` that opens `http://127.0.0.1:8787`.

- [ ] **Step 1: Change the bundle contract test first**

Require the source to reference the native controller and reject the old
controller and port:

```zsh
grep -Fq '/Users/frenzy/dev/hermes/hermes-tools/runner/hermes-webui-app.sh' "$SOURCE" ||
  fail "app must call the native Hermes WebUI controller"
grep -Fq 'http://127.0.0.1:8787' "$SOURCE" ||
  fail "app must open native Hermes WebUI"
! grep -Fq 'runner/stack.sh' "$SOURCE" || fail "app still calls the Docker stack"
! grep -Fq 'localhost:11001' "$SOURCE" || fail "app still opens OpenWebUI"
```

After `"$BUILD"`, apply the same assertions to `/usr/bin/osadecompile "$APP"`.
Keep assertions for the icon, `LSMultipleInstancesProhibited`, bundle ID, and
strict code-sign verification.

- [ ] **Step 2: Run the bundle test and verify RED**

Run:

```bash
/bin/zsh runner/tests/test_app_bundle.sh
```

Expected: FAIL because the source still references `runner/stack.sh` and port
11001.

- [ ] **Step 3: Implement the thin native AppleScript lifecycle**

Replace the source properties and handlers with:

```applescript
property lifecycleScript : "/Users/frenzy/dev/hermes/hermes-tools/runner/hermes-webui-app.sh"
property webUIURL : "http://127.0.0.1:8787"

on run
  try
    do shell script quoted form of lifecycleScript & " start"
    open location webUIURL
  on error errorMessage number errorNumber
    display dialog "Hermes WebUI could not start:" & return & errorMessage buttons {"OK"} default button "OK" with icon stop
    quit
  end try
end run

on reopen
  open location webUIURL
end reopen

on idle
  return 86400
end idle

on quit
  try
    do shell script quoted form of lifecycleScript & " stop"
  on error errorMessage number errorNumber
    display dialog "Hermes WebUI could not stop:" & return & errorMessage buttons {"OK"} default button "OK" with icon caution
  end try
  continue quit
end quit
```

- [ ] **Step 4: Keep the build deterministic and native-named**

In `runner/build-app.sh`, rename `SOURCE` to `APP_SOURCE`; preserve the exact
output path and icon, and keep
`CFBundleIdentifier=local.hermes.webui-runner`, single-instance metadata, Dock
visibility, ad-hoc signing, and the exact-path safety check.

- [ ] **Step 5: Run bundle test for GREEN**

Run:

```bash
/bin/zsh runner/tests/test_app_bundle.sh
```

Expected: AppleScript compiles; bundle metadata, icon, decompiled native URL,
controller reference, and code signature all PASS.

- [ ] **Step 6: Add bundle verification to the root test target**

Ensure `Makefile` runs:

```make
	/bin/zsh runner/tests/test_webui_app.sh
	/bin/zsh runner/tests/test_app_bundle.sh
	/bin/zsh runner/tests/test_launchd.sh
```

Run `make test` and expect all root tests to PASS.

- [ ] **Step 7: Commit the application retargeting**

Do not add the generated `.app` bundle if it is ignored. Commit tracked source
and tests:

```bash
git add Makefile runner/HermesWebUIRunner.applescript runner/build-app.sh \
  runner/tests/test_app_bundle.sh
git commit -m "feat: run native Hermes WebUI from Dock app"
```

---

### Task 4: Document the two native launch modes and verify the complete change

**Files:**

- Modify: `README.md`
- Modify: `tests/test_repository_layout.sh`

**Interfaces:**

- Consumes: completed native controller, app bundle builder, and preserved launchd commands.
- Produces: concise operator documentation and final automated proof of the approved scope.

- [ ] **Step 1: Add failing documentation assertions**

Add these contract checks to `tests/test_repository_layout.sh`:

```zsh
grep -Fq 'make app' "$ROOT/README.md" || fail "README must document the Dock app"
grep -Fq 'make webui-enable' "$ROOT/README.md" || fail "README must document launchd"
grep -Fq '127.0.0.1:8787' "$ROOT/README.md" || fail "README must document the native endpoint"
grep -Fq 'runner/hermes-webui-app.sh' "$ROOT/README.md" || fail "README must document app lifecycle commands"
! rg -n 'OpenWebUI|open-webui|localhost:11001|Docker Compose lifecycle' "$ROOT/README.md" >/dev/null ||
  fail "README still describes the removed OpenWebUI stack"
```

- [ ] **Step 2: Run the layout test and verify RED**

Run:

```bash
/bin/zsh tests/test_repository_layout.sh
```

Expected: FAIL because the README still describes OpenWebUI and port 11001.

- [ ] **Step 3: Rewrite the root README around the surviving tools**

Document:

- `hermes-webui/` as the only submodule;
- `desktop-plugins/comp-count/` and `restore-teamclaude.sh`;
- `make bootstrap` and `make test`;
- `make app`, the `Hermes WebUI.app` artifact, and normal Quit semantics;
- `runner/hermes-webui-app.sh start|status|stop` for recovery after Force Quit;
- default URL `http://127.0.0.1:8787`;
- the existing `make webui-*` launchd commands;
- a warning that app mode and launchd mode cannot share the same host/port and
  that the app never changes launchd configuration.

Do not document removed Compose targets, OpenWebUI remotes, bridge behavior, or
`API_SERVER_KEY` as a runner requirement.

- [ ] **Step 4: Run the complete verification suite**

Run fresh commands and read their complete outputs:

```bash
/bin/zsh -n runner/hermes-webui-app.sh
/bin/zsh -n runner/hermes-webui-launchd.sh
/bin/zsh -n runner/build-app.sh
/bin/zsh tests/test_repository_layout.sh
/bin/zsh runner/tests/test_webui_app.sh
/bin/zsh runner/tests/test_app_bundle.sh
/bin/zsh runner/tests/test_launchd.sh
/bin/sh tests/test-restore-teamclaude.sh
node --test desktop-plugins/comp-count/plugin.test.mjs
make test
git diff --check HEAD~3..HEAD
git status --short
```

Expected: every syntax check and test exits 0; `git diff --check` is silent;
only intentional README/test changes are uncommitted before the final commit.

- [ ] **Step 5: Audit the approved scope**

Run:

```bash
git submodule status
rg -n --hidden -g '!.git/**' -g '!hermes-webui/**' \
  'open-webui|openwebui|OpenWebUI|localhost:11001|runner/stack\.sh|compose\.yaml' .
```

Expected: only `hermes-webui` is a submodule. Matches outside the submodule are
limited to the 2026-09-09 design/plan's historical cleanup description; no
runtime, test, Makefile, or README dependency remains.

- [ ] **Step 6: Commit documentation and final contracts**

```bash
git add README.md tests/test_repository_layout.sh
git commit -m "docs: document native Hermes WebUI launch modes"
```

- [ ] **Step 7: Re-run final verification after the commit**

Run:

```bash
make test
git diff --check HEAD~4..HEAD
git status --short
```

Expected: all tests PASS, diff check is silent, and the worktree is clean apart
from ignored generated `Hermes WebUI.app` contents.
