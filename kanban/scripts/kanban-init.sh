#!/usr/bin/env bash
# Scaffold the Kanban development setup into a repository. Never overwrites.
#
#   kanban-init.sh <board-slug> [repo] [--prefix <profile-prefix>]
#
# Writes (each only when missing):
#   .kanban/config.env                 board, profile prefix, gate, base branch
#   .kanban/notify.env.example         notify target variable names, empty
#   <templates>/{common,research,review,fix,gate,coordinator}.md   role preambles
#   <templates>/tasks/                 per-card task bodies live here
#   docs/hermes_kanban_development.md  the per-repo runbook skeleton
# and adds .kanban/notify.env to .gitignore.
# Then: edit config.env (KANBAN_GATE!), the templates' repo sections and the runbook.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

[ $# -ge 1 ] || { sed -n 2,14p "$0" >&2; exit 2; }
BOARD="$1"; shift
REPO=""; PREFIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    -*) kanban_die "unknown option $1" 2 ;;
    *) REPO="$1"; shift ;;
  esac
done
REPO="${REPO:-$(git rev-parse --show-toplevel 2>/dev/null || true)}"
[ -n "$REPO" ] && [ -d "$REPO" ] || kanban_die "repository not found (pass it as the second argument)" 2
REPO="$(cd "$REPO" && pwd)"
case "$BOARD" in *[!a-z0-9-]*|"") kanban_die "board slug must be lowercase letters, digits and dashes: $BOARD" 2 ;; esac
PREFIX="${PREFIX:-$(printf '%s' "$BOARD" | tr -cd 'a-z0-9' | cut -c1-8)}"
TPL_SRC="$KANBAN_SKILL_DIR/templates"

put() { # src dst [render]
  local src="$1" dst="$2"
  if [ -e "$dst" ]; then echo "kept     ${dst#$REPO/}"; return; fi
  mkdir -p "$(dirname "$dst")"
  if [ "${3:-}" = render ]; then
    KANBAN_BOARD="$BOARD" KANBAN_PROFILE_PREFIX="$PREFIX" kanban_render "$src" > "$dst"
  else
    cp "$src" "$dst"
  fi
  echo "created  ${dst#$REPO/}"
}

put "$TPL_SRC/config.env" "$REPO/.kanban/config.env" render
put "$TPL_SRC/notify.env.example" "$REPO/.kanban/notify.env.example"
# shellcheck disable=SC1091
TEMPLATES="$(set -a; . "$REPO/.kanban/config.env"; printf %s "${KANBAN_TEMPLATES:-docs/agents/kanban-templates}")"
for f in common research review fix gate coordinator; do
  put "$TPL_SRC/roles/$f.md" "$REPO/$TEMPLATES/$f.md"
done
mkdir -p "$REPO/$TEMPLATES/tasks"
[ -e "$REPO/$TEMPLATES/tasks/.gitkeep" ] || : > "$REPO/$TEMPLATES/tasks/.gitkeep"
put "$TPL_SRC/runbook.md" "$REPO/docs/hermes_kanban_development.md"

if ! grep -qxF '.kanban/notify.env' "$REPO/.gitignore" 2>/dev/null; then
  printf '\n# Kanban notification target (names a private chat)\n.kanban/notify.env\n' >> "$REPO/.gitignore"
  echo "updated  .gitignore"
fi

cat <<EOF

next:
  1. edit .kanban/config.env — KANBAN_GATE and KANBAN_GATE_OK at least
  2. cp .kanban/notify.env.example .kanban/notify.env and fill it (gitignored)
  3. fill the repo sections of $TEMPLATES/*.md and docs/hermes_kanban_development.md
  4. bash $KANBAN_SCRIPTS/kanban-profiles.sh $PREFIX --repo $REPO
  5. hermes kanban boards create $BOARD --name "<title>"
EOF
