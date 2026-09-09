#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
CONTROLLER="$ROOT/runner/hermes-webui-app.sh"
TEST_TMP="$(mktemp -d)"
TEST_TMP="$(cd "$TEST_TMP" && pwd -P)"
typeset -a children=()
cleanup() {
  local pid
  if [[ -f "$TEST_TMP/spawned.pids" ]]; then
    for pid in ${(f)"$(<"$TEST_TMP/spawned.pids")"}; do
      /bin/kill "$pid" 2>/dev/null || true
    done
  fi
  for pid in "${children[@]}"; do /bin/kill "$pid" 2>/dev/null || true; done
  /bin/rm -rf "$TEST_TMP"
}
trap cleanup EXIT
fail() { print -u2 -- "FAIL: $*"; exit 1; }
assert_contains() { [[ "$1" == *"$2"* ]] || fail "expected <$2> in <$1>"; }
pass() { print -- "PASS: $*"; }
[[ -x "$CONTROLLER" ]] || fail "native app controller is missing"

mkdir -p "$TEST_TMP/bin" "$TEST_TMP/fake checkout/scripts/lib" "$TEST_TMP/home"
cat > "$TEST_TMP/fake checkout/start.sh" <<'FAKE_START'
#!/bin/zsh
set -euo pipefail
print -r -- "$*" >> "$FAKE_START_CALLS"
print -r -- "$$" >> "$FAKE_SPAWNED_PIDS"
print -r -- "$HERMES_WEBUI_PYTHON" > "$FAKE_PYTHON_CALL"
if [[ "${FAKE_START_MODE:-healthy}" == transition ]]; then
  exec /bin/zsh "${0:h}/bootstrap.py"
fi
if [[ "${FAKE_START_MODE:-healthy}" == exit ]]; then exit 7; fi
if [[ "${FAKE_START_MODE:-healthy}" == healthy ]]; then : > "$FAKE_HEALTHY"; fi
if [[ "${FAKE_START_MODE:-healthy}" == stubborn ]]; then trap '' TERM; else trap 'exit 0' TERM; fi
if [[ "${FAKE_START_MODE:-healthy}" == change-owner ]]; then
  trap 'print -r -- "/bin/sleep 100" > "$FAKE_PS_FILE"' TERM
fi
while true; do /bin/sleep 0.05; done
FAKE_START
cat > "$TEST_TMP/bin/curl" <<'FAKE_CURL'
#!/bin/zsh
print -r -- "$*" >> "$FAKE_CURL_CALLS"
[[ -f "$FAKE_HEALTHY" ]]
FAKE_CURL
cat > "$TEST_TMP/bin/ps" <<'FAKE_PS'
#!/bin/zsh
if [[ -n "${FAKE_PS_OUTPUT+x}" ]]; then
  print -r -- "$FAKE_PS_OUTPUT"
elif [[ -f "$FAKE_PS_FILE" ]]; then
  /bin/cat "$FAKE_PS_FILE"
else
  exec /bin/ps "$@"
fi
FAKE_PS
cat > "$TEST_TMP/bin/sleep" <<'FAKE_SLEEP'
#!/bin/zsh
exec /bin/sleep 0.05
FAKE_SLEEP
cat > "$TEST_TMP/fake checkout/scripts/lib/health_probe.sh" <<'FAKE_PROBE'
hermes_webui_probe_health() {
  printf '%s\n' "$*" >> "$FAKE_SHARED_PROBE_CALLS"
  [[ -f "$FAKE_HEALTHY" ]]
}
FAKE_PROBE
cat > "$TEST_TMP/fake checkout/bootstrap.py" <<'FAKE_BOOTSTRAP'
#!/bin/zsh
exec /bin/zsh "${0:h}/server.py"
FAKE_BOOTSTRAP
cat > "$TEST_TMP/fake checkout/server.py" <<'FAKE_SERVER'
#!/bin/zsh
print -r -- "$$" > "$FAKE_SERVER_PID"
: > "$FAKE_HEALTHY"
trap 'exit 0' TERM
while true; do /bin/sleep 0.05; done
FAKE_SERVER
chmod +x "$TEST_TMP/fake checkout/start.sh" "$TEST_TMP/bin/"*

export HERMES_WEBUI_DIR="$TEST_TMP/fake checkout"
export HERMES_WEBUI_USER_HOME="$TEST_TMP/home"
export HERMES_WEBUI_PYTHON=/usr/bin/true
export HERMES_WEBUI_HOST=127.0.0.1 HERMES_WEBUI_PORT=8787
export HERMES_WEBUI_APP_PID_FILE="$TEST_TMP/state/app.pid"
export HERMES_WEBUI_APP_LOG_FILE="$TEST_TMP/logs/app.log"
export HERMES_WEBUI_APP_START_TIMEOUT=10 HERMES_WEBUI_APP_STOP_TIMEOUT=10
export HERMES_WEBUI_APP_PS_BIN="$TEST_TMP/bin/ps"
export HERMES_WEBUI_APP_CURL_BIN="$TEST_TMP/bin/curl"
export HERMES_WEBUI_APP_SLEEP_BIN="$TEST_TMP/bin/sleep"
export FAKE_START_CALLS="$TEST_TMP/start.calls" FAKE_SPAWNED_PIDS="$TEST_TMP/spawned.pids"
export FAKE_PYTHON_CALL="$TEST_TMP/python.call" FAKE_CURL_CALLS="$TEST_TMP/curl.calls"
export FAKE_HEALTHY="$TEST_TMP/healthy" FAKE_PS_FILE="$TEST_TMP/ps.output"
export FAKE_SHARED_PROBE_CALLS="$TEST_TMP/shared-probe.calls"
export FAKE_SERVER_PID="$TEST_TMP/server.pid"
run_controller() { "$CONTROLLER" "$@"; }
reset_case() {
  unset FAKE_PS_OUTPUT FAKE_START_MODE
  rm -f "$HERMES_WEBUI_APP_PID_FILE" "$HERMES_WEBUI_APP_LOG_FILE" "$FAKE_HEALTHY" \
    "$FAKE_START_CALLS" "$FAKE_CURL_CALLS" "$FAKE_PS_FILE" "$FAKE_SHARED_PROBE_CALLS"
}
expect_failure() {
  local code=0
  output=$(run_controller "$@" 2>&1) || code=$?
  (( code != 0 )) || fail "$* unexpectedly succeeded"
}
assert_dead() {
  local pid="$1" attempt
  for attempt in {1..40}; do
    /bin/kill -0 "$pid" 2>/dev/null || return 0
    /bin/sleep 0.05
  done
  fail "PID $pid is still alive"
}

# Catches missing spawn/persistence, incorrect argv/environment, duplicate spawn,
# skipped ownership reattachment, missing TERM/state cleanup, and unsafe modes.
reset_case
output=$(run_controller start)
assert_contains "$output" "Started Hermes WebUI"
[[ -s "$HERMES_WEBUI_APP_PID_FILE" ]] || fail "start must persist a PID"
assert_contains "$(<"$FAKE_START_CALLS")" "--foreground --no-browser --host 127.0.0.1 8787"
[[ "$(<"$FAKE_PYTHON_CALL")" == /usr/bin/true ]] || fail "Python override not forwarded"
assert_contains "$(<"$FAKE_CURL_CALLS")" "--fail --silent --show-error --max-time 2 http://127.0.0.1:8787/health"
first_pid="$(<"$HERMES_WEBUI_APP_PID_FILE")"
run_controller start >/dev/null
[[ "$(<"$HERMES_WEBUI_APP_PID_FILE")" == "$first_pid" ]] || fail "start must reattach instead of spawning a duplicate"
[[ $(wc -l < "$FAKE_START_CALLS") -eq 1 ]] || fail "start spawned a duplicate"
assert_contains "$(run_controller status)" "owned and running"
for file in "$HERMES_WEBUI_APP_PID_FILE" "$HERMES_WEBUI_APP_LOG_FILE"; do
  [[ "$(stat -f %Lp "$file")" == 600 ]] || fail "$file must be mode 600"
done
for dir in "$TEST_TMP/state" "$TEST_TMP/logs"; do
  [[ "$(stat -f %Lp "$dir")" == 700 ]] || fail "$dir must be mode 700"
done
run_controller stop >/dev/null
assert_dead "$first_pid"
[[ ! -e "$HERMES_WEBUI_APP_PID_FILE" ]] || fail "stop must clear owned state"
pass "start, reattach, owned status, stop, argv, and private state"

# Real upstream foreground bootstrap execs server.py, retaining the original PID.
# Rejecting that final script would make both readiness and subsequent stop fail.
reset_case
export FAKE_START_MODE=transition
output=$(run_controller start)
assert_contains "$output" "Started Hermes WebUI"
server_pid="$(<"$HERMES_WEBUI_APP_PID_FILE")"
[[ "$(<"$FAKE_SERVER_PID")" == "$server_pid" ]] || fail "foreground exec must retain app PID"
assert_contains "$(run_controller status)" "owned and running"
run_controller start >/dev/null
[[ $(wc -l < "$FAKE_START_CALLS") -eq 1 ]] || fail "server.py reattachment must not spawn"
run_controller stop >/dev/null
assert_dead "$server_pid"
pass "foreground exec through bootstrap.py to server.py retains ownership"

# A saved PID is authoritative only while numeric, live and owned.
for bad_pid in nonsense 0 -1 '12 34' $'12\n34' 99999999; do
  reset_case
  print -r -- "$bad_pid" > "$HERMES_WEBUI_APP_PID_FILE"
  assert_contains "$(run_controller status)" stopped
  [[ ! -e "$HERMES_WEBUI_APP_PID_FILE" ]] || fail "malformed/stale PID must be removed"
  run_controller stop >/dev/null
done
pass "malformed/stale cleanup and idempotent stop"

reset_case
export FAKE_START_MODE=unhealthy
expect_failure start
assert_contains "$output" "did not become healthy"
assert_dead "$(tail -1 "$FAKE_SPAWNED_PIDS")"
[[ ! -e "$HERMES_WEBUI_APP_PID_FILE" ]] || fail "timeout must clear local state"
pass "startup timeout terminates its owned process"

reset_case
export FAKE_START_MODE=exit
expect_failure start
assert_dead "$(tail -1 "$FAKE_SPAWNED_PIDS")"
[[ ! -e "$HERMES_WEBUI_APP_PID_FILE" ]] || fail "early exit must clear state"
pass "early child exit fails startup"

reset_case
: > "$FAKE_HEALTHY"
expect_failure start
assert_contains "$output" "another server is already responding"
[[ ! -e "$FAKE_START_CALLS" && ! -e "$HERMES_WEBUI_APP_PID_FILE" ]] || fail "external responder must not be claimed"
assert_contains "$(run_controller status)" "external server responding"
run_controller stop >/dev/null
pass "external responder refusal and status"

reset_case
/bin/sleep 100 &
external_pid=$!
children+=("$external_pid")
for command in '/bin/sleep 100' "/usr/bin/printf %s $HERMES_WEBUI_DIR/start.sh" \
  "/bin/zsh -c echo $HERMES_WEBUI_DIR/start.sh" "/bin/zsh $HERMES_WEBUI_DIR/start.sh.other" \
  "/bin/zsh $TEST_TMP/other-checkout/start.sh"; do
  reset_case
  print -r -- "$external_pid" > "$HERMES_WEBUI_APP_PID_FILE"
  export FAKE_PS_OUTPUT="$command"
  expect_failure stop
  assert_contains "$output" "refusing to stop unowned PID"
  /bin/kill -0 "$external_pid" || fail "stop signaled an unowned PID"
  expect_failure start
  [[ "$(<"$HERMES_WEBUI_APP_PID_FILE")" == "$external_pid" ]] || fail "unsafe PID must be preserved"
  [[ ! -e "$FAKE_START_CALLS" ]] || fail "unsafe PID must prevent spawning"
  expect_failure status
  assert_contains "$output" "unsafe PID state"
done
pass "unowned PID preserved and never signaled (including incidental path mentions)"

# Exercise the normal shared-probe branch without network or real user state.
reset_case
: > "$FAKE_HEALTHY"
output=$(HERMES_WEBUI_APP_CURL_BIN='' run_controller status)
assert_contains "$output" "external server responding"
assert_contains "$(<"$FAKE_SHARED_PROBE_CALLS")" "127.0.0.1 8787 /health 2 direct"
pass "normal health check uses shared helper"

reset_case
export FAKE_START_MODE=stubborn
# Start this fixture explicitly, so its health can be controlled by the test.
HERMES_WEBUI_PYTHON=/usr/bin/true "$HERMES_WEBUI_DIR/start.sh" >/dev/null 2>&1 &
stubborn_pid=$!
children+=("$stubborn_pid")
/bin/sleep 0.1
print -r -- "$stubborn_pid" > "$HERMES_WEBUI_APP_PID_FILE"
run_controller stop >/dev/null
assert_dead "$stubborn_pid"
pass "stop escalates for a still-owned TERM-resistant process"

reset_case
export FAKE_START_MODE=change-owner
HERMES_WEBUI_PYTHON=/usr/bin/true "$HERMES_WEBUI_DIR/start.sh" >/dev/null 2>&1 &
changed_pid=$!
children+=("$changed_pid")
/bin/sleep 0.1
print -r -- "$changed_pid" > "$HERMES_WEBUI_APP_PID_FILE"
expect_failure stop
/bin/kill -0 "$changed_pid" || fail "stop KILLed a PID after ownership changed"
[[ "$(<"$HERMES_WEBUI_APP_PID_FILE")" == "$changed_pid" ]] || fail "changed ownership state must remain"
# Reset its trap by KILL only in test cleanup: this is a directly created fixture.
/bin/kill -KILL "$changed_pid"
pass "ownership is rechecked before escalation"

reset_case
for invalid in 0 65536 -1 abc '1+1' 999999999999999999999999999999; do
  expect_failure_with_port=0
  HERMES_WEBUI_PORT="$invalid" run_controller status >/dev/null 2>&1 || expect_failure_with_port=$?
  (( expect_failure_with_port != 0 )) || fail "invalid port $invalid accepted"
done
for name in HERMES_WEBUI_APP_START_TIMEOUT HERMES_WEBUI_APP_STOP_TIMEOUT; do
  for invalid in 0 -1 abc 3601 999999999999999999999999999999; do
    if env "$name=$invalid" "$CONTROLLER" status >/dev/null 2>&1; then fail "invalid timeout accepted"; fi
  done
done
for name in HERMES_WEBUI_APP_PS_BIN HERMES_WEBUI_APP_CURL_BIN HERMES_WEBUI_APP_SLEEP_BIN HERMES_WEBUI_PYTHON; do
  if env "$name=$TEST_TMP/missing" "$CONTROLLER" start >/dev/null 2>&1; then fail "missing executable accepted"; fi
done
for command in unexpected ''; do
  code=0
  run_controller "$command" >/dev/null 2>&1 || code=$?
  [[ "$code" == 64 ]] || fail "unknown command must exit 64"
done
assert_contains "$(run_controller status)" stopped
pass "configuration validation, usage exit 64, and stopped status"
