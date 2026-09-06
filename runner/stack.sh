#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${HERMES_WEBUI_PROJECT_DIR:-${SCRIPT_DIR:h}}"
HERMES_ENV_FILE="${HERMES_WEBUI_ENV_FILE:-/Users/frenzy/.hermes/.env}"
LOCAL_ENV_FILE="${HERMES_WEBUI_LOCAL_ENV_FILE:-$PROJECT_DIR/.env.local}"
COMPOSE_FILE="$PROJECT_DIR/compose.yaml"
WEBUI_URL="${HERMES_WEBUI_URL:-http://localhost:11001}"
RUNNER_LOG="${HERMES_WEBUI_LOG_FILE:-$PROJECT_DIR/runner/runner.log}"
DOCKER_TIMEOUT="${HERMES_WEBUI_DOCKER_TIMEOUT:-120}"
HEALTH_TIMEOUT="${HERMES_WEBUI_HEALTH_TIMEOUT:-120}"
COMPOSE_ENV_ARGS=(
  --env-file "$HERMES_ENV_FILE"
  --env-file "$LOCAL_ENV_FILE"
)

die() { print -u2 -- "$1"; return 1; }

validate_secret_source() {
  [[ -f "$HERMES_ENV_FILE" ]] || die "Hermes environment file not found: $HERMES_ENV_FILE"
  [[ -f "$LOCAL_ENV_FILE" ]] || die "Local environment file not found: $LOCAL_ENV_FILE"

  validate_assignment "$HERMES_ENV_FILE" API_SERVER_KEY 1 ||
    die "API_SERVER_KEY is missing or empty in $HERMES_ENV_FILE"
  validate_assignment "$LOCAL_ENV_FILE" OPENWEBUI_API_KEY 1 ||
    die "OPENWEBUI_API_KEY is missing or empty in $LOCAL_ENV_FILE"
  validate_assignment "$LOCAL_ENV_FILE" HERMES_BRIDGE_SECRET 32 ||
    die "HERMES_BRIDGE_SECRET is missing or shorter than 32 characters in $LOCAL_ENV_FILE"

  BRIDGE_HOST_PORT=$(/usr/bin/awk '
    /^[[:space:]]*BRIDGE_HOST_PORT[[:space:]]*=/ {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]"]+|[[:space:]"]+$/, "", value)
      found = 1
    }
    END {
      if (!found || value == "") value = "8787"
      port_number = value + 0
      if (value !~ /^[0-9]+$/ || port_number < 1 || port_number > 65535) exit 1
      print value
    }
  ' "$LOCAL_ENV_FILE") ||
    die "BRIDGE_HOST_PORT must be an integer from 1 to 65535 in $LOCAL_ENV_FILE"
  BRIDGE_URL="${HERMES_WEBUI_BRIDGE_URL:-http://127.0.0.1:$BRIDGE_HOST_PORT}"
}

validate_assignment() {
  local file="$1" key="$2" minimum_length="$3"
  /usr/bin/awk -v key="$key" -v minimum_length="$minimum_length" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]"]+|[[:space:]"]+$/, "", value)
      found = 1
    }
    END { exit(found && length(value) >= minimum_length ? 0 : 1) }
  ' "$file"
}

resolve_command() {
  local override="$1"
  local default_path="$2"
  local command_name="$3"
  local resolved

  if [[ -n "$override" ]]; then
    [[ -x "$override" ]] || die "$command_name executable not found: $override"
    print -r -- "$override"
    return 0
  fi
  if [[ -x "$default_path" ]]; then
    print -r -- "$default_path"
    return 0
  fi
  resolved=$(PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin command -v "$command_name") ||
    die "$command_name executable not found"
  print -r -- "$resolved"
}

initialize_commands() {
  DOCKER_BIN="$(resolve_command "${HERMES_WEBUI_DOCKER_BIN:-}" /usr/local/bin/docker docker)" || return 1
  OPEN_BIN="$(resolve_command "${HERMES_WEBUI_OPEN_BIN:-}" /usr/bin/open open)" || return 1
  CURL_BIN="$(resolve_command "${HERMES_WEBUI_CURL_BIN:-}" /usr/bin/curl curl)" || return 1
  SLEEP_BIN="$(resolve_command "${HERMES_WEBUI_SLEEP_BIN:-}" /bin/sleep sleep)" || return 1
}

prepare_log() {
  mkdir -p -m 700 "${RUNNER_LOG:h}" || return 1
  chmod 700 "${RUNNER_LOG:h}"
  touch "$RUNNER_LOG"
  chmod 600 "$RUNNER_LOG"
}

redact_sensitive() {
  /usr/bin/sed -E \
    -e 's/("(API_SERVER_KEY|OPENAI_API_KEY|OPENWEBUI_API_KEY|HERMES_BRIDGE_SECRET)"[[:space:]]*:[[:space:]]*")[^"]*"/\1[REDACTED]"/g' \
    -e 's/("Authorization"[[:space:]]*:[[:space:]]*"Bearer[[:space:]]+)[^"]*"/\1[REDACTED]"/g' \
    -e 's/(API_SERVER_KEY|OPENAI_API_KEY|OPENWEBUI_API_KEY|HERMES_BRIDGE_SECRET)([[:space:]]*[:=][[:space:]]*)[^[:space:]]+/\1\2[REDACTED]/g' \
    -e 's/(Authorization:[[:space:]]*Bearer)[[:space:]]+[^[:space:]]+/\1 [REDACTED]/g'
}

run_logged() {
  local output_file exit_code
  output_file=$(mktemp "${TMPDIR:-/tmp}/hermes-webui-runner.XXXXXX") || return 1
  if "$DOCKER_BIN" "$@" >"$output_file" 2>&1; then
    exit_code=0
  else
    exit_code=$?
  fi
  redact_sensitive <"$output_file" >>"$RUNNER_LOG"
  redact_sensitive <"$output_file"
  rm -f "$output_file"
  return "$exit_code"
}

docker_info_before() {
  local deadline="$1" remaining
  remaining=$(( deadline - EPOCHREALTIME ))
  (( remaining > 0 )) || return 1
  /usr/bin/perl -MTime::HiRes=alarm -e \
    'my $timeout = shift; alarm($timeout); exec @ARGV or exit 127' \
    "$remaining" "$DOCKER_BIN" info >/dev/null 2>&1
}

wait_for_docker() {
  local deadline remaining sleep_interval
  zmodload zsh/datetime || return 1
  deadline=$(( EPOCHREALTIME + DOCKER_TIMEOUT ))
  docker_info_before "$deadline" && return 0
  (( EPOCHREALTIME < deadline )) ||
    die "Docker daemon did not become available within ${DOCKER_TIMEOUT} seconds."
  "$OPEN_BIN" -a Docker || die "Docker Desktop could not be opened."
  while (( EPOCHREALTIME < deadline )); do
    remaining=$(( deadline - EPOCHREALTIME ))
    sleep_interval=1
    (( remaining < sleep_interval )) && sleep_interval=$remaining
    "$SLEEP_BIN" "$sleep_interval"
    docker_info_before "$deadline" && return 0
  done
  die "Docker daemon did not become available within ${DOCKER_TIMEOUT} seconds."
}

wait_for_url() {
  local label="$1" url="$2" deadline remaining curl_timeout
  zmodload zsh/datetime || return 1
  deadline=$(( EPOCHREALTIME + HEALTH_TIMEOUT ))
  while (( EPOCHREALTIME < deadline )); do
    remaining=$(( deadline - EPOCHREALTIME ))
    curl_timeout=2
    (( remaining < curl_timeout )) && curl_timeout=$remaining
    "$CURL_BIN" --fail --silent --show-error --output /dev/null \
      --max-time "$curl_timeout" "$url" && return 0
    (( EPOCHREALTIME >= deadline )) && break
    "$SLEEP_BIN" 1
  done
  die "$label did not become ready within ${HEALTH_TIMEOUT} seconds."
}

start_stack() {
  validate_secret_source
  initialize_commands
  prepare_log
  wait_for_docker
  run_logged compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" up -d --remove-orphans
  wait_for_url "Hermes WebUI" "$WEBUI_URL/health"
  wait_for_url "Hermes bridge (including a replay_complete Desktop connector)" "$BRIDGE_URL/health/ready"
}

stop_stack() {
  validate_secret_source
  initialize_commands
  prepare_log
  "$DOCKER_BIN" info >/dev/null 2>&1 ||
    die "Docker daemon is not available; the Hermes WebUI stack could not be stopped."
  run_logged compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" stop
}

status_stack() {
  local declared running declared_sorted running_sorted
  validate_secret_source
  initialize_commands
  prepare_log
  "$DOCKER_BIN" info >/dev/null 2>&1 ||
    die "Docker daemon is not available; the Hermes WebUI stack status could not be checked."
  declared=$(run_logged compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" config --services) || return 1
  running=$(run_logged compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" ps --services --status running) || return 1
  declared_sorted=$(print -r -- "$declared" | /usr/bin/sed '/^[[:space:]]*$/d' | /usr/bin/sort -u)
  running_sorted=$(print -r -- "$running" | /usr/bin/sed '/^[[:space:]]*$/d' | /usr/bin/sort -u)
  [[ -n "$declared_sorted" ]] || die "Docker Compose declared no services."
  [[ "$declared_sorted" == "$running_sorted" ]] ||
    die "Hermes WebUI stack containers are not fully running."
  print -r -- "Containers: running"
  "$CURL_BIN" --fail --silent --show-error --output /dev/null --max-time 2 \
    "$BRIDGE_URL/health/ready" ||
    die "Bridge: not connector-ready (start Hermes Desktop with the replay_complete connector)."
  print -r -- "Bridge: connector-ready"
}

usage() {
  print -u2 -- "Usage: $0 {start|stop|status}"
}

case "${1:-}" in
  start) start_stack ;;
  stop) stop_stack ;;
  status) status_stack ;;
  *) usage; exit 64 ;;
esac
