#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
RESET="$ROOT/runner/reset-openwebui-history.sh"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

fail() { print -u2 -- "FAIL: $*"; exit 1; }
assert_contains() { [[ "$1" == *"$2"* ]] || fail "expected <$2> in <$1>"; }
assert_not_contains() { [[ "$1" != *"$2"* ]] || fail "unexpected <$2> in <$1>"; }
assert_before() { [[ "$1" == *"$2"*"$3"* ]] || fail "expected <$2> before <$3> in <$1>"; }

mkdir -p "$TEST_TMP/project" "$TEST_TMP/bin"
: > "$TEST_TMP/project/compose.yaml"
TEST_OPENWEBUI_SECRET="test-openwebui-secret-never-log"
print -r -- "OPENWEBUI_API_KEY=$TEST_OPENWEBUI_SECRET" > "$TEST_TMP/project/.env.local"
print -r -- "API_SERVER_KEY=fake" > "$TEST_TMP/hermes.env"

cat > "$TEST_TMP/bin/docker" <<'FAKE_DOCKER'
#!/bin/zsh
print -r -- "docker $*" >> "$FAKE_CALLS"
if [[ "$1" == "info" ]]; then
  exit ${FAKE_DOCKER_INFO_EXIT:-0}
fi
if [[ "$*" == *"run --rm --no-deps bridge-service python -m hermes_bridge.maintenance reset-openwebui-links --database /data/bridge.sqlite3"* ]]; then
  print -r -- "2"
  exit ${FAKE_MAINTENANCE_EXIT:-0}
fi
exit 0
FAKE_DOCKER

cat > "$TEST_TMP/bin/curl" <<'FAKE_CURL'
#!/bin/zsh
print -r -- "curl $*" >> "$FAKE_CALLS"
joined="$*"
if [[ "$joined" == *"/health/status"* ]]; then
  if [[ "${FAKE_QUEUE_BUSY:-0}" == "1" ]]; then
    print -r -- '{"status":"ok","queue":{"pending":1,"active":0,"uncertain":0,"failed":0},"last_scan":"2026-09-07T12:00:00Z","failures":[]}'
  else
    print -r -- '{"status":"ok","queue":{"pending":0,"active":0,"uncertain":0,"failed":0},"last_scan":"2026-09-07T12:00:00Z","failures":[]}'
  fi
  exit 0
fi
if [[ "$joined" == *"/health/ready"* ]]; then
  [[ "${FAKE_READY_EXIT:-0}" == "0" ]] || exit "$FAKE_READY_EXIT"
  print -r -- '{"status":"ready"}'
  exit 0
fi
if [[ "$joined" == *"/api/v1/folders/"* ]]; then
  print -r -- '[{"id":"folder-hermes","name":"Hermes","parent_id":null},{"id":"nested","name":"Hermes","parent_id":"other"}]'
  exit 0
fi
if [[ "$joined" == *"/api/v1/chats/folder/folder-hermes"* ]]; then
  print -r -- '[{"id":"mirror-one","variables":{"hermes_lineage_key":"local:default:one"}},{"id":"local-chat","variables":{}},{"id":"mirror-two","variables":{"hermes_lineage_key":"local:default:two"}}]'
  exit 0
fi
if [[ "$joined" == *"/api/v1/chats/mirror-one"* || "$joined" == *"/api/v1/chats/mirror-two"* ]]; then
  [[ "${FAKE_DELETE_EXIT:-0}" == "0" ]] || exit "$FAKE_DELETE_EXIT"
  print -r -- 'true'
  exit 0
fi
print -u2 -- "unexpected curl call"
exit 22
FAKE_CURL

cat > "$TEST_TMP/bin/sleep" <<'FAKE_SLEEP'
#!/bin/zsh
exit 0
FAKE_SLEEP
chmod +x "$TEST_TMP/bin/"*

FAKE_CALLS="$TEST_TMP/calls"
export FAKE_CALLS
export HERMES_WEBUI_PROJECT_DIR="$TEST_TMP/project"
export HERMES_WEBUI_ENV_FILE="$TEST_TMP/hermes.env"
export HERMES_WEBUI_LOCAL_ENV_FILE="$TEST_TMP/project/.env.local"
export HERMES_WEBUI_DOCKER_BIN="$TEST_TMP/bin/docker"
export HERMES_WEBUI_CURL_BIN="$TEST_TMP/bin/curl"
export HERMES_WEBUI_JQ_BIN="/usr/bin/jq"
export HERMES_WEBUI_SLEEP_BIN="$TEST_TMP/bin/sleep"
export HERMES_WEBUI_HEALTH_TIMEOUT=2

: > "$FAKE_CALLS"
"$RESET" --yes > "$TEST_TMP/success.stdout" 2> "$TEST_TMP/success.stderr" ||
  fail "confirmed reset should succeed"
calls=$(<"$FAKE_CALLS")
compose_prefix="docker compose --env-file $TEST_TMP/hermes.env --env-file $TEST_TMP/project/.env.local -f $TEST_TMP/project/compose.yaml"
assert_contains "$calls" "$compose_prefix build bridge-service"
assert_contains "$calls" "$compose_prefix stop bridge-service"
assert_contains "$calls" "/api/v1/chats/mirror-one"
assert_contains "$calls" "/api/v1/chats/mirror-two"
assert_not_contains "$calls" "/api/v1/chats/local-chat"
assert_contains "$calls" "$compose_prefix run --rm --no-deps bridge-service python -m hermes_bridge.maintenance reset-openwebui-links --database /data/bridge.sqlite3"
assert_contains "$calls" "$compose_prefix start bridge-service"
assert_before "$calls" "stop bridge-service" "/api/v1/chats/mirror-one"
assert_before "$calls" "build bridge-service" "stop bridge-service"
assert_before "$calls" "/api/v1/chats/mirror-two" "reset-openwebui-links"
assert_before "$calls" "reset-openwebui-links" "start bridge-service"
assert_contains "$(<"$TEST_TMP/success.stdout")" "2"
for output in "$TEST_TMP/success.stdout" "$TEST_TMP/success.stderr"; do
  assert_not_contains "$(<"$output")" "$TEST_OPENWEBUI_SECRET"
done

: > "$FAKE_CALLS"
export FAKE_QUEUE_BUSY=1
set +e
"$RESET" --yes > "$TEST_TMP/busy.stdout" 2> "$TEST_TMP/busy.stderr"
code=$?
set -e
unset FAKE_QUEUE_BUSY
[[ $code -ne 0 ]] || fail "reset must reject a non-idle bridge queue"
busy_calls=$(<"$FAKE_CALLS")
assert_not_contains "$busy_calls" "stop bridge-service"
assert_not_contains "$busy_calls" "DELETE"
assert_contains "$(<"$TEST_TMP/busy.stderr")" "not idle"

: > "$FAKE_CALLS"
export FAKE_READY_EXIT=22
set +e
"$RESET" --yes > "$TEST_TMP/not-ready.stdout" 2> "$TEST_TMP/not-ready.stderr"
code=$?
set -e
unset FAKE_READY_EXIT
[[ $code -ne 0 ]] || fail "reset must reject a bridge that is not connector-ready"
not_ready_calls=$(<"$FAKE_CALLS")
assert_not_contains "$not_ready_calls" "stop bridge-service"
assert_not_contains "$not_ready_calls" "DELETE"

: > "$FAKE_CALLS"
export FAKE_DELETE_EXIT=22
set +e
"$RESET" --yes > "$TEST_TMP/delete-failure.stdout" 2> "$TEST_TMP/delete-failure.stderr"
code=$?
set -e
unset FAKE_DELETE_EXIT
[[ $code -ne 0 ]] || fail "a failed chat deletion must fail the reset"
failure_calls=$(<"$FAKE_CALLS")
assert_contains "$failure_calls" "stop bridge-service"
assert_contains "$failure_calls" "start bridge-service"

: > "$FAKE_CALLS"
set +e
print -r -- "NO" | "$RESET" > "$TEST_TMP/cancel.stdout" 2> "$TEST_TMP/cancel.stderr"
code=$?
set -e
[[ $code -ne 0 ]] || fail "incorrect confirmation must cancel"
cancel_calls=$(<"$FAKE_CALLS")
assert_not_contains "$cancel_calls" "stop bridge-service"
assert_not_contains "$cancel_calls" "DELETE"
