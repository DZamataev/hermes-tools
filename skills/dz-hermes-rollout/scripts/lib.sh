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

# Re-exec in the background with the output in a log (needed from inside a Hermes Desktop session
# for anything that quits the app). Call as: maybe_detach "$@"
maybe_detach() {
  local a args=() detach=false
  for a in "$@"; do if [ "$a" = --detach ]; then detach=true; else args+=("$a"); fi; done
  $detach || return 0
  mkdir -p "$STATE_DIR"
  local log=$STATE_DIR/$SCRIPT_NAME-$(date +%Y%m%d-%H%M%S).log
  nohup "$0" ${args[@]+"${args[@]}"} >"$log" 2>&1 </dev/null &
  echo "$SCRIPT_NAME: started in background (pid $!)"
  echo "$SCRIPT_NAME: log $log"
  echo "$SCRIPT_NAME: result line: grep -E '^$TAG (OK|FAILED)' $log"
  trap - EXIT
  exit 0
}

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

app_running() { pgrep -f "$1/Contents/MacOS/Hermes\$" >/dev/null; }
# Quit by process path, not by bundle id: the test instance and the live app share the id.
quit_app() {  # $1 = bundle path
  [ -n "$1" ] || return 0
  app_running "$1" || return 0
  pkill -TERM -f "$1/Contents/MacOS/Hermes\$" || true
  # Electron drains its `hermes serve` child before exiting: 30+ s is normal with live sessions.
  for _ in $(seq 1 90); do app_running "$1" || return 0; sleep 1; done
  fail "$1 did not quit within 90 s"
}

test_instance_running() { pgrep -f -- "/Contents/MacOS/Hermes $MARK" >/dev/null; }
# Any Hermes window other than the test instance (the Dock app, a release copy).
live_app_running() { pgrep -fl "/Contents/MacOS/Hermes" | grep -v -- "$MARK" | grep -q 'Hermes$'; }

# Desktop turns still running in a Hermes home: a "tui prompt accepted" with no later
# "tui turn finished" for the same session, within the last $2 hours (default 12; an older one is
# left over from a crash). Prints "<session id> since <time>" per live turn.
live_turns() {  # $1 = Hermes home, $2 = window in hours
  local cutoff f logs=()
  cutoff=$(date -v-"${2:-12}"H '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d "-${2:-12} hours" '+%Y-%m-%d %H:%M:%S')
  for f in "$1/logs/agent.log" "$1"/profiles/*/logs/agent.log; do [ -f "$f" ] && logs+=("$f"); done
  [ ${#logs[@]} -gt 0 ] || return 0
  grep -hE 'tui (prompt accepted|turn finished):' "${logs[@]}" | sort | awk -v cutoff="$cutoff" '
    { ts = $1 " " substr($2, 1, 8)
      if (!match($0, /agent_session_id=[^ ]+/)) next
      id = substr($0, RSTART + 17, RLENGTH - 17)
      if ($0 ~ /tui prompt accepted:/) open[id] = ts; else delete open[id] }
    END { for (id in open) if (open[id] >= cutoff) print id " since " open[id] }'
}
