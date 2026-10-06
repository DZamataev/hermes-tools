#!/usr/bin/env bash
# Bench for dz-agent-memory. Runs everything against a fake $HOME (agent homes
# built here), a bare git remote, a fake launchctl and a temp LaunchAgents dir.
# Never touches the real ~/.hermes, ~/.claude, ~/.codex or launchd.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AM="$HERE/../scripts/am.py"
SB="$(mktemp -d)"; SB="$(cd "$SB" && pwd -P)"; trap 'rm -rf "$SB"' EXIT
FAILS=0
ok()  { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export HOME="$SB/home" AM_LAUNCH_AGENTS="$SB/LaunchAgents" AM_LOG="$SB/sync.log" AM_NOTIFY=0
export AM_LAUNCHCTL="$SB/launchctl" AM_HERMES_CONFIG="$SB/home/.hermes/config.yaml"
export GIT_CONFIG_GLOBAL="$SB/gitconfig" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git config --global init.defaultBranch main; git config --global commit.gpgsign false
cat > "$AM_LAUNCHCTL" <<EOF
#!/bin/sh
echo "\$*" >> "$SB/launchctl.log"
case "\$1" in list) [ -f "$SB/loaded" ] && { echo '{ "LastExitStatus" = 0; };'; exit 0; }; exit 113 ;;
  bootstrap) touch "$SB/loaded" ;; bootout) rm -f "$SB/loaded" ;; esac
EOF
chmod +x "$AM_LAUNCHCTL"

# ---- fake agent homes ---------------------------------------------------------
H="$HOME/.hermes"
mkdir -p "$H/memories" "$H/skills/devops/alpha/scripts" "$H/skills/.hub" "$H/profiles/impl/memories" "$H/profiles/impl/skills/x"
printf 'fact one\nsecond line of fact one\n§\nfact two' > "$H/memories/MEMORY.md"
printf 'user is Denis' > "$H/memories/USER.md"
printf 'memory:\n  memory_char_limit: 100\n  user_char_limit: 50\n' > "$H/config.yaml"
printf -- '---\nname: alpha\n---\nbody\n' > "$H/skills/devops/alpha/SKILL.md"
echo 'echo hi' > "$H/skills/devops/alpha/scripts/run.sh"
head -c 2000 /dev/urandom > "$H/skills/.hub/index.bin"
echo 'cache' > "$H/skills/.curator_state"
printf 'role memory' > "$H/profiles/impl/memories/MEMORY.md"
echo 'profile skill copy' > "$H/profiles/impl/skills/x/SKILL.md"
mkdir -p "$HOME/.agents/skills/shared-one" "$HOME/.claude/skills" "$HOME/.claude/projects/-$(echo "$HOME" | tr / - | sed 's/^-//')-dev-app/memory"
printf -- '---\nname: shared-one\n---\n' > "$HOME/.agents/skills/shared-one/SKILL.md"
ln -s "$HOME/.agents/skills/shared-one" "$HOME/.claude/skills/shared-one"
echo 'claude rules' > "$HOME/.claude/CLAUDE.md"
echo 'app memory' > "$HOME/.claude/projects/-$(echo "$HOME" | tr / - | sed 's/^-//')-dev-app/memory/MEMORY.md"
# no ~/.codex at all: must be skipped

git init -q --bare "$SB/remote.git"
REPO="$HOME/dev/agent-memory"

# ---- setup ---------------------------------------------------------------------
python3 "$AM" setup "$REPO" --remote "$SB/remote.git" --push > "$SB/setup.txt" 2>&1; rc=$?
check "setup exits 0 (rc=$rc)" "[ $rc -eq 0 ]"
check "sync.sh, README, gitleaks, gitignore installed" "[ -x '$REPO/sync.sh' ] && [ -f '$REPO/README.md' ] && [ -f '$REPO/.gitleaks.toml' ] && [ -f '$REPO/.gitignore' ]"
check "README carries the interval" "grep -q 'every 2 h' '$REPO/README.md'"
check "hermes memory mirrored" "grep -q 'fact two' '$REPO/hermes/memories/MEMORY.md'"
check "skill with scripts mirrored" "[ -f '$REPO/hermes/skills/devops/alpha/scripts/run.sh' ]"
check "hub and curator caches excluded" "[ ! -e '$REPO/hermes/skills/.hub' ] && [ ! -e '$REPO/hermes/skills/.curator_state' ]"
check "profile memory mirrored, profile skills not" "[ -f '$REPO/hermes/profiles/impl/memories/MEMORY.md' ] && [ ! -e '$REPO/hermes/profiles/impl/skills' ]"
check "claude symlinked skill not duplicated, shared mirrored" "[ ! -e '$REPO/claude/skills/shared-one' ] && [ -f '$REPO/shared/agents-skills/shared-one/SKILL.md' ]"
check "claude project memory under a short name" "[ -f '$REPO/claude/projects/dev-app/MEMORY.md' ]"
check "missing ~/.codex skipped, not an error" "[ ! -e '$REPO/codex' ]"
check "first sync committed and pushed" "[ \"\$(git --git-dir='$SB/remote.git' rev-parse main)\" = \"\$(git -C '$REPO' rev-parse HEAD)\" ]"
check "launchd plist written and loaded" "[ -f '$AM_LAUNCH_AGENTS/com.dzamataev.agent-memory-sync.plist' ] && [ -f '$SB/loaded' ] && grep -q '<integer>7200</integer>' '$AM_LAUNCH_AGENTS/com.dzamataev.agent-memory-sync.plist'"

python3 "$AM" status "$REPO" > "$SB/status.txt"; rc=$?
check "status clean after setup (rc=$rc)" "[ $rc -eq 0 ] && grep -q 'problems: none' '$SB/status.txt'"

# ---- re-run setup: idempotent ----------------------------------------------------
n0=$(git -C "$REPO" rev-list --count HEAD)
python3 "$AM" setup "$REPO" --remote "$SB/remote.git" > "$SB/setup2.txt" 2>&1
check "second setup: no new commit, files same, schedule unchanged" "[ \$(git -C '$REPO' rev-list --count HEAD) = $n0 ] && grep -q 'sync.sh *same' '$SB/setup2.txt' && grep -q 'schedule unchanged' '$SB/setup2.txt'"
check "a different remote is refused" "! python3 '$AM' setup '$REPO' --remote '$SB/other.git' >/dev/null 2>&1"

# ---- a change flows through ----------------------------------------------------------
sleep 1; BASE_TIME="$(date '+%Y-%m-%d %H:%M:%S')"; sleep 1
printf 'fact one\nsecond line of fact one, edited\n§\nfact three, much longer than the others to fill the limit' > "$H/memories/MEMORY.md"
rm -rf "$H/skills/devops/alpha"; mkdir -p "$H/skills/devops/beta"; printf -- '---\nname: beta\n---\n' > "$H/skills/devops/beta/SKILL.md"
bash "$REPO/sync.sh" >> "$AM_LOG" 2>&1
check "removed skill removed from mirror" "[ ! -e '$REPO/hermes/skills/devops/alpha' ] && [ -f '$REPO/hermes/skills/devops/beta/SKILL.md' ]"
check "change pushed" "[ \"\$(git --git-dir='$SB/remote.git' rev-parse main)\" = \"\$(git -C '$REPO' rev-parse HEAD)\" ]"
check "commit message lists changed areas" "git -C '$REPO' log -1 --format=%s | grep -q 'hermes/memories'"
bash "$REPO/sync.sh" > "$SB/nochange.txt" 2>&1
check "no change → no commit" "grep -q 'no changes' '$SB/nochange.txt'"
echo 'offline edit' >> "$H/memories/USER.md"
bash "$REPO/sync.sh" --no-push > /dev/null 2>&1
check "--no-push leaves the commit local" "[ -n \"\$(git -C '$REPO' rev-list origin/main..HEAD)\" ]"
bash "$REPO/sync.sh" > /dev/null 2>&1
check "next run with no changes pushes the leftover commit" "[ \"\$(git --git-dir='$SB/remote.git' rev-parse main)\" = \"\$(git -C '$REPO' rev-parse HEAD)\" ]"

# ---- review ---------------------------------------------------------------------------
python3 "$AM" review "$REPO" --since "$BASE_TIME" --json > "$SB/review.json"
r() { python3 -c "import json,sys; d=json.load(open('$SB/review.json')); print($1)"; }
check "review: memory entry added and removed" "r '[m[\"added\"] for m in d[\"memory\"] if m[\"file\"]==\"hermes/memories/MEMORY.md\"]' | grep -q 'fact three, much longer'  && r 'd[\"memory\"][0][\"removed\"]' | grep -q 'fact two'"
check "review: an edited multi-line entry is one entry, removed and added whole" "[ \"\$(r 'len([m for m in d[\"memory\"] if m[\"file\"]==\"hermes/memories/MEMORY.md\"][0][\"added\"])')\" = 2 ] && r 'd[\"memory\"][0][\"added\"]' | grep -q 'fact one.nsecond line of fact one, edited'"
check "review: skill added and removed" "r 'd[\"skills\"][\"Hermes\"]' | grep -q \"'added': \\['devops/beta'\\]\" && r 'd[\"skills\"][\"Hermes\"]' | grep -q \"'removed': \\['devops/alpha'\\]\""
check "review: fill against the configured limit" "[ \"\$(r '[f[\"limit\"] for f in d[\"fill\"]]')\" = '[100, 50]' ] && [ \"\$(r 'd[\"fill\"][0][\"pct\"]')\" -gt 60 ]"
python3 "$AM" review "$REPO" --since 30d > "$SB/review.txt"
check "review text has the sections" "grep -q '== memory' '$SB/review.txt' && grep -q '== skills' '$SB/review.txt'"

# ---- secrets are blocked ----------------------------------------------------------------
before=$(git -C "$REPO" rev-parse HEAD)
tok="ghp_$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 36)"
echo "token: $tok" >> "$H/memories/USER.md"
bash "$REPO/sync.sh" > "$SB/leak.txt" 2>&1; rc=$?
check "a real-looking token blocks the commit" "[ $rc -ne 0 ] && grep -q 'SECRET FOUND' '$SB/leak.txt' && [ \"\$(git -C '$REPO' rev-parse HEAD)\" = '$before' ]"
check "blocked sync leaves nothing staged" "[ -z \"\$(git -C '$REPO' diff --cached --name-only)\" ]"
echo "SECRET FOUND: commit aborted" >> "$AM_LOG"
python3 "$AM" status "$REPO" > "$SB/status-leak.txt"; rc=$?
check "status reports the blocked sync" "[ $rc -ne 0 ] && grep -q 'gitleaks blocked' '$SB/status-leak.txt'"
printf 'user is Denis' > "$H/memories/USER.md"
echo "token: definitely-not-a-real-token" >> "$H/skills/devops/beta/SKILL.md"
bash "$REPO/sync.sh" > "$SB/allow.txt" 2>&1; rc=$?
check "the allowlisted placeholder passes" "[ $rc -eq 0 ] && grep -q committed '$SB/allow.txt'"

# ---- status catches problems ----------------------------------------------------------------
mkdir "$REPO/.git/sync.lock"; touch -t 202001010000 "$REPO/.git/sync.lock"
echo "# local tweak" >> "$REPO/sync.sh"
git -C "$REPO" commit -qam "local commit"   # not pushed
python3 "$AM" status "$REPO" > "$SB/status-bad.txt"; rc=$?
check "status: stale lock" "grep -q 'stale lock' '$SB/status-bad.txt'"
check "status: unpushed commit" "grep -q '1 commit(s) not pushed' '$SB/status-bad.txt'"
check "status: sync.sh drift" "grep -q 'sync.sh differs' '$SB/status-bad.txt'"
check "status exits non-zero on problems" "[ $rc -ne 0 ]"
rmdir "$REPO/.git/sync.lock"
python3 "$AM" setup "$REPO" --remote "$SB/remote.git" > "$SB/setup3.txt" 2>&1
check "setup restores sync.sh, old copy kept inside .git (not committed)" "grep -q 'sync.sh *updated' '$SB/setup3.txt' && ls '$REPO'/.git/am-backups/sync.sh.* >/dev/null 2>&1 && ! ls '$REPO'/sync.sh.* >/dev/null 2>&1 && ! grep -q 'local tweak' '$REPO/sync.sh'"

python3 "$AM" unschedule "$REPO" > /dev/null
check "unschedule removes the plist and keeps the repo" "[ ! -e '$AM_LAUNCH_AGENTS/com.dzamataev.agent-memory-sync.plist' ] && [ ! -f '$SB/loaded' ] && [ -d '$REPO/.git' ]"
python3 "$AM" status "$REPO" > "$SB/status-unsched.txt"
check "status: not scheduled" "grep -q 'not scheduled' '$SB/status-unsched.txt'"

# ---- setup refuses a non-empty non-repo ---------------------------------------------------------
mkdir -p "$SB/junk"; echo x > "$SB/junk/file"
check "setup refuses a non-empty directory that is not a repo" "! python3 '$AM' setup '$SB/junk' --no-schedule >/dev/null 2>&1 && [ ! -e '$SB/junk/.git' ]"

echo
[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
