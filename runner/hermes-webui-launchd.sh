#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
LABEL="com.parantoux.hermes-webui"
TEMPLATE="$ROOT/runner/$LABEL.plist.in"
WEBUI_DIR="${HERMES_WEBUI_DIR:-$ROOT/hermes-webui}"
USER_HOME="${HERMES_WEBUI_USER_HOME:-${HOME:?HOME is not set}}"
PYTHON_BIN="${HERMES_WEBUI_PYTHON:-$USER_HOME/.hermes/hermes-agent/venv/bin/python}"
HOST="${HERMES_WEBUI_HOST:-0.0.0.0}"
PORT="${HERMES_WEBUI_PORT:-8787}"
STATE_DIR="${HERMES_WEBUI_STATE_DIR:-$USER_HOME/.hermes/webui}"
LAUNCH_AGENT_DIR="${HERMES_WEBUI_LAUNCH_AGENT_DIR:-$USER_HOME/Library/LaunchAgents}"
PLIST="$LAUNCH_AGENT_DIR/$LABEL.plist"
DOMAIN="${HERMES_WEBUI_DOMAIN:-gui/$(/usr/bin/id -u)}"
HEALTH_URL="${HERMES_WEBUI_HEALTH_URL:-http://127.0.0.1:$PORT/health?deep=1}"
HEALTH_TIMEOUT="${HERMES_WEBUI_HEALTH_TIMEOUT:-30}"

LAUNCHCTL_BIN="${HERMES_WEBUI_LAUNCHCTL_BIN:-/bin/launchctl}"
PLUTIL_BIN="${HERMES_WEBUI_PLUTIL_BIN:-/usr/bin/plutil}"
CURL_BIN="${HERMES_WEBUI_CURL_BIN:-/usr/bin/curl}"
SLEEP_BIN="${HERMES_WEBUI_SLEEP_BIN:-/bin/sleep}"

die() { print -u2 -- "$1"; exit 1; }

usage() {
  print -u2 -- "Usage: $0 {enable|disable|restart|status}"
}

is_loaded() {
  "$LAUNCHCTL_BIN" print "$DOMAIN/$LABEL" >/dev/null 2>&1
}

is_disabled() {
  local output
  output=$("$LAUNCHCTL_BIN" print-disabled "$DOMAIN" 2>/dev/null || true)
  [[ "$output" == *\"$LABEL\"' => disabled'* ]]
}

validate_install() {
  [[ -f "$TEMPLATE" ]] || die "LaunchAgent template not found: $TEMPLATE"
  [[ -x "$WEBUI_DIR/start.sh" ]] || die "Hermes WebUI launcher not found: $WEBUI_DIR/start.sh"
  [[ -x "$PYTHON_BIN" ]] || die "Hermes Python is not executable: $PYTHON_BIN"
  [[ "$PORT" == <1-65535> ]] || die "Invalid Hermes WebUI port: $PORT"
}

render_plist() {
  local temporary_plist
  /bin/mkdir -p "$LAUNCH_AGENT_DIR" "$STATE_DIR"
  temporary_plist=$(mktemp "$PLIST.tmp.XXXXXX") || die "Could not create a temporary plist"
  trap '/bin/rm -f "$temporary_plist"' EXIT
  /bin/cp "$TEMPLATE" "$temporary_plist"

  "$PLUTIL_BIN" -remove ProgramArguments.6 "$temporary_plist"
  "$PLUTIL_BIN" -remove ProgramArguments.5 "$temporary_plist"
  "$PLUTIL_BIN" -remove ProgramArguments.1 "$temporary_plist"
  "$PLUTIL_BIN" -insert ProgramArguments.1 -string "$WEBUI_DIR/start.sh" "$temporary_plist"
  "$PLUTIL_BIN" -insert ProgramArguments.5 -string "$HOST" "$temporary_plist"
  "$PLUTIL_BIN" -insert ProgramArguments.6 -string "$PORT" "$temporary_plist"
  "$PLUTIL_BIN" -replace WorkingDirectory -string "$WEBUI_DIR" "$temporary_plist"
  "$PLUTIL_BIN" -replace StandardOutPath -string "$STATE_DIR/launchd-stdout.log" "$temporary_plist"
  "$PLUTIL_BIN" -replace StandardErrorPath -string "$STATE_DIR/launchd-stderr.log" "$temporary_plist"
  "$PLUTIL_BIN" -replace EnvironmentVariables.HOME -string "$USER_HOME" "$temporary_plist"
  "$PLUTIL_BIN" -replace EnvironmentVariables.HERMES_WEBUI_PYTHON -string "$PYTHON_BIN" "$temporary_plist"
  "$PLUTIL_BIN" -replace EnvironmentVariables.HERMES_WEBUI_HOST -string "$HOST" "$temporary_plist"
  "$PLUTIL_BIN" -replace EnvironmentVariables.HERMES_WEBUI_PORT -string "$PORT" "$temporary_plist"
  "$PLUTIL_BIN" -lint "$temporary_plist" >/dev/null

  /bin/chmod 644 "$temporary_plist"
  /bin/mv "$temporary_plist" "$PLIST"
  trap - EXIT
}

wait_until_unloaded() {
  local attempt
  for attempt in {1..10}; do
    is_loaded || return 0
    "$SLEEP_BIN" 1
  done
  die "LaunchAgent did not unload: $DOMAIN/$LABEL"
}

wait_until_healthy() {
  local attempt
  for attempt in {1..$HEALTH_TIMEOUT}; do
    if "$CURL_BIN" --fail --silent --show-error --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    "$SLEEP_BIN" 1
  done
  die "Hermes WebUI did not become healthy: $HEALTH_URL"
}

enable_service() {
  validate_install
  render_plist
  "$LAUNCHCTL_BIN" enable "$DOMAIN/$LABEL"
  if is_loaded; then
    "$LAUNCHCTL_BIN" bootout "$DOMAIN/$LABEL"
    wait_until_unloaded
  fi
  "$LAUNCHCTL_BIN" bootstrap "$DOMAIN" "$PLIST"
  wait_until_healthy
  print -- "Hermes WebUI enabled and running at http://127.0.0.1:$PORT"
}

disable_service() {
  if is_loaded; then
    "$LAUNCHCTL_BIN" bootout "$DOMAIN/$LABEL"
    wait_until_unloaded
  fi
  "$LAUNCHCTL_BIN" disable "$DOMAIN/$LABEL"
  print -- "Hermes WebUI disabled and stopped"
}

restart_service() {
  is_loaded || die "Hermes WebUI is not loaded; run '$0 enable' first"
  "$LAUNCHCTL_BIN" kickstart -k "$DOMAIN/$LABEL"
  wait_until_healthy
  print -- "Hermes WebUI restarted"
}

status_service() {
  local enable_state run_state plist_state
  if is_disabled; then enable_state=disabled; else enable_state=enabled; fi
  if is_loaded; then run_state=running; else run_state=stopped; fi
  if [[ -f "$PLIST" ]]; then plist_state=installed; else plist_state=missing; fi
  print -- "Hermes WebUI: $enable_state, $run_state, plist $plist_state"
}

case "${1:-}" in
  enable) enable_service ;;
  disable) disable_service ;;
  restart) restart_service ;;
  status) status_service ;;
  *) usage; exit 64 ;;
esac
