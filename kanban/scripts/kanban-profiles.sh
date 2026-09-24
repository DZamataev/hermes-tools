#!/usr/bin/env bash
# Create the three role profiles for a board and give each a role-only memory.
#
#   kanban-profiles.sh <prefix> [--clone-from <profile>] [--repo <path>]
#                      [--model-impl <provider>:<model>] [--model-review …] [--model-fix …]
#                      [--fallback-review <provider>:<model>] [--force-memory]
#
# Creates <prefix>impl, <prefix>review, <prefix>fix (skips any that exist).
# --clone-from  source profile (default: default). Cloning copies MEMORY.md
#               wholesale — that is why every NEW profile's MEMORY.md is rewritten
#               here, before its first card. Existing profiles keep their memory
#               unless --force-memory.
# --repo        named in the memory entries (default: current git top level).
# --model-*     pins model.provider/model.default in that profile. Rule: the
#               reviewer runs on a different model family than the author.
# --fallback-*  writes a top-level fallback_providers list in that profile.
# Prints what it did and each profile's configured model.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -ge 1 ] || { sed -n 2,17p "$0" >&2; exit 2; }
PREFIX="$1"; shift
CLONE=default; REPO=""; FORCE=0
declare -a MODELS=() FALLBACKS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --clone-from) CLONE="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --model-impl|--model-review|--model-fix) MODELS+=("${1#--model-}=$2"); shift 2 ;;
    --fallback-impl|--fallback-review|--fallback-fix) FALLBACKS+=("${1#--fallback-}=$2"); shift 2 ;;
    --force-memory) FORCE=1; shift ;;
    *) kanban_die "unknown option $1" 2 ;;
  esac
done
case "$PREFIX" in *[!a-z0-9]*|"") kanban_die "prefix must be lowercase alphanumeric: $PREFIX" 2 ;; esac
REPO="${REPO:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
[ -n "$REPO" ] || kanban_die "run inside the repository or pass --repo" 2
HH="${HERMES_HOME:-$HOME/.hermes}"

memory() {
  case "$1" in
    impl) cat <<EOF
Role: implementer for Kanban cards in $REPO. Work only in the card's workspace; local commits allowed, never push/merge/rebase/reset/amend/force.
§
Read the repo's agent rules and the card's ticket before editing. AGENTS.md / CLAUDE.md are write-protected: return the exact edit in the summary.
§
TDD with the RED run quoted; a manual mutation check for every new guarantee (break the line, see the failure, restore). No mutation helper scripts.
§
Obstacle → kanban_block, reason opening with the action and the path. Summary first line: START_HEAD..END_HEAD, N files, gate: green — nothing about intent.
EOF
      ;;
    review) cat <<EOF
Role: blind adversarial reviewer for $REPO. Read the diff and the repo's rules only; never tickets, plans, task files, the implementer's card or other cards' comments. Reconstruct intent from the diff.
§
Run the gate yourself; a summary is a self-report.
§
Hunt: tests that survive breaking their line; tests asserting something other than their name; undocumented assumptions; layer violations; leaks; races; injection; secrets.
§
State the commit range actually reviewed. Findings F1… with path:line, consequence, concrete fix, severity. Change nothing in the tree.
EOF
      ;;
    fix) cat <<EOF
Role: remediation for review findings in $REPO. Apply each finding with a test and a mutation check, or reject it with proof (output or the refuting line).
§
A finding that argues with a written decision (ADR, glossary) is not applied: report it as "needs decision".
§
A symptom-shaped finding is fixed on every path that produces it. Two failed attempts on one finding: stop, record, move on.
§
Local commits only; never push/merge/rebase/reset/amend/force. Summary first line: START_HEAD..END_HEAD, N files, gate: green.
EOF
      ;;
  esac
}

desc() {
  case "$1" in
    impl) echo "Implements and researches Kanban cards for $(basename "$REPO"): TDD, mutation checks, neutral summaries" ;;
    review) echo "Blind adversarial reviewer for $(basename "$REPO") cards: diff and repo rules only" ;;
    fix) echo "Applies or rejects review findings with proof for $(basename "$REPO") cards" ;;
  esac
}

lookup() { # role list -> value for role
  local role="$1"; shift; local kv
  for kv in "$@"; do [ "${kv%%=*}" = "$role" ] && { echo "${kv#*=}"; return; }; done
  return 0
}

for role in impl review fix; do
  name="$PREFIX$role"; dir="$HH/profiles/$name"; created=0
  if [ -d "$dir" ]; then
    echo "$name: exists"
  else
    hermes_cli profile create "$name" --clone-from "$CLONE" --no-alias --description "$(desc "$role")" >/dev/null
    [ -d "$dir" ] || kanban_die "profile create reported success but $dir is missing"
    created=1; echo "$name: created from $CLONE"
  fi
  if [ "$created" = 1 ] || [ "$FORCE" = 1 ]; then
    mkdir -p "$dir/memories"
    memory "$role" > "$dir/memories/MEMORY.md"
    echo "$name: MEMORY.md rewritten ($(wc -c < "$dir/memories/MEMORY.md" | tr -d ' ') bytes)"
  fi
  spec="$(lookup "$role" ${MODELS[@]+"${MODELS[@]}"})"
  if [ -n "$spec" ]; then
    case "$spec" in *:*) ;; *) kanban_die "--model-$role wants <provider>:<model>, got $spec" 2 ;; esac
    HERMES_HOME="$dir" hermes_cli config set model.provider "${spec%%:*}" >/dev/null
    HERMES_HOME="$dir" hermes_cli config set model.default "${spec#*:}" >/dev/null
  fi
  fb="$(lookup "$role" ${FALLBACKS[@]+"${FALLBACKS[@]}"})"
  if [ -n "$fb" ]; then
    case "$fb" in *:*) ;; *) kanban_die "--fallback-$role wants <provider>:<model>, got $fb" 2 ;; esac
    python3 - "$dir/config.yaml" "${fb%%:*}" "${fb#*:}" <<'PY'
import re, sys
path, prov, model = sys.argv[1:]
text = open(path).read() if __import__("os").path.exists(path) else ""
# Drop an existing top-level fallback_providers block, then append the new one.
text = re.sub(r"(?ms)^fallback_providers:\n(?:^[ \t-].*\n?)*", "", text).rstrip("\n")
text += f"\nfallback_providers:\n- provider: {prov}\n  model: {model}\n"
open(path, "w").write(text)
PY
  fi
  printf '%s: model %s via %s\n' "$name" \
    "$(HERMES_HOME="$dir" hermes_cli config get model.default 2>/dev/null | tail -1)" \
    "$(HERMES_HOME="$dir" hermes_cli config get model.provider 2>/dev/null | tail -1)"
done
echo "check routing after the first real card: grep 'OpenAI client created' $HH/profiles/${PREFIX}<role>/logs/agent.log"
