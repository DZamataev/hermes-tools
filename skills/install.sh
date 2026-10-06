#!/usr/bin/env bash
# Install skills from this directory into a Hermes home by copying them
# (no symlinks back to this checkout).
#
#   ./install.sh [--hermes-home <dir>] [--category <name>] [--force] [skill...]
#
# Default: every skill here, into ${HERMES_HOME:-~/.hermes}/skills/software-development/.
# Keep dz-wrapup and dz-clean-worktrees in the same category: dz-wrapup calls
# the other's scripts through ../dz-clean-worktrees.
# --force replaces an existing copy; the old one goes to <hermes-home>/backups/skills/.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
CATEGORY=software-development
FORCE=0
NAMES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --hermes-home) HOME_DIR="$2"; shift 2 ;;
    --category) CATEGORY="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "install.sh: unknown argument $1" >&2; exit 2 ;;
    *) NAMES+=("$1"); shift ;;
  esac
done
for tool in python3 git; do
  command -v "$tool" >/dev/null || { echo "install.sh: $tool is required" >&2; exit 1; }
done
if [ ${#NAMES[@]} -eq 0 ]; then
  for d in "$SRC"/*/; do [ -f "$d/SKILL.md" ] && NAMES+=("$(basename "$d")"); done
fi

for name in "${NAMES[@]}"; do
  [ -f "$SRC/$name/SKILL.md" ] || { echo "install.sh: no skill $name here" >&2; exit 1; }
  DEST="$HOME_DIR/skills/$CATEGORY/$name"
  if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    [ "$FORCE" = 1 ] || { echo "install.sh: $DEST exists; re-run with --force to replace it" >&2; exit 1; }
    # Outside skills/: a copy left there would load as a second skill of the same name.
    BACKUP="$HOME_DIR/backups/skills/$name.$(date +%Y%m%d%H%M%S)"
    mkdir -p "$(dirname "$BACKUP")"
    mv "$DEST" "$BACKUP"
    echo "previous copy moved to $BACKUP"
  fi
  mkdir -p "$DEST"
  for item in SKILL.md scripts tests references templates; do
    [ -e "$SRC/$name/$item" ] && cp -R "$SRC/$name/$item" "$DEST/"
  done
  find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
  echo "installed: $DEST"
done
