#!/usr/bin/env bash
# Read-only: which agent harness runs this, what Hermes is installed, and so which mode the skill is
# in. Prints key=value lines; the skill branches on `mode`.
#
#   harness          hermes | claude-code | codex | unknown
#   harness_detail   the surface (desktop, tui, cli, ...) when known
#   hermes_desktop   yes when a Hermes Desktop app is an ancestor (deploy needs --detach)
#   install_*        the live install: present, dir, origin (of the tracked remote), branch,
#                    kind (dz-fork|vanilla|other-fork|none)
#   dev_*            the fork checkout rollout merges in (from the rollout config)
#   config           the rollout config file, present or not
#   mode             initial-setup | install-only | dev-only | rollout
#
#   state.sh [--help]
. "$(dirname "$0")/lib.sh"
trap - EXIT
case ${1:-} in -h|--help) sed -n '2,14p' "$0"; exit 0 ;; esac

# ── harness ───────────────────────────────────────────────────────────────────────────────────
# The nearest ancestor that is a known agent wins (an agent can run inside another's terminal);
# environment markers decide when the ancestry says nothing.
classify() {  # $1 = process args
  case $1 in
    *Hermes.app/Contents/MacOS/Hermes*|*hermes_cli.main*|*/.hermes/bin/hermes*|*/bin/hermes\ *) echo hermes ;;
    *[Cc]laude*) echo claude-code ;;
    *codex*) echo codex ;;
  esac
}
harness="" hermes_desktop=no pid=${ROLLOUT_HARNESS_PID:-$PPID}   # tests pass 1: ancestry says nothing
for _ in $(seq 1 40); do
  [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null || break
  args=$(ps -o args= -p "$pid" 2>/dev/null) || break
  case $args in *Hermes.app/Contents/MacOS/Hermes*) hermes_desktop=yes ;; esac
  [ -z "$harness" ] && harness=$(classify "$args")
  pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
done
if [ -z "$harness" ]; then
  if [ "${CLAUDECODE:-}" = 1 ]; then harness=claude-code
  elif env | grep -q '^CODEX_'; then harness=codex
  elif [ -n "${HERMES_SESSION_ID:-}" ]; then harness=hermes
  else harness=unknown; fi
fi
case $harness in
  hermes) detail=${HERMES_SESSION_SOURCE:-${HERMES_SESSION_PLATFORM:-}}; [ $hermes_desktop = yes ] && detail=${detail:-desktop} ;;
  claude-code) detail=${CLAUDE_CODE_ENTRYPOINT:-} ;;
  *) detail="" ;;
esac

# ── installs ──────────────────────────────────────────────────────────────────────────────────
kind_of() {  # $1 = origin URL
  case $1 in
    "") echo none ;;
    *[/:][Dd][Zz]amataev/hermes-agent|*[/:][Dd][Zz]amataev/hermes-agent.git) echo dz-fork ;;
    *[/:][Nn]ous[Rr]esearch/hermes-agent|*[/:][Nn]ous[Rr]esearch/hermes-agent.git) echo vanilla ;;
    *) echo other-fork ;;
  esac
}
# The remote the checked-out branch tracks tells what it runs (an install fed by rollout keeps
# origin on upstream and tracks the fork); origin when the branch tracks nothing.
source_url() {  # $1 = checkout
  local up remote
  up=$(git -C "$1" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
  remote=${up%%/*}
  git -C "$1" remote get-url "${remote:-origin}" 2>/dev/null || git -C "$1" remote get-url origin 2>/dev/null || true
}
install_present=no install_origin="" install_branch=""
if [ -e "$INSTALL/.git" ]; then
  install_present=yes
  install_origin=$(source_url "$INSTALL")
  install_branch=$(git -C "$INSTALL" branch --show-current 2>/dev/null || true)
fi
install_kind=$( [ $install_present = yes ] && kind_of "$install_origin" || echo none)
dev_present=no dev_origin=""
if [ -n "$FORK" ] && [ -e "$FORK/.git" ]; then
  dev_present=yes
  dev_origin=$(source_url "$FORK")
fi
config=$( [ -f "$ROLLOUT_CONFIG" ] && echo present || echo missing)

if [ $install_present = no ] && [ $dev_present = no ]; then mode=initial-setup
elif [ $dev_present = no ]; then mode=install-only
elif [ $install_present = no ]; then mode=dev-only
else mode=rollout; fi

cat <<EOF
harness=$harness
harness_detail=$detail
hermes_desktop=$hermes_desktop
install_present=$install_present
install_dir=$INSTALL
install_origin=$install_origin
install_branch=$install_branch
install_kind=$install_kind
app_present=$( [ -d "$APP" ] && echo yes || echo no)
dev_present=$dev_present
dev_dir=$FORK
dev_origin=$dev_origin
config=$config
config_file=$ROLLOUT_CONFIG
mode=$mode
EOF
