#!/usr/bin/env bash
# Bench for dz-hermes-rollout: a throwaway fork with feature worktrees and a fake install, then
# checks merge.sh, the config precedence, deploy.sh's preconditions and live-turn detection.
# Touches nothing outside its mktemp sandbox; never starts or quits a Hermes app.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../scripts"
SB="$(mktemp -d)"; SB="$(cd "$SB" && pwd -P)"
trap 'rm -rf "$SB"' EXIT
FAILS=0
ok()  { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL="$SB/gitconfig" GIT_CONFIG_NOSYSTEM=1
git config --global init.defaultBranch main
git config --global commit.gpgsign false
commit() { echo "$3" > "$1/$2"; git -C "$1" add "$2"; git -C "$1" commit -qm "$4"; }

# upstream → fork (integration branch develop) → worktrees; install cloned from the fork.
git init -q "$SB/upstream"; commit "$SB/upstream" base.txt base "initial"
git clone -q "$SB/upstream" "$SB/fork" 2>/dev/null
git -C "$SB/fork" remote rename origin upstream
git -C "$SB/fork" checkout -q -b develop
WT="$SB/wt"; mkdir -p "$WT"
git -C "$SB/fork" worktree add -q -b feat/a "$WT/a"; commit "$WT/a" a.txt a "feat: a"
git -C "$SB/fork" worktree add -q -b feat/clean "$WT/clean"
git -C "$SB/fork" worktree add -q -b outside/x "$SB/elsewhere"; commit "$SB/elsewhere" x.txt x "feat: x"
commit "$SB/upstream" up.txt up "upstream change"
LIVE="$SB/live"; mkdir -p "$LIVE/logs"
git clone -q -b develop "$SB/fork" "$LIVE/hermes-agent" 2>/dev/null
mkdir -p "$LIVE/hermes-agent/.hermes/bin"
printf '#!/bin/sh\necho fake-hermes "$@"\n' > "$LIVE/hermes-agent/.hermes/bin/hermes"
chmod +x "$LIVE/hermes-agent/.hermes/bin/hermes"
echo ".hermes/" >> "$LIVE/hermes-agent/.git/info/exclude"

mkdir -p "$SB/cfg"
cat > "$SB/cfg/config.sh" <<EOF
ROLLOUT_FORK=$SB/fork
ROLLOUT_WT_ROOT=$WT
ROLLOUT_BRANCH=develop
ROLLOUT_LIVE_HOME=$LIVE
ROLLOUT_TEST_HOME=$SB/test-home
EOF
export ROLLOUT_CONFIG="$SB/cfg/config.sh" ROLLOUT_NO_NOTIFY=1 ROLLOUT_SKIP_SMOKE=1

# ── merge.sh ──
out=$("$S/merge.sh" --dry-run 2>&1)
check "dry run lists the worktree branch ahead"        '[[ $out == *"to merge: feat/a (+1)"* ]]'
check "dry run reports an up-to-date branch"           '[[ $out == *"up to date: feat/clean"* ]]'
check "dry run skips worktrees outside WT_ROOT"        '[[ $out != *"outside/x"* ]]'
check "dry run merges nothing"                         '[ ! -f "$SB/fork/a.txt" ]'
out=$(ROLLOUT_WT_ROOT= "$S/merge.sh" --dry-run 2>&1)
check "env var overrides the config file (all worktrees)" '[[ $out == *"to merge: outside/x"* ]]'
out=$("$S/merge.sh" --upstream 2>&1)
check "merge.sh succeeds"                              '[[ $out == *"MERGE OK"* ]]'
check "upstream merged into develop"                   '[ -f "$SB/fork/up.txt" ]'
check "feature branch merged into develop"             '[ -f "$SB/fork/a.txt" ]'
git -C "$WT/clean" checkout -q -b feat/conflict; commit "$WT/clean" a.txt other "conflicting a"
git -C "$WT/clean" checkout -q feat/clean; git -C "$WT/clean" reset -q --hard feat/conflict
out=$("$S/merge.sh" 2>&1)
check "a conflict fails with the branch named"         '[[ $out == *"MERGE FAILED: merge conflict: feat/clean"* ]]'
check "a conflict leaves develop clean (aborted)"      '[ -z "$(git -C "$SB/fork" status --porcelain --untracked-files=no)" ]'
git -C "$SB/fork" branch -q -D feat/conflict

# ── config errors ──
out=$(ROLLOUT_CONFIG="$SB/none.sh" ROLLOUT_FORK= "$S/merge.sh" --dry-run 2>&1)
check "missing config is reported"                     '[[ $out == *"ROLLOUT_FORK is not set"* ]]'

# ── deploy.sh preconditions (dry run: no side effects) ──
out=$("$S/deploy.sh" --dry-run 2>&1)
check "deploy dry run warns on an unverified sha"      '[[ $out == *"WARNING: develop"*"is not verified"* ]]'
check "deploy dry run plans the fast-forward"          '[[ $out == *"DEPLOY OK: dry run"* && $out == *"commit(s)"* ]]'
out=$("$S/deploy.sh" 2>&1)
check "deploy refuses an unverified sha"               '[[ $out == *"DEPLOY FAILED"*"is not verified"* ]]'
echo "$(git -C "$SB/fork" rev-parse develop) now tests=changed app=false" > "$LIVE/logs/rollout/verified"
out=$("$S/deploy.sh" --dry-run 2>&1)
check "deploy dry run accepts a verified sha"          '[[ $out == *"verified: "* && $out != *"not verified"* ]]'
commit "$LIVE/hermes-agent" local.txt l "install-only commit"
out=$("$S/deploy.sh" --dry-run 2>&1)
check "deploy refuses an install ahead of the fork"    '[[ $out == *"has commits that"* ]]'
git -C "$LIVE/hermes-agent" reset -q --hard HEAD~1
echo dirty >> "$SB/fork/base.txt"
out=$("$S/deploy.sh" --dry-run 2>&1)
check "deploy refuses a dirty fork"                    '[[ $out == *"has uncommitted changes"* ]]'
git -C "$SB/fork" checkout -q -- base.txt

# ── live_turns ──
H="$SB/turns"; mkdir -p "$H/logs" "$H/profiles/p1/logs"
now=$(date '+%Y-%m-%d %H:%M:%S'); old=$(date -v-20H '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -d '-20 hours' '+%Y-%m-%d %H:%M:%S')
cat > "$H/logs/agent.log" <<EOF
$now,100 INFO tui_gateway.server: tui prompt accepted: ui_session=a agent_session_id=S_DONE kind=user
$now,200 INFO [S_DONE] tui_gateway.server: tui turn finished: ui_session=a agent_session_id=S_DONE status=complete
$now,300 INFO tui_gateway.server: tui prompt accepted: ui_session=b agent_session_id=S_LIVE kind=user
$old,100 INFO tui_gateway.server: tui prompt accepted: ui_session=c agent_session_id=S_CRASHED kind=user
EOF
echo "$now,400 INFO tui_gateway.server: tui prompt accepted: ui_session=d agent_session_id=S_PROFILE kind=process_complete" > "$H/profiles/p1/logs/agent.log"
turns=$(bash -c '. "$1"; trap - EXIT; live_turns "$2"' _ "$S/lib.sh" "$H" | cut -d' ' -f1 | sort | tr '\n' ' ')
check "live_turns: open turns in home and profiles, not finished or stale ones" '[ "$turns" = "S_LIVE S_PROFILE " ]'
turns=$(bash -c '. "$1"; trap - EXIT; live_turns "$2"' _ "$S/lib.sh" "$SB/nohome")
check "live_turns: a home without logs has none"       '[ -z "$turns" ]'

echo
[ $FAILS -eq 0 ] && echo "all passed" || echo "$FAILS failed"
exit $FAILS
