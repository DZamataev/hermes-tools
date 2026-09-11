#!/bin/zsh
set -euo pipefail
setopt extendedglob

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
WEBUI_DIR="${HERMES_WEBUI_DIR:-$ROOT/hermes-webui}"
USER_HOME="${HERMES_WEBUI_USER_HOME:-${HOME:?HOME is not set}}"
PYTHON_BIN="${HERMES_WEBUI_PYTHON:-$USER_HOME/.hermes/hermes-agent/venv/bin/python}"
HOST="${HERMES_WEBUI_HOST:-0.0.0.0}"
PORT="${HERMES_WEBUI_PORT:-8787}"
PID_FILE="${HERMES_WEBUI_APP_PID_FILE:-$USER_HOME/.hermes/webui-app.pid}"
LOG_FILE="${HERMES_WEBUI_APP_LOG_FILE:-$USER_HOME/.hermes/webui-app.log}"
START_TIMEOUT="${HERMES_WEBUI_APP_START_TIMEOUT:-30}"
STOP_TIMEOUT="${HERMES_WEBUI_APP_STOP_TIMEOUT:-10}"
PS_BIN="${HERMES_WEBUI_APP_PS_BIN:-/bin/ps}"
CURL_BIN="${HERMES_WEBUI_APP_CURL_BIN:-/usr/bin/curl}"
SLEEP_BIN="${HERMES_WEBUI_APP_SLEEP_BIN:-/bin/sleep}"

die() { print -u2 -- "$1"; return 1; }
valid_pid() { [[ "${1:-}" == <1-> ]]; }
process_alive() { /bin/kill -0 "$1" >/dev/null 2>&1; }
process_args() { "$PS_BIN" -p "$1" -o command= 2>/dev/null; }

# ps renders argv as text (including unquoted paths with spaces). Match only
# program position or the first script argument of a known interpreter, never
# an incidental path in arbitrary arguments or a shell -c expression.
is_checkout_script() {
  local command="$1" script
  # Foreground bootstrap execs server.py without changing PID.
  for script in "$WEBUI_DIR/start.sh" "$WEBUI_DIR/bootstrap.py" "$WEBUI_DIR/server.py"; do
    [[ "$command" == "$script" || "$command" == "$script"[[:space:]]* ]] && return 0
  done
  return 1
}
process_is_owned() {
  local args interpreter remainder
  args="$(process_args "$1")" || return 1
  args="${args##[[:space:]]#}"
  is_checkout_script "$args" && return 0
  # The configured Python path may itself contain spaces.
  if [[ "$args" == "$PYTHON_BIN"[[:space:]]* ]]; then
    remainder="${args#"$PYTHON_BIN"}"
    is_checkout_script "${remainder##[[:space:]]#}" && return 0
  fi
  interpreter="${args%%[[:space:]]*}"
  [[ "$args" != "$interpreter" ]] || return 1
  case "${interpreter:t}" in
    sh|bash|zsh|python|Python|python[0-9]##(.[0-9]##)#) ;;
    *) return 1 ;;
  esac
  remainder="${args#"$interpreter"}"
  is_checkout_script "${remainder##[[:space:]]#}"
}

validate_config() {
  local executable
  [[ "$PORT" == <1-65535> && ${#PORT} -le 5 ]] || die "Invalid Hermes WebUI port: $PORT" || return
  [[ "$START_TIMEOUT" == <1-3600> && ${#START_TIMEOUT} -le 4 ]] || die "Invalid startup timeout: $START_TIMEOUT" || return
  [[ "$STOP_TIMEOUT" == <1-3600> && ${#STOP_TIMEOUT} -le 4 ]] || die "Invalid stop timeout: $STOP_TIMEOUT" || return
  for executable in "$PS_BIN" "$CURL_BIN" "$SLEEP_BIN"; do
    [[ -x "$executable" && ! -d "$executable" ]] || die "Not executable: $executable" || return
  done
  [[ -d "$WEBUI_DIR" ]] || die "Hermes WebUI checkout not found: $WEBUI_DIR" || return
  WEBUI_DIR="$(cd "$WEBUI_DIR" && pwd -P)"
  [[ -r "$WEBUI_DIR/scripts/lib/health_probe.sh" ]] || die "Hermes WebUI health probe not found" || return
}

health_probe() {
  if [[ -n "${HERMES_WEBUI_APP_CURL_BIN:-}" ]]; then
    "$CURL_BIN" --fail --silent --show-error --max-time 2 \
      "http://127.0.0.1:$PORT/health" >/dev/null 2>&1
  else
    # The upstream helper is Bash: its local `path` would mutate PATH in zsh.
    # Source it in Bash, loading the same checkout .env as start.sh first so
    # TLS/fallback probing observes the server's effective configuration.
    PATH=/usr/bin:/bin /bin/bash -c '
      env_file="$3/.env"
      if [[ -f "$env_file" ]]; then
        filtered_env="$(mktemp "${TMPDIR:-/tmp}/hermes-webui-app-env.XXXXXX")" || exit 1
        trap '\''rm -f "$filtered_env"'\'' EXIT
        grep -vE '\''^[[:space:]]*(export[[:space:]]+)?(UID|GID|EUID|EGID|PPID)='\'' "$env_file" > "$filtered_env" || true
        set -a
        source "$filtered_env"
        set +a
      fi
      source "$1"
      hermes_webui_probe_health 127.0.0.1 "$2" /health 2 direct
    ' hermes-webui-app "$WEBUI_DIR/scripts/lib/health_probe.sh" "$PORT" "$WEBUI_DIR" >/dev/null
  fi
}

# 0: owned PID printed; 1: no live saved PID; 2: unsafe state, left intact.
owned_pid() {
  local pid
  [[ ! -L "$PID_FILE" ]] || return 2
  [[ -e "$PID_FILE" ]] || return 1
  [[ -f "$PID_FILE" && -r "$PID_FILE" ]] || return 2
  pid="$(<"$PID_FILE")"
  if ! valid_pid "$pid" || ! process_alive "$pid"; then
    /bin/rm -f "$PID_FILE"
    return 1
  fi
  process_is_owned "$pid" || return 2
  print -r -- "$pid"
}
clear_pid() {
  [[ ! -L "$PID_FILE" && -f "$PID_FILE" && "$(<"$PID_FILE")" == "$1" ]] || return 0
  /bin/rm -f "$PID_FILE"
}

prepare_state() {
  local directory file
  umask 077
  for directory in "${PID_FILE:h}" "${LOG_FILE:h}"; do
    [[ -d "$directory" ]] || /bin/mkdir -p -m 700 "$directory" || return
  done
  for file in "$PID_FILE" "$LOG_FILE"; do
    [[ ! -L "$file" && ( ! -e "$file" || -f "$file" ) ]] || die "Unsafe state file: $file" || return
    : >> "$file" || return
    /bin/chmod 600 "$file" || return
  done
}

terminate_owned() {
  local pid="$1" attempt
  process_alive "$pid" || return 0
  process_is_owned "$pid" || die "refusing to stop unowned PID $pid" || return
  /bin/kill -TERM "$pid" 2>/dev/null || { process_alive "$pid" && return 1; }
  for (( attempt=0; attempt < STOP_TIMEOUT; attempt++ )); do
    process_alive "$pid" || return 0
    "$SLEEP_BIN" 1
  done
  process_alive "$pid" || return 0
  process_is_owned "$pid" || die "refusing to stop unowned PID $pid" || return
  /bin/kill -KILL "$pid" 2>/dev/null || { process_alive "$pid" && return 1; }
  for (( attempt=0; attempt < STOP_TIMEOUT; attempt++ )); do
    process_alive "$pid" || return 0
    "$SLEEP_BIN" 1
  done
  die "Hermes WebUI PID $pid did not stop"
}

wait_until_healthy() {
  local pid="$1" attempt
  for (( attempt=0; attempt < START_TIMEOUT; attempt++ )); do
    process_alive "$pid" || return 1
    # A freshly forked child can briefly still have the controller's argv.
    # Wait for exec; that transition never grants permission to signal it.
    if process_is_owned "$pid" && health_probe; then
      process_alive "$pid" && process_is_owned "$pid" && return 0
    fi
    "$SLEEP_BIN" 1
  done
  return 1
}

start_app() {
  local pid saved_result=0
  pid="$(owned_pid)" || saved_result=$?
  if (( saved_result == 2 )); then die "unsafe PID state; refusing to start Hermes WebUI"; return 1; fi
  if (( saved_result == 0 )); then
    wait_until_healthy "$pid" || { die "Hermes WebUI did not become healthy (PID $pid)"; return 1; }
    print -- "Started Hermes WebUI (reattached PID $pid)"
    return 0
  fi
  if health_probe; then die "another server is already responding"; return 1; fi
  [[ -x "$WEBUI_DIR/start.sh" ]] || die "Hermes WebUI launcher is not executable" || return
  [[ -r "$WEBUI_DIR/bootstrap.py" ]] || die "Hermes WebUI bootstrap.py not found" || return
  [[ -x "$PYTHON_BIN" && ! -d "$PYTHON_BIN" ]] || die "Hermes Python is not executable: $PYTHON_BIN" || return
  prepare_state || return
  HERMES_WEBUI_PYTHON="$PYTHON_BIN" \
    "$WEBUI_DIR/start.sh" --foreground --no-browser --host "$HOST" "$PORT" \
    >>"$LOG_FILE" 2>&1 &
  pid=$!
  print -r -- "$pid" > "$PID_FILE"
  if wait_until_healthy "$pid"; then
    print -- "Started Hermes WebUI (PID $pid)"
    return 0
  fi
  # Only this invocation's child is eligible for startup-failure cleanup.
  # If ownership changed, retain the PID as unsafe instead of hiding a process.
  if terminate_owned "$pid"; then clear_pid "$pid"; fi
  die "Hermes WebUI did not become healthy; see $LOG_FILE"
}

stop_app() {
  local pid saved_result=0
  pid="$(owned_pid)" || saved_result=$?
  if (( saved_result == 2 )); then die "refusing to stop unowned PID (unsafe PID state)"; return 1; fi
  if (( saved_result == 0 )); then
    terminate_owned "$pid" || return
    clear_pid "$pid"
  fi
  print -- "Hermes WebUI stopped"
}
status_app() {
  local pid saved_result=0
  pid="$(owned_pid)" || saved_result=$?
  if (( saved_result == 2 )); then die "Hermes WebUI: unsafe PID state"; return 1; fi
  if (( saved_result == 0 )); then
    print -- "Hermes WebUI: owned and running (PID $pid)"
  elif health_probe; then
    print -- "Hermes WebUI: external server responding"
  else
    print -- "Hermes WebUI: stopped"
  fi
}

case "${1:-}" in
  start|stop|status) [[ $# -eq 1 ]] || { print -u2 -- "Usage: $0 {start|stop|status}"; exit 64; } ;;
  *) print -u2 -- "Usage: $0 {start|stop|status}"; exit 64 ;;
esac
validate_config
case "$1" in
  start) start_app ;;
  stop) stop_app ;;
  status) status_app ;;
esac
