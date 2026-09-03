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
    -e 's/("(API_SERVER_KEY|OPENAI_API_KEY)"[[:space:]]*:[[:space:]]*")[^"]*"/\1[REDACTED]"/g' \
    -e 's/("Authorization"[[:space:]]*:[[:space:]]*"Bearer[[:space:]]+)[^"]*"/\1[REDACTED]"/g' \
    -e 's/(API_SERVER_KEY|OPENAI_API_KEY)([[:space:]]*[:=][[:space:]]*)[^[:space:]]+/\1\2[REDACTED]/g' \
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

wait_for_webui() {
  local deadline remaining curl_timeout
  zmodload zsh/datetime || return 1
  deadline=$(( EPOCHREALTIME + HEALTH_TIMEOUT ))
  while (( EPOCHREALTIME < deadline )); do
    remaining=$(( deadline - EPOCHREALTIME ))
    curl_timeout=2
    (( remaining < curl_timeout )) && curl_timeout=$remaining
    "$CURL_BIN" --fail --silent --show-error --max-time "$curl_timeout" "$WEBUI_URL/health" && return 0
    (( EPOCHREALTIME >= deadline )) && break
    "$SLEEP_BIN" 1
  done
  die "Hermes WebUI did not become healthy within ${HEALTH_TIMEOUT} seconds."
}

start_stack() {
  validate_secret_source
  initialize_commands
  prepare_log
  wait_for_docker
  run_logged compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans
  wait_for_webui
}

stop_stack() {
  validate_secret_source
  initialize_commands
  prepare_log
  "$DOCKER_BIN" info >/dev/null 2>&1 ||
    die "Docker daemon is not available; the Hermes WebUI stack could not be stopped."
  run_logged compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" stop
}

status_stack() {
  local declared running declared_sorted running_sorted
  validate_secret_source
  initialize_commands
  prepare_log
  "$DOCKER_BIN" info >/dev/null 2>&1 ||
    die "Docker daemon is not available; the Hermes WebUI stack status could not be checked."
  declared=$(run_logged compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --services) || return 1
  running=$(run_logged compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps --services --status running) || return 1
  declared_sorted=$(print -r -- "$declared" | /usr/bin/sed '/^[[:space:]]*$/d' | /usr/bin/sort -u)
  running_sorted=$(print -r -- "$running" | /usr/bin/sed '/^[[:space:]]*$/d' | /usr/bin/sort -u)
  [[ -n "$declared_sorted" ]] || die "Docker Compose declared no services."
  [[ "$declared_sorted" == "$running_sorted" ]] ||
    die "Hermes WebUI stack is not fully running."
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
