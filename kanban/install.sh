#!/usr/bin/env bash
# Install this skill into a Hermes home by copying it (no symlinks, no link back
# to wherever this copy came from).
#
#   ./install.sh [--hermes-home <dir>] [--category <name>] [--force]
#
# Default target: ${HERMES_HOME:-~/.hermes}/skills/software-development/hermes-kanban-development
# --force replaces an existing copy; the old one goes to <hermes-home>/backups/skills/.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
CATEGORY=software-development
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --hermes-home) HOME_DIR="$2"; shift 2 ;;
    --category) CATEGORY="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done

for tool in bash python3 git; do
  command -v "$tool" >/dev/null || { echo "install.sh: $tool is required" >&2; exit 1; }
done
command -v hermes >/dev/null || echo "note: 'hermes' is not on PATH; the scripts need it at run time" >&2

DEST="$HOME_DIR/skills/$CATEGORY/hermes-kanban-development"
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  [ "$FORCE" = 1 ] || { echo "install.sh: $DEST exists; re-run with --force to replace it" >&2; exit 1; }
  # Outside skills/: a copy left there would load as a second skill of the same name.
  BACKUP="$HOME_DIR/backups/skills/hermes-kanban-development.$(date +%Y%m%d%H%M%S)"
  mkdir -p "$(dirname "$BACKUP")"
  mv "$DEST" "$BACKUP"
  echo "previous copy moved to $BACKUP"
fi

mkdir -p "$DEST"
for item in SKILL.md README.md LICENSE install.sh scripts templates references; do
  [ -e "$SRC/$item" ] && cp -R "$SRC/$item" "$DEST/"
done
chmod +x "$DEST"/install.sh "$DEST"/scripts/*.sh "$DEST"/scripts/*.py
echo "installed: $DEST"
echo "next, in a repository: bash $DEST/scripts/kanban-init.sh <board-slug>"
