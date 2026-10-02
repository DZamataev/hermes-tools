#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${HERMES_WEBUI_PROJECT_DIR:-${SCRIPT_DIR:h}}"
HERMES_ENV_FILE="${HERMES_WEBUI_ENV_FILE:-/Users/frenzy/.hermes/.env}"
LOCAL_ENV_FILE="${HERMES_WEBUI_LOCAL_ENV_FILE:-$PROJECT_DIR/.env.local}"
COMPOSE_FILE="$PROJECT_DIR/compose.yaml"
WEBUI_URL="${HERMES_WEBUI_URL:-http://localhost:11001}"
HEALTH_TIMEOUT="${HERMES_WEBUI_HEALTH_TIMEOUT:-120}"
COMPOSE_ENV_ARGS=(
  --env-file "$HERMES_ENV_FILE"
  --env-file "$LOCAL_ENV_FILE"
)

ASSUME_YES=0
BRIDGE_STOPPED=0
RESET_NEEDED=0
TEMP_DIR=""

die() { print -u2 -- "$1"; return 1; }

usage() {
  print -u2 -- "Usage: $0 [--yes]"
}

parse_arguments() {
  case "${1:-}" in
    "") ;;
    --yes) ASSUME_YES=1 ;;
    *) usage; return 64 ;;
  esac
  (( $# <= 1 )) || { usage; return 64; }
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

read_assignment() {
  local file="$1" key="$2"
  /usr/bin/awk -v key="$key" '
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      value = substr($0, index($0, "=") + 1)
      gsub(/^[[:space:]\"]+|[[:space:]\"]+$/, "", value)
      found = 1
    }
    END {
      if (!found || value == "") exit 1
      print value
    }
  ' "$file"
}

initialize() {
  [[ -f "$HERMES_ENV_FILE" ]] || die "Hermes environment file not found: $HERMES_ENV_FILE"
  [[ -f "$LOCAL_ENV_FILE" ]] || die "Local environment file not found: $LOCAL_ENV_FILE"
  [[ -f "$COMPOSE_FILE" ]] || die "Compose file not found: $COMPOSE_FILE"

  DOCKER_BIN="$(resolve_command "${HERMES_WEBUI_DOCKER_BIN:-}" /usr/local/bin/docker docker)"
  CURL_BIN="$(resolve_command "${HERMES_WEBUI_CURL_BIN:-}" /usr/bin/curl curl)"
  JQ_BIN="$(resolve_command "${HERMES_WEBUI_JQ_BIN:-}" /usr/bin/jq jq)"
  SLEEP_BIN="$(resolve_command "${HERMES_WEBUI_SLEEP_BIN:-}" /bin/sleep sleep)"
  OPENWEBUI_API_KEY="$(read_assignment "$LOCAL_ENV_FILE" OPENWEBUI_API_KEY)" ||
    die "OPENWEBUI_API_KEY is missing or empty in $LOCAL_ENV_FILE"
  print -r -- "$OPENWEBUI_API_KEY" | /usr/bin/grep -Eq '^[A-Za-z0-9._~-]+$' ||
    die "OPENWEBUI_API_KEY contains unsupported characters"

  local bridge_port
  bridge_port="$(read_assignment "$LOCAL_ENV_FILE" BRIDGE_HOST_PORT 2>/dev/null || print -r -- 8787)"
  print -r -- "$bridge_port" | /usr/bin/grep -Eq '^[0-9]+$' ||
    die "BRIDGE_HOST_PORT must be an integer"
  (( bridge_port >= 1 && bridge_port <= 65535 )) ||
    die "BRIDGE_HOST_PORT must be from 1 to 65535"
  BRIDGE_URL="${HERMES_WEBUI_BRIDGE_URL:-http://127.0.0.1:$bridge_port}"

  TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hermes-reset-history.XXXXXX")"
  chmod 700 "$TEMP_DIR"
  print -r -- "header = \"Authorization: Bearer $OPENWEBUI_API_KEY\"" > "$TEMP_DIR/curl.conf"
  chmod 600 "$TEMP_DIR/curl.conf"
  unset OPENWEBUI_API_KEY
}

compose() {
  "$DOCKER_BIN" compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" "$@"
}

api_get() {
  local url="$1" output="$2"
  "$CURL_BIN" --config "$TEMP_DIR/curl.conf" --fail --silent --show-error \
    --max-time 30 "$url" > "$output"
}

bridge_get() {
  local url="$1" output="$2"
  "$CURL_BIN" --fail --silent --show-error --max-time 2 "$url" > "$output"
}

verify_bridge_ready() {
  local ready_file="$TEMP_DIR/preflight-ready.json"
  bridge_get "$BRIDGE_URL/health/ready" "$ready_file" ||
    die "Hermes bridge is not ready. Start Hermes Desktop and its Read API before resetting history."
  "$JQ_BIN" -e '.status == "ready"' "$ready_file" >/dev/null ||
    die "Hermes bridge returned an invalid readiness status."
}

verify_idle_queue() {
  local status_file="$TEMP_DIR/bridge-status.json"
  bridge_get "$BRIDGE_URL/health/status" "$status_file" ||
    die "Hermes bridge status is unavailable. Start the stack before resetting history."
  "$JQ_BIN" -e '
    (.queue | type == "object") and
    ([.queue.pending, .queue.active, .queue.uncertain, .queue.failed] | all(type == "number"))
  ' "$status_file" >/dev/null || die "Hermes bridge returned an invalid queue status."
  "$JQ_BIN" -e '
    .queue.pending == 0 and .queue.active == 0 and
    .queue.uncertain == 0 and .queue.failed == 0
  ' "$status_file" >/dev/null ||
    die "Hermes bridge queue is not idle; finish or resolve queued work before resetting history."
}

find_hermes_folder() {
  local folders_file="$TEMP_DIR/folders.json"
  api_get "$WEBUI_URL/api/v1/folders/" "$folders_file" ||
    die "Could not list OpenWebUI folders."
  "$JQ_BIN" -e 'type == "array"' "$folders_file" >/dev/null ||
    die "OpenWebUI returned an invalid folder list."
  local count
  count="$($JQ_BIN -r '[.[] | select(.name == "Hermes" and .parent_id == null and (.id | type) == "string" and (.id | length) > 0)] | length' "$folders_file")"
  [[ "$count" == "1" ]] ||
    die "Expected exactly one root Hermes folder in OpenWebUI; found $count."
  HERMES_FOLDER_ID="$($JQ_BIN -r '.[] | select(.name == "Hermes" and .parent_id == null) | .id' "$folders_file")"
}

fetch_mirror_ids() {
  local output="$1"
  local chats_file="$TEMP_DIR/chats.json"
  api_get "$WEBUI_URL/api/v1/chats/folder/$HERMES_FOLDER_ID" "$chats_file" ||
    die "Could not list chats in the OpenWebUI Hermes folder."
  "$JQ_BIN" -e 'type == "array"' "$chats_file" >/dev/null ||
    die "OpenWebUI returned an invalid chat list."
  "$JQ_BIN" -r '
    .[] |
    select((.id | type) == "string" and (.id | length) > 0) |
    select((.variables | type) == "object") |
    select((.variables.hermes_lineage_key | type) == "string" and (.variables.hermes_lineage_key | length) > 0) |
    .id
  ' "$chats_file" > "$output"
}

confirm_reset() {
  local count="$1" answer=""
  print -r -- "Found $count Hermes-mirrored OpenWebUI chats."
  (( ASSUME_YES )) && return 0
  print -n -- "Type RESET to delete and resynchronize only these chats: "
  IFS= read -r answer || answer=""
  [[ "$answer" == "RESET" ]] || die "Reset cancelled."
}

delete_chat() {
  local chat_id="$1" response="$TEMP_DIR/delete-response.json"
  print -r -- "$chat_id" | /usr/bin/grep -Eq '^[A-Za-z0-9_-]+$' || return 1
  "$CURL_BIN" --config "$TEMP_DIR/curl.conf" --fail --silent --show-error \
    --max-time 30 --request DELETE "$WEBUI_URL/api/v1/chats/$chat_id" > "$response" ||
    return 1
  "$JQ_BIN" -e '. == true' "$response" >/dev/null
}

reset_bridge_links() {
  local output="$TEMP_DIR/reset-count.txt"
  compose run --rm --no-deps bridge-service \
    python -m hermes_bridge.maintenance reset-openwebui-links \
    --database /data/bridge.sqlite3 > "$output"
  /usr/bin/grep -Eq '^[0-9]+$' "$output" ||
    die "Bridge maintenance returned an invalid result."
  RESET_COUNT="$(<"$output")"
}

start_bridge() {
  compose start bridge-service
  BRIDGE_STOPPED=0
}

wait_for_resynchronization() {
  zmodload zsh/datetime || return 1
  local deadline=$(( EPOCHREALTIME + HEALTH_TIMEOUT ))
  local ready_file="$TEMP_DIR/ready.json" status_file="$TEMP_DIR/resync-status.json"
  while (( EPOCHREALTIME < deadline )); do
    if bridge_get "$BRIDGE_URL/health/ready" "$ready_file" &&
       bridge_get "$BRIDGE_URL/health/status" "$status_file" &&
       "$JQ_BIN" -e '
         .last_scan != null and
         .queue.pending == 0 and .queue.active == 0 and
         (.failures | type == "array" and length == 0)
       ' "$status_file" >/dev/null 2>&1; then
      return 0
    fi
    "$SLEEP_BIN" 1
  done
  die "Hermes bridge did not finish resynchronization within ${HEALTH_TIMEOUT} seconds."
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if (( BRIDGE_STOPPED )); then
    print -u2 -- "Restarting Hermes bridge after an interrupted reset..."
    if (( RESET_NEEDED )); then
      reset_bridge_links >/dev/null 2>&1 || exit_code=1
    fi
    compose start bridge-service >/dev/null 2>&1 || exit_code=1
  fi
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
  exit "$exit_code"
}

main() {
  parse_arguments "$@"
  initialize
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  "$DOCKER_BIN" info >/dev/null 2>&1 ||
    die "Docker daemon is not available."
  verify_bridge_ready
  verify_idle_queue
  find_hermes_folder
  fetch_mirror_ids "$TEMP_DIR/mirror-ids-before.txt"
  local initial_count
  initial_count="$(/usr/bin/wc -l < "$TEMP_DIR/mirror-ids-before.txt" | /usr/bin/tr -d ' ')"
  if (( initial_count == 0 )); then
    print -r -- "No Hermes-mirrored OpenWebUI chats found; nothing was changed."
    return 0
  fi
  confirm_reset "$initial_count"

  compose build bridge-service
  compose stop bridge-service
  BRIDGE_STOPPED=1

  fetch_mirror_ids "$TEMP_DIR/mirror-ids.txt"
  local final_count deleted_count=0 failed_count=0 chat_id
  final_count="$(/usr/bin/wc -l < "$TEMP_DIR/mirror-ids.txt" | /usr/bin/tr -d ' ')"
  if [[ "$final_count" != "$initial_count" ]]; then
    print -r -- "Mirror set changed before bridge shutdown; using the final count of $final_count."
  fi

  while IFS= read -r chat_id; do
    [[ -n "$chat_id" ]] || continue
    if delete_chat "$chat_id"; then
      deleted_count=$(( deleted_count + 1 ))
      RESET_NEEDED=1
    else
      failed_count=$(( failed_count + 1 ))
      print -u2 -- "Failed to delete one Hermes mirror; its identifier was not printed."
    fi
  done < "$TEMP_DIR/mirror-ids.txt"

  if (( deleted_count > 0 )); then
    reset_bridge_links
    RESET_NEEDED=0
  else
    RESET_COUNT=0
  fi
  start_bridge
  wait_for_resynchronization

  print -r -- "Deleted $deleted_count Hermes mirrors and detached $RESET_COUNT bridge mappings."
  if (( failed_count > 0 )); then
    die "$failed_count Hermes mirrors could not be deleted; rerun the command after checking OpenWebUI."
  fi
  print -r -- "OpenWebUI history was recreated from Hermes."
}

main "$@"
