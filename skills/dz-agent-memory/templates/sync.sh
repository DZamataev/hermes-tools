#!/bin/bash
# One-way mirror of coding agents' memory and skills (Hermes, Claude Code, Codex)
# into this repository, then secret scan, commit and push. Installed and kept up
# to date by the dz-agent-memory skill; edits made in the mirrored folders are
# overwritten on the next run — edit in the source.
# Usage: sync.sh [--no-push]
# Env: AM_NOTIFY=0 disables the macOS notification when gitleaks blocks a commit.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="$(cd "$(dirname "$0")" && pwd)"
PUSH=1; [ "${1:-}" = "--no-push" ] && PUSH=0
LOCK="$REPO/.git/sync.lock"
mkdir "$LOCK" 2>/dev/null || { echo "$(date '+%F %T') sync already running"; exit 0; }
trap 'rmdir "$LOCK"' EXIT

R() { rsync -a --delete --delete-excluded --exclude .DS_Store --exclude '*.lock' "$@"; }
# Mirror dir $1 into $2 when it exists; drop the mirror when the source is gone.
RD() { local src="$1" dst="$2"; shift 2
  if [ -d "$src" ]; then mkdir -p "$dst"; R "$@" "$src/" "$dst/"; else rm -rf "$dst"; fi; }
CF() { if [ -f "$1" ]; then mkdir -p "$(dirname "$2")"; cp "$1" "$2"; else rm -f "$2"; fi; }

# --- hermes -------------------------------------------------------------
H="$HOME/.hermes"; D="$REPO/hermes"
if [ -d "$H" ]; then
  RD "$H/memories" "$D/memories"
  CF "$H/SOUL.md" "$D/SOUL.md"
  RD "$H/skills" "$D/skills" --exclude '.hub' --exclude '.curator_*' --exclude '.usage.json*' \
    --exclude '.locks' --exclude '.bundled_manifest' --exclude '__pycache__'
  # profiles: only their own memory and SOUL (their skills are copies of the main set)
  mkdir -p "$D/profiles"
  for p in "$H"/profiles/*/; do
    [ -d "$p" ] || continue
    n=$(basename "$p")
    RD "$p/memories" "$D/profiles/$n/memories"
    CF "$p/SOUL.md" "$D/profiles/$n/SOUL.md"
  done
  for d in "$D"/profiles/*/; do [ -d "$d" ] && { [ -d "$H/profiles/$(basename "$d")" ] || rm -rf "$d"; }; done
fi

# --- claude -------------------------------------------------------------
C="$HOME/.claude"; D="$REPO/claude"
if [ -d "$C" ]; then
  CF "$C/CLAUDE.md" "$D/CLAUDE.md"
  # skills that are symlinks into ~/.agents/skills live in shared/agents-skills
  RD "$C/skills" "$D/skills" -q --no-links
  for s in agents commands hooks; do
    if [ -n "$(ls -A "$C/$s" 2>/dev/null)" ]; then RD "$C/$s" "$D/$s"; else rm -rf "$D/$s"; fi
  done
  # per-project auto-memory; folder name without the -Users-<me>- prefix
  tmp=$(mktemp -d); prefix="-$(echo "$HOME" | tr / - | sed 's/^-//')-"
  for m in "$C"/projects/*/memory; do
    [ -d "$m" ] || continue
    n=$(basename "$(dirname "$m")"); n=${n#"$prefix"}
    mkdir -p "$tmp/$n"; cp -R "$m/" "$tmp/$n/"
  done
  mkdir -p "$D/projects"; R "$tmp/" "$D/projects/"; rm -rf "$tmp"
fi

# --- codex --------------------------------------------------------------
X="$HOME/.codex"; D="$REPO/codex"
if [ -d "$X" ]; then
  CF "$X/AGENTS.md" "$D/AGENTS.md"
  RD "$X/skills" "$D/skills" --exclude '.system'
  RD "$X/rules" "$D/rules"
  RD "$X/memories" "$D/memories"
fi

# --- shared (~/.agents/skills, symlinked from ~/.claude/skills) ---------
RD "$HOME/.agents/skills" "$REPO/shared/agents-skills"

# --- scan, commit, push -------------------------------------------------
cd "$REPO"
# Push whatever an earlier run committed but could not push (offline, auth).
push() {
  [ $PUSH = 1 ] && git remote get-url origin >/dev/null 2>&1 || return 0
  local br; br=$(git rev-parse --abbrev-ref HEAD)
  if ! git rev-parse -q --verify "refs/remotes/origin/$br" >/dev/null \
     || [ -n "$(git rev-list "origin/$br..HEAD" 2>/dev/null)" ]; then
    git push -q -u origin HEAD
  fi
}
git add -A
if git diff --cached --quiet; then push; echo "$(date '+%F %T') no changes"; exit 0; fi
if ! gitleaks git --staged --redact --no-banner -c .gitleaks.toml . >/dev/null 2>&1; then
  git reset -q
  [ "${AM_NOTIFY:-1}" = 0 ] || osascript -e 'display notification "gitleaks нашёл секрет — коммит отменён. Запусти sync.sh вручную." with title "agent-memory"' 2>/dev/null || true
  echo "$(date '+%F %T') SECRET FOUND: commit aborted; run: git add -A && gitleaks git --staged --redact -v -c .gitleaks.toml ."; exit 1
fi
changed=$(git diff --cached --name-only | awk -F/ '{print (NF>1 ? $1"/"$2 : $1)}' | sort -u | head -8 | paste -sd, - | sed 's/,/, /g')
n=$(git diff --cached --name-only | wc -l | tr -d ' ')
git commit -qm "sync: $n files — $changed"
push
echo "$(date '+%F %T') committed: $n files"
