#!/bin/zsh
set -euo pipefail
zmodload zsh/datetime

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

set +e
"$STACK" >"$TEST_TMP/missing-action.stdout" 2>"$TEST_TMP/missing-action.stderr"
code=$?
set -e
[[ $code -eq 64 ]] || fail "missing action must exit 64"
assert_contains "$(<"$TEST_TMP/missing-action.stderr")" "Usage:"

set +e
"$STACK" restart >"$TEST_TMP/unknown-action.stdout" 2>"$TEST_TMP/unknown-action.stderr"
code=$?
set -e
[[ $code -eq 64 ]] || fail "unknown action must exit 64"
assert_contains "$(<"$TEST_TMP/unknown-action.stderr")" "Usage:"

mkdir -p "$TEST_TMP/bin"
cat > "$TEST_TMP/bin/docker" <<'FAKE_DOCKER'
#!/bin/zsh
print -r -- "$*" >> "$FAKE_CALLS"
if [[ "$1" == "info" ]]; then
  if [[ -n "${FAKE_DOCKER_INFO_DELAY:-}" ]]; then
    /bin/sleep "$FAKE_DOCKER_INFO_DELAY"
  fi
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
if [[ "$*" == *"up -d --remove-orphans"* && -n "${FAKE_COMPOSE_EXIT:-}" ]]; then
  print -r -- "OPENAI_API_KEY=$TEST_SECRET"
  print -r -- "OPENAI_API_KEY: $TEST_SECRET"
  print -r -- "API_SERVER_KEY: $TEST_SECRET"
  print -r -- "Authorization: Bearer $TEST_SECRET"
  print -r -- "{\"OPENAI_API_KEY\": \"$TEST_SECRET\"}"
  print -r -- "{\"API_SERVER_KEY\": \"$TEST_SECRET\"}"
  print -r -- "{\"Authorization\": \"Bearer $TEST_SECRET\"}"
  exit "$FAKE_COMPOSE_EXIT"
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
if [[ -n "${FAKE_CURL_DELAY:-}" ]]; then
  /bin/sleep "$FAKE_CURL_DELAY"
fi
exit ${FAKE_CURL_EXIT:-0}
FAKE_CURL

cat > "$TEST_TMP/bin/sleep" <<'FAKE_SLEEP'
#!/bin/zsh
exit 0
FAKE_SLEEP
chmod +x "$TEST_TMP/bin/"*

TEST_SECRET="test-secret-never-log"
export TEST_SECRET
print -r -- "API_SERVER_KEY=$TEST_SECRET" > "$TEST_TMP/hermes.env"
FAKE_CALLS="$TEST_TMP/calls"
FAKE_DOCKER_COUNTER="$TEST_TMP/docker-counter"
export FAKE_CALLS FAKE_DOCKER_COUNTER
export HERMES_WEBUI_PROJECT_DIR="$TEST_TMP/project"
export HERMES_WEBUI_ENV_FILE="$TEST_TMP/hermes.env"
export HERMES_WEBUI_DOCKER_BIN="$TEST_TMP/bin/docker"
export HERMES_WEBUI_OPEN_BIN="$TEST_TMP/bin/open"
export HERMES_WEBUI_CURL_BIN="$TEST_TMP/bin/curl"
export HERMES_WEBUI_SLEEP_BIN="$TEST_TMP/bin/sleep"
export HERMES_WEBUI_DOCKER_TIMEOUT=2
export HERMES_WEBUI_HEALTH_TIMEOUT=2
export HERMES_WEBUI_LOG_FILE="$TEST_TMP/runner.log"

: > "$FAKE_CALLS"
print 0 > "$FAKE_DOCKER_COUNTER"
export FAKE_DOCKER_INFO_FAILURES=1
"$STACK" start >"$TEST_TMP/start.stdout" 2>"$TEST_TMP/start.stderr" ||
  fail "start should succeed with fake commands"
start_calls=$(<"$FAKE_CALLS")
assert_contains "$start_calls" "-a Docker"
assert_contains "$start_calls" "compose --env-file $TEST_TMP/hermes.env -f $TEST_TMP/project/compose.yaml up -d --remove-orphans"
assert_contains "$start_calls" "--fail --silent --show-error --max-time "
assert_contains "$start_calls" "http://localhost:11001/health"
assert_not_contains "$(<"$TEST_TMP/start.stdout")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/start.stderr")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/runner.log")" "$TEST_SECRET"

: > "$FAKE_CALLS"
export FAKE_DOCKER_INFO_FAILURES=0
"$STACK" stop >"$TEST_TMP/stop.stdout" 2>"$TEST_TMP/stop.stderr" ||
  fail "stop should succeed with fake commands"
stop_calls=$(<"$FAKE_CALLS")
assert_contains "$stop_calls" "compose --env-file $TEST_TMP/hermes.env -f $TEST_TMP/project/compose.yaml stop"
assert_not_contains "$stop_calls" " down"
assert_not_contains "$stop_calls" " rm"
assert_not_contains "$stop_calls" "--volumes"
assert_not_contains "$(<"$TEST_TMP/stop.stdout")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/stop.stderr")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/runner.log")" "$TEST_SECRET"

: > "$FAKE_CALLS"
export FAKE_DECLARED_SERVICES=$'open-webui\nbridge'
export FAKE_RUNNING_SERVICES="open-webui"
set +e
"$STACK" status >"$TEST_TMP/status.stdout" 2>"$TEST_TMP/status.stderr"
code=$?
set -e
[[ $code -ne 0 ]] || fail "status must fail when a declared service is not running"
assert_not_contains "$(<"$TEST_TMP/status.stdout")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/status.stderr")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/runner.log")" "$TEST_SECRET"

: > "$TEST_TMP/runner.log"
export FAKE_DECLARED_SERVICES="open-webui"
export FAKE_RUNNING_SERVICES="open-webui"
export FAKE_COMPOSE_EXIT=42
set +e
"$STACK" start >"$TEST_TMP/failed-start.stdout" 2>"$TEST_TMP/failed-start.stderr"
code=$?
set -e
unset FAKE_COMPOSE_EXIT
[[ $code -eq 42 ]] || fail "start must preserve a failed compose exit code"
failed_log=$(<"$TEST_TMP/runner.log")
assert_contains "$failed_log" "[REDACTED]"
assert_not_contains "$failed_log" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/failed-start.stdout")" "$TEST_SECRET"
assert_not_contains "$(<"$TEST_TMP/failed-start.stderr")" "$TEST_SECRET"

: > "$FAKE_CALLS"
export HERMES_WEBUI_HEALTH_TIMEOUT=1
export FAKE_CURL_EXIT=22
export FAKE_CURL_DELAY=2
set +e
"$STACK" start >"$TEST_TMP/slow-health.stdout" 2>"$TEST_TMP/slow-health.stderr"
code=$?
set -e
unset FAKE_CURL_EXIT FAKE_CURL_DELAY
export HERMES_WEBUI_HEALTH_TIMEOUT=2
[[ $code -ne 0 ]] || fail "start must fail when health checks exceed the deadline"
health_calls=$(rg -c --fixed-strings "http://localhost:11001/health" "$FAKE_CALLS" || true)
[[ "$health_calls" == "1" ]] || fail "health timeout must include curl duration; expected one probe, got $health_calls"

: > "$FAKE_CALLS"
print 0 > "$FAKE_DOCKER_COUNTER"
export HERMES_WEBUI_DOCKER_TIMEOUT=1
export FAKE_DOCKER_INFO_FAILURES=100
export FAKE_DOCKER_INFO_DELAY=3
started=$EPOCHREALTIME
set +e
"$STACK" start >"$TEST_TMP/slow-docker.stdout" 2>"$TEST_TMP/slow-docker.stderr"
code=$?
set -e
duration=$(( EPOCHREALTIME - started ))
unset FAKE_DOCKER_INFO_DELAY
export FAKE_DOCKER_INFO_FAILURES=0
export HERMES_WEBUI_DOCKER_TIMEOUT=2
[[ $code -ne 0 ]] || fail "start must fail when a docker info probe exceeds the deadline"
(( duration < 2.5 )) || fail "docker timeout must include docker info duration; elapsed ${duration}s"
assert_not_contains "$(<"$FAKE_CALLS")" "up -d --remove-orphans"
