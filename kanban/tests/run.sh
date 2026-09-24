#!/usr/bin/env bash
# Tests for the kanban scripts. No real board, profile or cron job is touched:
# a fake `hermes` on PATH records every call and answers from a JSON state file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../scripts"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
FAILS=0
ok()   { printf 'ok   %s\n' "$1"; }
bad()  { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ---- fake hermes -----------------------------------------------------------
mkdir -p "$SANDBOX/bin" "$SANDBOX/home/cron" "$SANDBOX/home/profiles"
export HERMES_HOME="$SANDBOX/home" FAKE_STATE="$SANDBOX/state.json" FAKE_LOG="$SANDBOX/calls.log"
echo '{"tasks": [], "next": 1, "subs": [], "jobs": []}' > "$FAKE_STATE"
cp "$HERE/fake_hermes.py" "$SANDBOX/bin/hermes"; chmod +x "$SANDBOX/bin/hermes"
export KANBAN_HERMES="$SANDBOX/bin/hermes"

# ---- a repo ------------------------------------------------------------------
R="$SANDBOX/repo"; mkdir -p "$R"; git -C "$R" init -q; git -C "$R" commit -q --allow-empty -m init
cd "$R"

# init
"$S/kanban-init.sh" demo-board >/dev/null
check "init writes config with the board" "grep -qx 'KANBAN_BOARD=demo-board' .kanban/config.env"
check "init derives the profile prefix" "grep -qx 'KANBAN_PROFILE_PREFIX=demoboar' .kanban/config.env"
check "init copies every role template" "for f in common research review fix gate coordinator; do test -f docs/agents/kanban-templates/\$f.md || exit 1; done"
check "init gitignores the notify env" "grep -qxF '.kanban/notify.env' .gitignore"
echo "# mine" > docs/hermes_kanban_development.md
"$S/kanban-init.sh" demo-board >/dev/null
check "init never overwrites" "grep -qx '# mine' docs/hermes_kanban_development.md"
check "init adds the gitignore line once" "[ \$(grep -cxF '.kanban/notify.env' .gitignore) = 1 ]"

sed -i.bak 's/^KANBAN_PROFILE_PREFIX=.*/KANBAN_PROFILE_PREFIX=demo/; s/^KANBAN_GATE=.*/KANBAN_GATE="pnpm verify"/; s/^KANBAN_GATE_OK=.*/KANBAN_GATE_OK="all steps passed"/' .kanban/config.env
printf 'KANBAN_NOTIFY_CHAT_ID=-100123\nKANBAN_NOTIFY_THREAD_ID=9\n' > .kanban/notify.env
echo "do the thing" > task.md

# card
ID="$("$S/kanban-card.sh" impl "Slice: implement" task.md "$R")"
check "card prints the id" "[ '$ID' = t_1 ]"
BODY="$(python3 -c 'import json;print(json.load(open("'"$FAKE_STATE"'"))["tasks"][0]["body"])')"
check "card body has the role preamble" "grep -q 'Standing constraints' <<<\"\$BODY\""
check "card body substitutes the workdir" "grep -q '$R' <<<\"\$BODY\" && ! grep -q '{{' <<<\"\$BODY\""
check "card body substitutes the gate" "grep -q 'pnpm verify' <<<\"\$BODY\""
check "card body ends with the task" "[ \"\$(tail -1 <<<\"\$BODY\")\" = 'do the thing' ]"
check "card goes to the impl profile" "grep -q 'create Slice: implement.*--assignee demoimpl' '$FAKE_LOG'"
check "card body goes through stdin" "grep -q -- '--body-file -' '$FAKE_LOG'"
check "card subscribes to the thread" "grep -q 'notify-subscribe t_1 .*--thread-id 9 --chat-type thread' '$FAKE_LOG'"
if "$S/kanban-card.sh" impl "x" task.md "$R" "" >/dev/null 2>&1; then bad "card refuses an empty parent"; else ok "card refuses an empty parent"; fi
if "$S/kanban-card.sh" impl "x" task.md relative/path >/dev/null 2>&1; then bad "card refuses a relative workdir"; else ok "card refuses a relative workdir"; fi
FAKE_NO_ID=1 "$S/kanban-card.sh" impl "x" task.md "$R" >/dev/null 2>&1 && bad "card fails when create returns no id" || ok "card fails when create returns no id"
GID="$("$S/kanban-card.sh" gate "G" task.md "$R")"
check "gate is created blocked with no assignee" "grep \"create G \" '$FAKE_LOG' | grep -q -- '--initial-status blocked' && ! grep \"create G \" '$FAKE_LOG' | grep -q -- '--assignee'"

# chain
: > "$FAKE_LOG"
OUT="$("$S/kanban-chain.py" --title "List" --task task.md --workdir "$R" --after "$ID" --gate-task task.md)"
check "chain prints four ids" "grep -Eq '^impl=t_[0-9]+ review=t_[0-9]+ fix=t_[0-9]+ gate=t_[0-9]+$' <<<'$OUT'"
check "chain creates every card blocked" "[ \$(grep -c 'create .*--initial-status blocked' '$FAKE_LOG') = 4 ]"
check "chain releases only after linking checks" "awk '/ unblock /{u=NR} / show /{s=NR} END{exit !(s<u)}' '$FAKE_LOG'"
check "chain never unblocks the gate" "! grep ' unblock ' '$FAKE_LOG' | grep -q \"\$(sed 's/.*gate=//' <<<'$OUT')\""
check "chain dispatches once" "[ \$(grep -c ' dispatch' '$FAKE_LOG') = 1 ]"
check "review card uses the review profile" "grep -q 'create List: review .*--assignee demoreview' '$FAKE_LOG'"
FAKE_WRONG_PARENT=1 "$S/kanban-chain.py" --title "Bad" --task task.md --workdir "$R" >/dev/null 2>&1 \
  && bad "chain stops when an edge is wrong" || ok "chain stops when an edge is wrong"
check "chain with a wrong edge releases nothing" "! grep -q 'unblock.*' <(sed -n '/create Bad/,\$p' '$FAKE_LOG')"

# monitor
check "monitor drill passes" "python3 '$S/kanban-monitor.py' --drill >/dev/null"
check "monitor reads the fake board" "python3 '$S/kanban-monitor.py' demo-board | grep -q '^DONE:0'"

# coordinator
echo "gate {{NO_SUCH_VALUE}}" > "$SANDBOX/bad.md"
if (. "$S/lib.sh"; kanban_render "$SANDBOX/bad.md") >/dev/null 2>&1; then bad "render fails on an unknown placeholder"; else ok "render fails on an unknown placeholder"; fi
check "coordinator prompt renders fully" "'$S/kanban-coordinator.sh' prompt | grep -q 'demo-board' && ! '$S/kanban-coordinator.sh' prompt | grep -q '{{'"
"$S/kanban-coordinator.sh" up >/dev/null
check "coordinator installs the detector with the board baked in" "grep -q \"BOARD = 'demo-board'\" '$HERMES_HOME/scripts/kanban_monitor_demo_board.py'"
check "coordinator creates a monitor-gated job to the thread" "grep -q 'cron create.*--deliver telegram:-100123:9.*--monitor-script kanban_monitor_demo_board.py' '$FAKE_LOG'"
"$S/kanban-coordinator.sh" up >/dev/null 2>&1 && bad "coordinator refuses a second job" || ok "coordinator refuses a second job"
"$S/kanban-coordinator.sh" down >/dev/null
check "coordinator down removes job and detector" "grep -q 'cron remove' '$FAKE_LOG' && [ ! -e '$HERMES_HOME/scripts/kanban_monitor_demo_board.py' ]"

# profiles
: > "$FAKE_LOG"
"$S/kanban-profiles.sh" demo --model-review codex:gpt-x --fallback-review other:gpt-x >/dev/null
check "profiles creates three roles" "[ \$(grep -c 'profile create' '$FAKE_LOG') = 3 ]"
check "profiles rewrites cloned memory" "grep -q 'blind adversarial reviewer' '$HERMES_HOME/profiles/demoreview/memories/MEMORY.md' && ! grep -q INHERITED '$HERMES_HOME/profiles/demoreview/memories/MEMORY.md'"
check "profiles pins the reviewer model" "grep -q 'config set model.default gpt-x' '$FAKE_LOG'"
check "profiles writes the fallback block" "grep -q -- '- provider: other' '$HERMES_HOME/profiles/demoreview/config.yaml'"
echo "KEEP" > "$HERMES_HOME/profiles/demoimpl/memories/MEMORY.md"
"$S/kanban-profiles.sh" demo >/dev/null
check "profiles keeps an existing memory" "grep -qx KEEP '$HERMES_HOME/profiles/demoimpl/memories/MEMORY.md'"

# the package stands alone
PKG="$(cd "$HERE/.." && pwd)"
check "package names no machine-specific path" "! grep -rnE '/Users/|~/dev/|/home/[a-z]' '$PKG' --include='*.md' --include='*.sh' --include='*.py' --include='*.env' --include='*.example' | grep -v '/tests/run.sh:'"
check "package has no symlinks" "[ -z \"\$(find '$PKG' -type l)\" ]"
check "SKILL.md links only files inside the package" "python3 - '$PKG' <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
bad = []
for md in root.rglob('*.md'):
    for target in re.findall(r'\]\(([^)#]+)\)', md.read_text()):
        if '://' in target: continue
        if not (md.parent / target).resolve().is_relative_to(root) or not (md.parent / target).exists():
            bad.append(f'{md.relative_to(root)} -> {target}')
sys.exit('\n'.join(bad) if bad else 0)
PY"
INST="$SANDBOX/inst-home"
bash "$PKG/install.sh" --hermes-home "$INST" >/dev/null
IDIR="$INST/skills/software-development/hermes-kanban-development"
check "install copies, not links" "[ -f '$IDIR/SKILL.md' ] && [ ! -L '$IDIR' ] && [ -z \"\$(find '$IDIR' -type l)\" ]"
check "installed scripts are executable" "[ -x '$IDIR/scripts/kanban-card.sh' ] && [ -x '$IDIR/scripts/kanban-chain.py' ]"
check "installed copy has references and templates" "[ -f '$IDIR/references/pitfalls.md' ] && [ -f '$IDIR/templates/roles/review.md' ]"
if bash "$PKG/install.sh" --hermes-home "$INST" >/dev/null 2>&1; then bad "install refuses to clobber without --force"; else ok "install refuses to clobber without --force"; fi
bash "$PKG/install.sh" --hermes-home "$INST" --force >/dev/null
check "install --force backs up outside skills/" "ls -d '$INST/backups/skills/hermes-kanban-development.'* >/dev/null 2>&1 && [ -z \"\$(find '$INST/skills' -maxdepth 2 -name 'hermes-kanban-development.*')\" ]"
R2="$SANDBOX/repo2"; mkdir -p "$R2"; git -C "$R2" init -q
check "installed copy scaffolds a repo on its own" "(cd '$R2' && bash '$IDIR/scripts/kanban-init.sh' other-board >/dev/null) && [ -f '$R2/.kanban/config.env' ]"

echo
[ "$FAILS" = 0 ] && echo "all kanban tests passed" || { echo "$FAILS failed"; exit 1; }
