# Hermes WebUI Runner Implementation Plan

> Historical implementation plan. The completed runner was later migrated
> into the `hermes-tools` Git repository; paths below reflect its current
> location, while the original no-Git execution notes are retained as history.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a native stay-open macOS application that starts the complete Hermes WebUI Compose stack, opens OpenWebUI when healthy, and stops the stack on normal application Quit.

**Architecture:** A testable Zsh lifecycle controller owns Docker discovery, Docker Desktop startup, Compose, health checks, logging, and secret-safe errors. A thin AppleScript app delegates lifecycle operations to that controller. A reproducible build script compiles and signs the app bundle and installs the existing Hermes icon.

**Tech Stack:** macOS 26, AppleScript via /usr/bin/osacompile, Zsh, Docker Desktop and Docker Compose v2, curl, shell tests with injected fake executables.

**Spec:** /Users/frenzy/dev/hermes/hermes-tools/docs/superpowers/specs/2026-09-03-hermes-webui-runner-design.md

## Global Constraints

- Build /Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app.
- Read API_SERVER_KEY only from /Users/frenzy/.hermes/.env; never evaluate that file as shell and never print the secret.
- Wait at most 120 seconds for Docker and 120 seconds for OpenWebUI.
- Stop with docker compose stop; never use down, rm, or --volumes.
- Manage every service declared by the Compose project, including the future bridge.
- Leave Docker Desktop running on app exit.
- Accept that Force Quit, kill -9, and system crashes bypass AppleScript cleanup.
- The original workspace was not a Git repository, so its implementation used
  recorded verification checkpoints instead of commits.

## File Structure

- Modify /Users/frenzy/dev/hermes/hermes-tools/compose.yaml: remove the literal key.
- Create /Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh: start, stop, and status commands.
- Create /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_stack.sh: isolated lifecycle tests.
- Create /Users/frenzy/dev/hermes/hermes-tools/runner/HermesWebUIRunner.applescript: Dock lifecycle handlers.
- Create /Users/frenzy/dev/hermes/hermes-tools/runner/build-app.sh: deterministic bundle build.
- Create /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_app_bundle.sh: source and bundle checks.
- Generate /Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app.

---

### Task 1: Externalize the key and build the shell lifecycle controller

**Files:**

- Modify: /Users/frenzy/dev/hermes/hermes-tools/compose.yaml
- Create: /Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh
- Create: /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_stack.sh

**Interfaces:**

- Consumes: API_SERVER_KEY assignment in /Users/frenzy/.hermes/.env and Docker Compose v2.
- Produces: stack.sh start, stack.sh stop, and stack.sh status; exit 0 means success.
- Test overrides: HERMES_WEBUI_PROJECT_DIR, HERMES_WEBUI_ENV_FILE, HERMES_WEBUI_URL, HERMES_WEBUI_DOCKER_BIN, HERMES_WEBUI_OPEN_BIN, HERMES_WEBUI_CURL_BIN, HERMES_WEBUI_SLEEP_BIN, HERMES_WEBUI_DOCKER_TIMEOUT, and HERMES_WEBUI_HEALTH_TIMEOUT.

- [ ] **Step 1: Write the failing shell contract test**

Create runner/tests/test_stack.sh with temporary files and an initial failure check:

~~~zsh
#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
STACK="$ROOT/runner/stack.sh"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

fail() { print -u2 -- "FAIL: $*"; exit 1; }
assert_contains() { [[ "$1" == *"$2"* ]] || fail "expected <$2> in <$1>"; }
assert_not_contains() { [[ "$1" != *"$2"* ]] || fail "unexpected <$2> in <$1>"; }

grep -Eq 'OPENAI_API_KEY=[[:alnum:]]{16,}' "$ROOT/compose.yaml" &&
  fail "compose.yaml contains a literal API key"

mkdir -p "$TEST_TMP/project"
cp "$ROOT/compose.yaml" "$TEST_TMP/project/compose.yaml"
: > "$TEST_TMP/empty.env"

set +e
output=$(HERMES_WEBUI_PROJECT_DIR="$TEST_TMP/project" \
  HERMES_WEBUI_ENV_FILE="$TEST_TMP/empty.env" \
  "$STACK" status 2>&1)
code=$?
set -e
[[ $code -ne 0 ]] || fail "missing API_SERVER_KEY must fail"
assert_contains "$output" "API_SERVER_KEY"
assert_not_contains "$output" "Authorization: Bearer"
~~~

- [ ] **Step 2: Run the test and verify it fails**

Run:

~~~bash
/bin/zsh /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_stack.sh
~~~

Expected: failure because stack.sh does not exist and Compose still embeds the key.

- [ ] **Step 3: Replace the literal Compose key**

Use mapping syntax in the OpenWebUI service:

~~~yaml
environment:
  OPENAI_API_BASE_URL: http://host.docker.internal:8642/v1
  OPENAI_API_KEY: ${API_SERVER_KEY:?API_SERVER_KEY is required}
  ENABLE_OLLAMA_API: "false"
~~~

Keep the existing image, port 11001, volume, extra_hosts, and restart policy unchanged.

- [ ] **Step 4: Implement paths, secret validation, and command dispatch**

Create runner/stack.sh beginning with:

~~~zsh
#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${HERMES_WEBUI_PROJECT_DIR:-/Users/frenzy/dev/hermes/hermes-tools}"
ENV_FILE="${HERMES_WEBUI_ENV_FILE:-/Users/frenzy/.hermes/.env}"
COMPOSE_FILE="$PROJECT_DIR/compose.yaml"
WEBUI_URL="${HERMES_WEBUI_URL:-http://localhost:11001}"
RUNNER_LOG="${HERMES_WEBUI_LOG_FILE:-$PROJECT_DIR/runner/runner.log}"
DOCKER_TIMEOUT="${HERMES_WEBUI_DOCKER_TIMEOUT:-120}"
HEALTH_TIMEOUT="${HERMES_WEBUI_HEALTH_TIMEOUT:-120}"

die() { print -u2 -- "$1"; return 1; }

validate_secret_source() {
  [[ -f "$ENV_FILE" ]] || die "Hermes environment file not found: $ENV_FILE"
  /usr/bin/awk '
    /^API_SERVER_KEY=/ {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]"]+|[[:space:]"]+$/, "", value)
      if (length(value) > 0) found = 1
    }
    END { exit(found ? 0 : 1) }
  ' "$ENV_FILE" || die "API_SERVER_KEY is missing or empty in $ENV_FILE"
}
~~~

Resolve Docker from HERMES_WEBUI_DOCKER_BIN, /usr/local/bin/docker, /opt/homebrew/bin/docker, then controlled PATH /opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin. Resolve open, curl, and sleep through their test overrides or /usr/bin/open, /usr/bin/curl, and /bin/sleep. Reject missing or unknown actions with exit 64 and usage text.

- [ ] **Step 5: Extend the test with fake Docker, open, curl, and sleep**

Create executable fakes under $TEST_TMP/bin. The Docker fake must append every argument list to $FAKE_CALLS, fail docker info for $FAKE_DOCKER_INFO_FAILURES attempts, return open-webui and bridge for compose config --services, and return configurable running services for compose ps --services --status running. The curl fake returns $FAKE_CURL_EXIT. The open fake only records arguments. The sleep fake exits immediately.

Use this fake-command pattern, expanding the Docker branches for the two Compose queries:

~~~zsh
mkdir -p "$TEST_TMP/bin"
cat > "$TEST_TMP/bin/docker" <<'FAKE_DOCKER'
#!/bin/zsh
print -r -- "$*" >> "$FAKE_CALLS"
if [[ "$1" == "info" ]]; then
  count=$(cat "$FAKE_DOCKER_COUNTER" 2>/dev/null || print 0)
  print $((count + 1)) > "$FAKE_DOCKER_COUNTER"
  (( count >= ${FAKE_DOCKER_INFO_FAILURES:-0} ))
  exit $?
fi
if [[ "$*" == *"config --services"* ]]; then
  print -r -- "${FAKE_DECLARED_SERVICES:-open-webui}"
  exit 0
fi
if [[ "$*" == *"ps --services --status running"* ]]; then
  print -r -- "${FAKE_RUNNING_SERVICES:-open-webui}"
  exit 0
fi
exit 0
FAKE_DOCKER

cat > "$TEST_TMP/bin/open" <<'FAKE_OPEN'
#!/bin/zsh
print -r -- "$*" >> "$FAKE_CALLS"
FAKE_OPEN

cat > "$TEST_TMP/bin/curl" <<'FAKE_CURL'
#!/bin/zsh
print -r -- "$*" >> "$FAKE_CALLS"
exit ${FAKE_CURL_EXIT:-0}
FAKE_CURL

cat > "$TEST_TMP/bin/sleep" <<'FAKE_SLEEP'
#!/bin/zsh
exit 0
FAKE_SLEEP
chmod +x "$TEST_TMP/bin/"*
~~~

Add cases asserting:

- start calls open -a Docker after an initial docker info failure;
- start invokes compose with --env-file, the exact Compose file, up -d --remove-orphans;
- start polls http://localhost:11001/health;
- stop invokes compose stop and never uses down, rm, or --volumes;
- status fails when one declared service is missing from the running list;
- stdout, stderr, and runner.log never contain test-secret-never-log.

- [ ] **Step 6: Implement bounded lifecycle behavior**

Implement wait_for_docker with one initial docker info probe, one open -a Docker call when unavailable, and one-second polling bounded by DOCKER_TIMEOUT. Implement wait_for_webui with:

~~~zsh
"$CURL_BIN" --fail --silent --show-error --max-time 2 "$WEBUI_URL/health"
~~~

Implement the public operations:

~~~zsh
start_stack() {
  validate_secret_source
  wait_for_docker
  run_logged compose up -d --remove-orphans
  wait_for_webui
}

stop_stack() {
  validate_secret_source
  "$DOCKER_BIN" info >/dev/null 2>&1 ||
    die "Docker daemon is not available; the Hermes WebUI stack could not be stopped."
  run_logged compose stop
}
~~~

status_stack must compare sorted, non-empty compose config --services output with compose ps --services --status running output. run_logged must capture command output in a temporary file, preserve the original exit code, redact API_SERVER_KEY, OPENAI_API_KEY, and Authorization: Bearer values into runner.log, then remove the temporary file.

Create the runner directory with mode 700 and runner.log with mode 600.

- [ ] **Step 7: Run shell verification**

Run:

~~~bash
/bin/zsh -n /Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh
/bin/zsh /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_stack.sh
docker compose --env-file /Users/frenzy/.hermes/.env \
  -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml config --services
~~~

Expected: shell tests pass and Compose prints open-webui. Do not run bare docker compose config because it can render the secret.

- [ ] **Step 8: Record the Task 1 checkpoint**

Record the successful syntax, test, and Compose service-list commands in the execution notes. Do not initialize Git.

---

### Task 2: Build and verify the stay-open AppleScript app

**Files:**

- Create: /Users/frenzy/dev/hermes/hermes-tools/runner/HermesWebUIRunner.applescript
- Create: /Users/frenzy/dev/hermes/hermes-tools/runner/build-app.sh
- Create: /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_app_bundle.sh
- Generate: /Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app

**Interfaces:**

- Consumes: runner/stack.sh start, runner/stack.sh stop, and /Users/frenzy/.hermes/hermes-agent/apps/desktop/assets/icon.icns.
- Produces: a single-instance stay-open Dock app with run, reopen, idle, and quit handlers.

- [ ] **Step 1: Write failing app source and bundle tests**

Create runner/tests/test_app_bundle.sh:

~~~zsh
#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SOURCE="$ROOT/runner/HermesWebUIRunner.applescript"
BUILD="$ROOT/runner/build-app.sh"
APP="$ROOT/Hermes WebUI.app"

[[ -f "$SOURCE" ]] || { print -u2 -- "missing AppleScript source"; exit 1; }
[[ -x "$BUILD" ]] || { print -u2 -- "missing executable build script"; exit 1; }

for handler in 'on run' 'on reopen' 'on idle' 'on quit'; do
  grep -Fq "$handler" "$SOURCE" || { print -u2 -- "missing $handler"; exit 1; }
done

"$BUILD"
[[ -f "$APP/Contents/Resources/applet.icns" ]]
[[ "$(/usr/libexec/PlistBuddy -c 'Print :LSMultipleInstancesProhibited' "$APP/Contents/Info.plist")" == "true" ]]
/usr/bin/codesign --verify --deep --strict "$APP"
/usr/bin/osadecompile "$APP" | grep -Fq 'http://localhost:11001'
~~~

- [ ] **Step 2: Run the test and verify it fails**

Run:

~~~bash
/bin/zsh /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_app_bundle.sh
~~~

Expected: failure because the AppleScript source and build script do not exist.

- [ ] **Step 3: Implement AppleScript lifecycle handlers**

Create the source:

~~~applescript
property stackScript : "/Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh"
property webUIURL : "http://localhost:11001"

on run
  try
    do shell script quoted form of stackScript & " start"
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
    do shell script quoted form of stackScript & " stop"
  on error errorMessage number errorNumber
    display dialog "Hermes WebUI stack could not be stopped:" & return & errorMessage buttons {"OK"} default button "OK" with icon caution
  end try
  continue quit
end quit
~~~

The quit handler performs cleanup both for normal Quit and startup failure. Do not add a second stop call inside run.

- [ ] **Step 4: Implement the deterministic build script**

Create executable runner/build-app.sh. It must validate source and icon paths, validate that the output is exactly /Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app, remove only that exact previous app bundle, then run:

~~~bash
/usr/bin/osacompile -s -o "$APP" "$SOURCE"
/bin/cp "$ICON" "$APP/Contents/Resources/applet.icns"
/usr/libexec/PlistBuddy -c 'Set :CFBundleDisplayName Hermes WebUI' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier local.hermes.webui-runner' "$APP/Contents/Info.plist"
~~~

Use PlistBuddy Add commands when LS keys are absent and Set commands when present so LSMultipleInstancesProhibited is boolean true and LSUIElement is boolean false. Finish with:

~~~bash
/usr/bin/codesign --force --deep --sign - "$APP"
~~~

- [ ] **Step 5: Compile and verify the app**

Run:

~~~bash
chmod +x /Users/frenzy/dev/hermes/hermes-tools/runner/build-app.sh
chmod +x /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_app_bundle.sh
/bin/zsh /Users/frenzy/dev/hermes/hermes-tools/runner/tests/test_app_bundle.sh
~~~

Expected: compilation, icon, single-instance metadata, decompilation, and strict ad-hoc signature checks pass.

- [ ] **Step 6: Record the Task 2 checkpoint**

Record the successful bundle test. Do not initialize Git.

---

### Task 3: Verify the real stack and persistent data

**Files:**

- Verify: /Users/frenzy/dev/hermes/hermes-tools/compose.yaml
- Verify: /Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app
- Inspect: Docker volume hermes_open-webui

**Interfaces:**

- Consumes: the actual Hermes key, Docker Desktop, the current OpenWebUI container, and its named volume.
- Produces: evidence for health, Hermes model access, app start/reopen/quit, complete-stack stop, Docker continuity, and chat persistence.

- [ ] **Step 1: Capture non-secret pre-test state**

Run:

~~~bash
docker volume inspect hermes_open-webui --format '{{.Name}}'
docker compose --env-file /Users/frenzy/.hermes/.env \
  -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml config --services
docker compose --env-file /Users/frenzy/.hermes/.env \
  -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml ps
~~~

Record the volume name, service list, and current states.

- [ ] **Step 2: Recreate OpenWebUI with the externalized key**

Run:

~~~bash
docker compose --env-file /Users/frenzy/.hermes/.env \
  -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml up -d --force-recreate
curl --fail --silent http://localhost:11001/health >/dev/null
~~~

Expected: OpenWebUI becomes healthy and reuses hermes_open-webui.

- [ ] **Step 3: Verify effective Hermes connectivity**

In the authenticated OpenWebUI page, verify that hermes-agent appears in the model picker. If the stored OpenWebUI connection still contains the stale credential, update that connection through Admin Settings using the active value from /Users/frenzy/.hermes/.env. Do not delete or edit the OpenWebUI database directly and do not delete the volume.

- [ ] **Step 4: Verify app start and reopen**

Stop the stack with runner/stack.sh stop, then run:

~~~bash
open '/Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app'
~~~

Expected: the app stays in the Dock, all services from compose config --services become running, and the browser opens http://localhost:11001. Click the Dock icon again; the URL reopens and no second Runner process appears.

- [ ] **Step 5: Verify normal Quit**

Run:

~~~bash
osascript -e 'tell application id "local.hermes.webui-runner" to quit'
docker compose --env-file /Users/frenzy/.hermes/.env \
  -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml ps --services --status running
docker info >/dev/null
docker volume inspect hermes_open-webui --format '{{.Name}}'
~~~

Expected: the Compose service list is empty, Docker still responds, and the volume still exists.

- [ ] **Step 6: Verify chat persistence**

Open the Runner app again, sign in if needed, and confirm that at least one pre-existing chat remains listed. Quit normally after the check.

- [ ] **Step 7: Record final verification**

Record results for shell tests, app compilation, health, model visibility, start/reopen/quit, complete-stack stop, Docker daemon continuity, and chat persistence. Do not initialize Git.
