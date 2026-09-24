#!/usr/bin/env bash
# Shared helpers for the kanban scripts. Sourced, not run.
#   kanban_load_config   — finds the repo, loads .kanban/config.env and the notify env
#   kanban_render FILE   — prints FILE with {{PLACEHOLDERS}} substituted from the environment

KANBAN_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KANBAN_SKILL_DIR="$(cd "$KANBAN_SCRIPTS/.." && pwd)"

kanban_die() { printf '%s: %s\n' "$(basename "$0")" "$*" >&2; exit "${2:-1}"; }

# The repository is KANBAN_REPO, else the git top level of the current directory.
# Config is read from that tree; the gitignored notify env from the PRIMARY
# checkout (a worktree has no copy of it).
kanban_load_config() {
  local top common
  top="${KANBAN_REPO:-$(git rev-parse --show-toplevel 2>/dev/null)}" \
    || kanban_die "not inside a git repository (set KANBAN_REPO)"
  [ -n "$top" ] || kanban_die "not inside a git repository (set KANBAN_REPO)"
  [ -f "$top/.kanban/config.env" ] \
    || kanban_die "$top/.kanban/config.env is missing — run kanban-init.sh first"
  set -a
  # shellcheck disable=SC1091
  . "$top/.kanban/config.env"
  set +a
  common="$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  KANBAN_PRIMARY="$(dirname "$common")"
  KANBAN_REPO_ROOT="$top"
  : "${KANBAN_BOARD:?KANBAN_BOARD is empty in .kanban/config.env}"
  : "${KANBAN_PROFILE_PREFIX:?KANBAN_PROFILE_PREFIX is empty in .kanban/config.env}"
  KANBAN_TEMPLATES="${KANBAN_TEMPLATES:-docs/agents/kanban-templates}"
  KANBAN_RULES="${KANBAN_RULES:-AGENTS.md}"
  KANBAN_BASE_BRANCH="${KANBAN_BASE_BRANCH:-main}"
  KANBAN_NOTIFY_ENV="${KANBAN_NOTIFY_ENV:-.kanban/notify.env}"
  KANBAN_NOTIFY_FILE="$KANBAN_PRIMARY/$KANBAN_NOTIFY_ENV"
  if [ -f "$KANBAN_NOTIFY_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$KANBAN_NOTIFY_FILE"
    set +a
  fi
  export KANBAN_REPO_ROOT KANBAN_PRIMARY KANBAN_TEMPLATES KANBAN_RULES KANBAN_BASE_BRANCH
}

# platform:chat[:thread], empty when no target is configured.
kanban_notify_target() {
  [ -n "${KANBAN_NOTIFY_CHAT_ID:-}" ] || return 0
  printf '%s:%s%s' "${KANBAN_NOTIFY_PLATFORM:-telegram}" "$KANBAN_NOTIFY_CHAT_ID" \
    "${KANBAN_NOTIFY_THREAD_ID:+:$KANBAN_NOTIFY_THREAD_ID}"
}

# Substitutes {{NAME}} with $KANBAN_NAME, or $NAME for the few plain ones set by
# the caller (WORKDIR). Python, not sed: values carry slashes, pipes and quotes.
kanban_render() {
  python3 - "$1" <<'PY'
import os, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
alias = {
    "BOARD": "KANBAN_BOARD", "GATE": "KANBAN_GATE", "GATE_OK": "KANBAN_GATE_OK",
    "BASE": "KANBAN_BASE_BRANCH", "RULES": "KANBAN_RULES", "TEMPLATES": "KANBAN_TEMPLATES",
    "REPO": "KANBAN_PRIMARY", "LANGUAGE": "KANBAN_LANGUAGE", "WORKDIR": "WORKDIR",
    "LAND_CHECKS": "KANBAN_LAND_CHECKS_TEXT", "PREFIX": "KANBAN_PROFILE_PREFIX",
}
missing = []
def sub(m):
    key = m.group(1)
    val = os.environ.get(alias.get(key, "KANBAN_" + key))
    if val is None:
        missing.append(key)
        return m.group(0)
    return val
out = re.sub(r"\{\{([A-Z_]+)\}\}", sub, text)
if missing:
    sys.exit(f"render {sys.argv[1]}: no value for {', '.join(sorted(set(missing)))}")
sys.stdout.write(out)
PY
}

# The hermes binary; KANBAN_HERMES overrides it (tests point it at a fake — PATH
# alone is not enough, since BASH_ENV may re-prepend the real one in children).
hermes_cli() { "${KANBAN_HERMES:-hermes}" "$@"; }

# hermes refuses board writes when this leaks in from a parent session.
unset HERMES_DELEGATED_CHILD_CONTEXT
