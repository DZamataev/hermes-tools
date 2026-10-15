# Shared by merge.sh / verify.sh / deploy.sh. Sourced, not executed.
# shellcheck shell=bash disable=SC2034  # the config variables are used by the scripts sourcing this
set -euo pipefail

# ── configuration ─────────────────────────────────────────────────────────────────────────────
# Read from $ROLLOUT_CONFIG (default ~/.config/dz-hermes-rollout/config.sh; template in
# ../templates/config.sh). Environment variables of the same name win over the file.
ROLLOUT_CONFIG=${ROLLOUT_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/dz-hermes-rollout/config.sh}
_env_snapshot=$(declare -px 2>/dev/null | grep -E '^declare -x ROLLOUT_' || true)
# shellcheck source=/dev/null
[ -f "$ROLLOUT_CONFIG" ] && . "$ROLLOUT_CONFIG"
eval "$_env_snapshot"


FORK=${ROLLOUT_FORK:-}                                          # fork checkout: merges, tests, test build
BRANCH=${ROLLOUT_BRANCH:-develop}                               # the fork's integration branch
WT_ROOT=${ROLLOUT_WT_ROOT:-}                                    # feature worktrees (empty: every linked worktree of $FORK)
UPSTREAM=${ROLLOUT_UPSTREAM:-upstream/main}                     # <remote>/<branch> merged by merge.sh --upstream
PUSH_REMOTE=${ROLLOUT_PUSH_REMOTE:-origin}                      # where deploy.sh --push sends $BRANCH
LIVE_HOME=${ROLLOUT_LIVE_HOME:-${HERMES_HOME:-$HOME/.hermes}}   # home of the live app and gateway
INSTALL=${ROLLOUT_INSTALL:-$LIVE_HOME/hermes-agent}             # git checkout the live install runs
TEST_HOME=${ROLLOUT_TEST_HOME:-$HOME/.hermes-rollout-test}      # isolated state of the test instance
APP=${ROLLOUT_APP:-/Applications/Hermes.app}                    # the app the Dock launches
POST_DEPLOY=${ROLLOUT_POST_DEPLOY:-}                            # optional command after the install update
RESTART_GATEWAY=${ROLLOUT_RESTART_GATEWAY:-1}                   # 0 when no gateway service is installed

HB=$INSTALL/.hermes/bin/hermes                                  # the install's launcher: gateway + app run it
STATE_DIR=$LIVE_HOME/logs/rollout
VERIFIED=$STATE_DIR/verified                                    # "<sha> <date> ..." written by verify.sh
MARK=--hermes-rollout-test-instance  # extra argv on the test instance: tells it apart from the live app

SCRIPT_NAME=$(basename "$0" .sh)
TAG=$(printf '%s' "$SCRIPT_NAME" | tr '[:lower:]' '[:upper:]')

step() { printf '\n== %s  [%s]\n' "$*" "$(date +%H:%M:%S)"; }
notify() {
  [ "${ROLLOUT_NO_NOTIFY:-0}" = 1 ] && return 0
  command -v osascript >/dev/null && osascript -e "display notification \"${1//\"/\'}\" with title \"Hermes $SCRIPT_NAME\"" >/dev/null 2>&1 || true
}
fail() {
  trap - EXIT
  echo "$TAG FAILED: $*"
  notify "failed: $*"
  exit 1
}
ok() {
  trap - EXIT
  echo
  echo "$TAG OK: $*"
  notify "OK: $*"
}
# shellcheck disable=SC2154  # rc is assigned inside the trap body
trap 'rc=$?; [ $rc -eq 0 ] || { echo "$TAG FAILED: line $LINENO exited $rc"; notify "failed (line $LINENO)"; }' EXIT

require_config() {
  [ -n "$FORK" ] || fail "ROLLOUT_FORK is not set — copy templates/config.sh to $ROLLOUT_CONFIG and fill it in"
  [ -e "$FORK/.git" ] || fail "ROLLOUT_FORK is not a git checkout: $FORK"
  mkdir -p "$STATE_DIR"
}

# Deploy quits Hermes Desktop, which kills an agent running in it (its turn and every background
# subagent) and the shells of its embedded terminal. Both carry a marker in their environment:
# HERMES_SESSION_ID (agent tools) or HERMES_DESKTOP_TERMINAL (the terminal pane, inherited by any
# agent harness started there).
inside_hermes() { [ -n "${HERMES_SESSION_ID:-}" ] || [ "${HERMES_DESKTOP_TERMINAL:-}" = 1 ]; }

# Run a command inside the fork's activated environment (deps + pytest).
fork_env() { (cd "$FORK" && bash -c '. ./activate -- >/dev/null 2>&1 || exit 97; "$@"' _ "$@"); }

smoke() {  # $1 = checkout; imports the startup-critical modules with the given python runner
  local root=$1; shift
  (cd "$root" && "$@" -c "import hermes_cli.main, run_agent, model_tools, toolsets") \
    || fail "import smoke test failed in $root"
}

require_branch_clean() {  # $1 = checkout; package-lock.json churn is desktop-build noise
  local dirty
  [ "$(git -C "$1" branch --show-current)" = "$BRANCH" ] || fail "$1 is not on $BRANCH"
  dirty=$(git -C "$1" status --porcelain --untracked-files=no | grep -v 'package-lock.json$' || true)
  [ -z "$dirty" ] || fail "$1 has uncommitted changes: $(echo "$dirty" | tr '\n' ' ')"
}

# The packaged desktop build of a checkout (release/mac-<arch>/Hermes.app), or nothing.
release_app() {  # $1 = checkout
  local a
  for a in "$1"/apps/desktop/release/mac*/Hermes.app; do [ -d "$a" ] && { echo "$a"; return 0; }; done
  return 0
}

# Main Electron processes by executable path (`comm`), not `pgrep -f`: macOS sometimes withholds a
# process's argv, and then pgrep/pkill -f miss the live app outright.
hermes_pids() {  # $1 = bundle path, or empty for every Hermes bundle
  ps -axo pid=,comm= | awk -v want="${1:+$1/Contents/MacOS/Hermes}" '
    { pid = $1; sub(/^ *[0-9]+ +/, ""); exe = $0
      if (exe !~ /\/Contents\/MacOS\/Hermes$/) next
      if (want == "" || exe == want) print pid }'
}
app_running() { [ -n "$(hermes_pids "$1")" ]; }
# Quit by process path, not by bundle id: the test instance and the live app share the id.
quit_app() {  # $1 = bundle path
  [ -n "$1" ] || return 0
  app_running "$1" || return 0
  # shellcheck disable=SC2046  # one pid per word
  kill -TERM $(hermes_pids "$1") 2>/dev/null || true
  # Electron drains its `hermes serve` child before exiting: 30+ s is normal with live sessions.
  for _ in $(seq 1 90); do app_running "$1" || return 0; sleep 1; done
  fail "$1 did not quit within 90 s"
}

# The test instance is told apart only by its extra argv, so this one needs `pgrep -f`.
test_instance_pids() { pgrep -f -- "/Contents/MacOS/Hermes $MARK" || true; }
test_instance_running() { [ -n "$(test_instance_pids)" ]; }
# Any Hermes window other than the test instance (the Dock app, a release copy).
live_app_running() {
  local pid test
  test=" $(test_instance_pids | tr '\n' ' ') "
  for pid in $(hermes_pids ""); do [[ "$test" == *" $pid "* ]] || return 0; done
  return 1
}

# Backends a Hermes Desktop on ANOTHER machine started here over SSH for the live home
# (`hermes serve --isolated --ssh-session-token-file <home>/desktop-ssh/...`). Neither app quit
# nor gateway restart touches them, so after a deploy they keep serving the old code while
# imports load the new files: plugin routes 404 and wire contracts break. The remote Desktop
# respawns one on demand, so stopping them is the restart. Prints one pid per line.
ssh_backends() {
  ps -axo pid=,args= | awk -v dir="--ssh-session-token-file $LIVE_HOME/desktop-ssh/" \
    'index($0, dir) && index($0, " serve ") { print $1 }'
}
stop_ssh_backends() {
  local pids p alive
  pids=$(ssh_backends)
  [ -n "$pids" ] || return 0
  echo "stopping SSH backend(s): $(echo "$pids" | tr '\n' ' ')(the remote Desktop respawns them on the new code)"
  # shellcheck disable=SC2086  # one pid per word
  kill -TERM $pids 2>/dev/null || true
  for _ in $(seq 1 60); do
    alive=""
    for p in $pids; do kill -0 "$p" 2>/dev/null && alive+="$p "; done
    [ -n "$alive" ] || return 0
    sleep 1
  done
  fail "SSH backend(s) did not stop within 60 s: $alive"
}

# Desktop turns still running in a Hermes home: a "tui prompt accepted" with no later
# "tui turn finished" for the same session, within the last $2 hours (default 12; an older one is
# left over from a crash). Prints "<session id> since <time>" per live turn. A desktop backend
# (`hermes serve`) logs its turns to gui.log, the CLI and gateway to agent.log: read both.
live_turns() {  # $1 = Hermes home, $2 = window in hours
  local cutoff f logs=()
  cutoff=$(date -v-"${2:-12}"H '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "-${2:-12} hours" '+%Y-%m-%d %H:%M:%S')
  for f in "$1/logs/agent.log" "$1/logs/gui.log" "$1"/profiles/*/logs/agent.log "$1"/profiles/*/logs/gui.log; do
    [ -f "$f" ] && logs+=("$f")
  done
  [ ${#logs[@]} -gt 0 ] || return 0
  # `|| true`: no tui lines yet (a fresh install) is grep exit 1, fatal under the callers' pipefail.
  { grep -hE 'tui (prompt accepted|turn finished):' "${logs[@]}" || true; } | sort | awk -v cutoff="$cutoff" '
    { ts = $1 " " substr($2, 1, 8)
      if (!match($0, /agent_session_id=[^ ]+/)) next
      id = substr($0, RSTART + 17, RLENGTH - 17)
      if ($0 ~ /tui prompt accepted:/) open[id] = ts; else delete open[id] }
    END { for (id in open) if (open[id] >= cutoff) print id " since " open[id] }'
}

# Delegated subagents still working in a Hermes home. A background delegate_task child outlives its
# parent's turn (the parent's "turn finished" is already logged), so live_turns misses it, yet an app
# quit kills it. A child = a session whose "conversation turn" line says platform=subagent, whose row
# in that home's state.db is not ended, and that logged something in the last $2 minutes (default
# 15; an older one is a crash leftover). Prints "<session id> (subagent of <parent>) since <time>".
live_subagents() {  # $1 = Hermes home, $2 = quiet window in minutes
  local cutoff home f id parent last logs
  cutoff=$(date -v-"${2:-15}"M '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "-${2:-15} minutes" '+%Y-%m-%d %H:%M:%S')
  for home in "$1" "$1"/profiles/*; do
    [ -f "$home/state.db" ] || continue
    logs=()
    for f in "$home/logs/agent.log" "$home/logs/gui.log"; do [ -f "$f" ] && logs+=("$f"); done
    [ ${#logs[@]} -gt 0 ] || continue
    for id in $({ grep -hE 'conversation turn: session=[^ ]+ .*platform=subagent' "${logs[@]}" || true; } \
                  | sed -E 's/.*conversation turn: session=([^ ]+).*/\1/' | sort -u); do
      parent=$(sqlite3 "$home/state.db" "SELECT parent_session_id FROM sessions WHERE id='$id' AND ended_at IS NULL" 2>/dev/null || true)
      [ -n "$parent" ] || continue
      last=$({ grep -hF "[$id]" "${logs[@]}" || true; } | sort | tail -1 | cut -c1-19)
      if [ -n "$last" ] && [[ "$last" > "$cutoff" ]]; then echo "$id (subagent of $parent) since $last"; fi
    done
  done
  return 0
}
