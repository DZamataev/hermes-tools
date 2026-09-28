#!/usr/bin/env bash
# Tests for the kanban scripts. No real board or profile is touched:
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
mkdir -p "$SANDBOX/bin" "$SANDBOX/home/profiles"
export HERMES_HOME="$SANDBOX/home" FAKE_STATE="$SANDBOX/state.json" FAKE_LOG="$SANDBOX/calls.log"
echo '{"tasks": [], "next": 1, "subs": [], "jobs": []}' > "$FAKE_STATE"
cp "$HERE/fake_hermes.py" "$SANDBOX/bin/hermes"; chmod +x "$SANDBOX/bin/hermes"
export KANBAN_HERMES="$SANDBOX/bin/hermes"
# The suite may itself run inside a Hermes session: start from "no calling session".
unset HERMES_SESSION_KEY HERMES_SESSION_PLATFORM HERMES_SESSION_CHAT_ID HERMES_CRON_SESSION HERMES_KANBAN_TASK \
  KANBAN_NOTIFY_SESSION

# ---- a repo ------------------------------------------------------------------
R="$SANDBOX/repo"; mkdir -p "$R"; git -C "$R" init -q; git -C "$R" commit -q --allow-empty -m init
cd "$R"

# init
"$S/kanban-init.sh" demo-board >/dev/null
check "init writes config with the board" "grep -qx 'KANBAN_BOARD=demo-board' .kanban/config.env"
check "init derives the profile prefix" "grep -qx 'KANBAN_PROFILE_PREFIX=demoboar' .kanban/config.env"
check "init copies every role template" "for f in common research review fix gate; do test -f docs/agents/kanban-templates/\$f.md || exit 1; done"
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
check "impl card carries the strict local-commit contract" "grep 'create Slice: implement' '$FAKE_LOG' | grep -qE -- '--completion-contract local-commit( |\$)'"
: > "$FAKE_LOG"
for role in fix research review; do "$S/kanban-card.sh" "$role" "C-$role" task.md "$R" >/dev/null; done
check "fix card may close a clean review without a commit" "grep 'create C-fix ' '$FAKE_LOG' | grep -qE -- '--completion-contract local-commit-or-none( |\$)'"
check "research and review cards carry no contract" "! grep -E 'create C-(research|review) ' '$FAKE_LOG' | grep -q -- '--completion-contract'"
body_of() { python3 -c 'import json,sys;print(next(t["body"] for t in json.load(open(sys.argv[1]))["tasks"] if t["title"]==sys.argv[2]))' "$FAKE_STATE" "$1"; }
check "impl, fix and research cards tell how to ask the orchestrator and wait" "grep -q 'await_reply_minutes=10' <<<\"\$BODY\" && body_of C-fix | grep -q 'await_reply_minutes=10' && body_of C-research | grep -q 'await_reply_minutes=10'"
check "a review card forbids asking about intent" "body_of C-review | grep -q 'Do not ask the orchestrator about intent' && ! body_of C-review | grep -q 'await_reply_minutes='"
if "$S/kanban-card.sh" impl "x" task.md "$R" "" >/dev/null 2>&1; then bad "card refuses an empty parent"; else ok "card refuses an empty parent"; fi
if "$S/kanban-card.sh" impl "x" task.md relative/path >/dev/null 2>&1; then bad "card refuses a relative workdir"; else ok "card refuses a relative workdir"; fi
FAKE_NO_ID=1 "$S/kanban-card.sh" impl "x" task.md "$R" >/dev/null 2>&1 && bad "card fails when create returns no id" || ok "card fails when create returns no id"
GID="$("$S/kanban-card.sh" gate "G" task.md "$R")"
check "gate is created blocked with no assignee" "grep \"create G \" '$FAKE_LOG' | grep -q -- '--initial-status blocked' && ! grep \"create G \" '$FAKE_LOG' | grep -q -- '--assignee'"
check "card outside a session subscribes no session" "! grep -q -- '--platform tui' '$FAKE_LOG'"

# card: the calling desktop/TUI session is subscribed too
: > "$FAKE_LOG"
SID="$(HERMES_SESSION_KEY=sess-orch "$S/kanban-card.sh" impl "S" task.md "$R")"
check "card subscribes the calling session" "grep -q \"notify-subscribe $SID --platform tui --chat-id sess-orch\" '$FAKE_LOG'"
check "card keeps the notify target next to the session" "[ \$(grep -c \"notify-subscribe $SID \" '$FAKE_LOG') = 2 ]"
: > "$FAKE_LOG"
HERMES_SESSION_KEY=sess-orch KANBAN_NOTIFY_SESSION=0 "$S/kanban-card.sh" impl "S" task.md "$R" >/dev/null
check "KANBAN_NOTIFY_SESSION=0 skips the session" "! grep -q -- '--platform tui' '$FAKE_LOG'"
HERMES_SESSION_KEY=sess-cron HERMES_CRON_SESSION=1 "$S/kanban-card.sh" impl "S" task.md "$R" >/dev/null
check "a cron session is not subscribed" "! grep -q -- 'sess-cron' '$FAKE_LOG'"
HERMES_SESSION_KEY=sess-worker HERMES_KANBAN_TASK=t_9 "$S/kanban-card.sh" impl "S" task.md "$R" >/dev/null
check "a board worker's session is not subscribed" "! grep -q -- 'sess-worker' '$FAKE_LOG'"
HERMES_SESSION_KEY=sess-tg HERMES_SESSION_PLATFORM=telegram HERMES_SESSION_CHAT_ID=42 \
  "$S/kanban-card.sh" impl "S" task.md "$R" >/dev/null
check "a gateway session is left to the notify target" "! grep -q -- 'sess-tg' '$FAKE_LOG'"
rm -f .kanban/notify.env.off; mv .kanban/notify.env .kanban/notify.env.off
: > "$FAKE_LOG"
NID="$(HERMES_SESSION_KEY=sess-orch "$S/kanban-card.sh" impl "S" task.md "$R")"
check "the session is subscribed with no notify target configured" "[ \$(grep -c \"notify-subscribe $NID \" '$FAKE_LOG') = 1 ] && grep -q \"notify-subscribe $NID --platform tui\" '$FAKE_LOG'"
mv .kanban/notify.env.off .kanban/notify.env

# chain
: > "$FAKE_LOG"
OUT="$("$S/kanban-chain.py" --no-session --title "List" --task task.md --workdir "$R" --after "$ID" --gate-task task.md)"
check "chain prints four ids" "grep -Eq '^impl=t_[0-9]+ review=t_[0-9]+ fix=t_[0-9]+ gate=t_[0-9]+$' <<<'$OUT'"
check "chain creates every card blocked" "[ \$(grep -c 'create .*--initial-status blocked' '$FAKE_LOG') = 4 ]"
check "chain releases only after linking checks" "awk '/ unblock /{u=NR} / show /{s=NR} END{exit !(s<u)}' '$FAKE_LOG'"
check "chain never unblocks the gate" "! grep ' unblock ' '$FAKE_LOG' | grep -q \"\$(sed 's/.*gate=//' <<<'$OUT')\""
check "chain dispatches once" "[ \$(grep -c ' dispatch' '$FAKE_LOG') = 1 ]"
check "review card uses the review profile" "grep -q 'create List: review .*--assignee demoreview' '$FAKE_LOG'"
FAKE_WRONG_PARENT=1 "$S/kanban-chain.py" --no-session --title "Bad" --task task.md --workdir "$R" >/dev/null 2>&1 \
  && bad "chain stops when an edge is wrong" || ok "chain stops when an edge is wrong"
check "chain with a wrong edge releases nothing" "! grep -q 'unblock.*' <(sed -n '/create Bad/,\$p' '$FAKE_LOG')"

# chain from an orchestrator session: every card reports back to it
: > "$FAKE_LOG"
OUT="$(HERMES_SESSION_KEY=sess-orch "$S/kanban-chain.py" --title "Sess" --task task.md --workdir "$R" --gate-task task.md)"
check "chain subscribes the calling session to every card" "[ \$(grep -c 'notify-subscribe .*--platform tui --chat-id sess-orch' '$FAKE_LOG') = 4 ]"
check "chain reports the subscribed session" "grep -q 'session=sess-orch' <<<'$OUT'"
: > "$FAKE_LOG"
FAKE_SUB_FAIL=tui HERMES_SESSION_KEY=sess-orch "$S/kanban-chain.py" --title "NoSub" --task task.md --workdir "$R" >/dev/null 2>&1 \
  && bad "chain stops when the session subscription fails" || ok "chain stops when the session subscription fails"
check "chain with a failed session subscription releases nothing" "! grep -q ' unblock ' '$FAKE_LOG'"
# a chain launched where the session key did not reach (execute_code used to strip it) would run unheard
: > "$FAKE_LOG"
"$S/kanban-chain.py" --title "Deaf" --task task.md --workdir "$R" 2>"$SANDBOX/deaf.err" >/dev/null \
  && bad "chain refuses to run without a session key" || ok "chain refuses to run without a session key"
check "the refusal names the way out" "grep -q -- '--no-session' '$SANDBOX/deaf.err'"
check "a refused chain creates nothing" "! grep -q ' create ' '$FAKE_LOG'"
HERMES_SESSION_KEY=sess-tg HERMES_SESSION_PLATFORM=telegram "$S/kanban-chain.py" --title "Gw" --task task.md --workdir "$R" >/dev/null 2>&1 \
  && ok "a gateway session (key present, deliberately not subscribed) is not refused" \
  || bad "a gateway session (key present, deliberately not subscribed) is not refused"

# render
echo "gate {{NO_SUCH_VALUE}}" > "$SANDBOX/bad.md"
if (. "$S/lib.sh"; kanban_render "$SANDBOX/bad.md") >/dev/null 2>&1; then bad "render fails on an unknown placeholder"; else ok "render fails on an unknown placeholder"; fi

# profiles
: > "$FAKE_LOG"
"$S/kanban-profiles.sh" demo --model-review codex:gpt-x --fallback-review other:gpt-x >/dev/null
check "profiles creates three roles" "[ \$(grep -c 'profile create' '$FAKE_LOG') = 3 ]"
check "profiles rewrites cloned memory" "grep -q 'blind adversarial reviewer' '$HERMES_HOME/profiles/demoreview/memories/MEMORY.md' && ! grep -q INHERITED '$HERMES_HOME/profiles/demoreview/memories/MEMORY.md'"
check "profiles pins the reviewer model" "grep -q 'config set model.default gpt-x' '$FAKE_LOG'"
check "profiles writes the fallback block" "grep -q -- '- provider: other' '$HERMES_HOME/profiles/demoreview/config.yaml'"
check "profiles sets worker_fallback=wait on impl and fix" "grep -qx 'kanban.worker_fallback=wait' '$HERMES_HOME/profiles/demoimpl/config.set' 2>/dev/null && grep -qx 'kanban.worker_fallback=wait' '$HERMES_HOME/profiles/demofix/config.set' 2>/dev/null"
check "profiles leaves the reviewer on allow" "! grep -q worker_fallback '$HERMES_HOME/profiles/demoreview/config.set' 2>/dev/null"
echo "KEEP" > "$HERMES_HOME/profiles/demoimpl/memories/MEMORY.md"
"$S/kanban-profiles.sh" demo >/dev/null
check "profiles keeps an existing memory" "grep -qx KEEP '$HERMES_HOME/profiles/demoimpl/memories/MEMORY.md'"

# the package stands alone
PKG="$(cd "$HERE/.." && pwd)"
check "package names no machine-specific path" "! grep -rnE '/Users/|~/dev/|/home/[a-z]' '$PKG' --include='*.md' --include='*.sh' --include='*.py' --include='*.env' --include='*.example' | grep -v '/tests/run.sh:' | grep -v '/PLAN.md:'"  # PLAN.md: the work plan, install.sh does not ship it
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

# kanban-sync: the package's own templates satisfy every rule it checks projects against
SYNC="$S/kanban-sync.py"
check "every rule marker is in the package's own templates (present) or absent from them (absent)" "python3 - '$PKG' <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
bad = []
for line in (root / 'templates/rules.tsv').read_text().splitlines():
    if not line.strip() or line.startswith('#'): continue
    rid, roles, want, marker = line.split('\t', 3)
    for role in roles.split(','):
        has = marker in (root / 'templates/roles' / f'{role}.md').read_text()
        if has != (want == 'present'): bad.append(f'{rid}:{role}')
sys.exit(' '.join(bad) if bad else 0)
PY"
SR="$SANDBOX/sync-root"; mkdir -p "$SR"
FRESH="$SR/fresh"; mkdir -p "$FRESH"; git -C "$FRESH" init -q
(cd "$FRESH" && bash "$S/kanban-init.sh" fresh-board >/dev/null)
git -C "$FRESH" add -A && git -C "$FRESH" -c user.email=t@t -c user.name=t commit -qm init
OUT="$(python3 "$SYNC" --root "$SR")"
check "sync: a project scaffolded from this package is up to date" "grep -q 'role templates up to date' <<<'$OUT' && ! grep -q missing <<<'$OUT'"
# an older project: templates without the ask rule
OLD="$SR/old"; cp -R "$FRESH" "$OLD"
python3 - "$OLD/docs/agents/kanban-templates" <<'PY'
import pathlib, re, sys
d = pathlib.Path(sys.argv[1])
for role in ("common", "fix", "research"):
    p = d / f"{role}.md"
    s = p.read_text()
    s = re.sub(r"^- [^\n]*\n(?:  [^\n]*\n)*", lambda m: "" if "await_reply_minutes=10" in m.group(0) else m.group(0), s, flags=re.M)
    p.write_text(s)
PY
git -C "$OLD" -c user.email=t@t -c user.name=t commit -qam "older method"
OUT="$(python3 "$SYNC" --root "$SR")"
check "sync finds every project under the root" "grep -q '$FRESH ' <<<'$OUT' && grep -q '$OLD ' <<<'$OUT'"
check "sync names the missing rule per role" "[ \$(grep -c 'missing   ask-and-wait' <<<'$OUT') = 3 ]"
echo "local edit" >> "$OLD/docs/agents/kanban-templates/research.md"
APPLY="$(python3 "$SYNC" --apply "$OLD")"
check "sync --apply adds the rule to clean templates" "grep -q 'added    docs/agents/kanban-templates/common.md: ask-and-wait' <<<'$APPLY' && grep -q 'await_reply_minutes=10' '$OLD/docs/agents/kanban-templates/fix.md'"
check "sync --apply leaves a template with uncommitted changes alone" "grep -q 'skipped  docs/agents/kanban-templates/research.md' <<<'$APPLY' && ! grep -q 'await_reply_minutes=10' '$OLD/docs/agents/kanban-templates/research.md'"
check "sync --apply puts the bullet where the package has it" "python3 - '$OLD/docs/agents/kanban-templates/common.md' '$PKG/templates/roles/common.md' <<'PY'
import sys
got, want = (open(p).read() for p in sys.argv[1:])
sys.exit(0 if got == want else 1)
PY"
check "sync --apply never commits" "[ -n \"\$(git -C '$OLD' status --porcelain)\" ] && [ \$(git -C '$OLD' rev-list --count HEAD) = 2 ]"
printf -- '- Ask: await_reply_minutes=5 when unsure.\n' >> "$OLD/docs/agents/kanban-templates/review.md"
APPLY="$(python3 "$SYNC" --apply "$OLD" --allow-dirty)"
check "sync reports a forbidden rule for a manual edit, never rewrites it" "grep -q 'manual   docs/agents/kanban-templates/review.md: remove the text' <<<'$APPLY' && grep -q 'await_reply_minutes=5' '$OLD/docs/agents/kanban-templates/review.md'"
check "sync --allow-dirty fills the dirty template too" "grep -q 'await_reply_minutes=10' '$OLD/docs/agents/kanban-templates/research.md'"

echo
[ "$FAILS" = 0 ] && echo "all kanban tests passed" || { echo "$FAILS failed"; exit 1; }
