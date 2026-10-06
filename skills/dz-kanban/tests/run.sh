#!/usr/bin/env bash
# Bench for dz-kanban's kb.py: a sandbox HERMES_HOME with a board DB built here,
# a sessions state.db, a fake hermes (DZ_HERMES), a fake hermes-tools + installed
# method skill, and a project repo with .kanban/config.env. Touches nothing real.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KB="$HERE/../scripts/kb.py"
SB="$(mktemp -d)"; SB="$(cd "$SB" && pwd -P)"; trap 'rm -rf "$SB"' EXIT
FAILS=0
ok()  { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

REAL_HERMES_PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python"
export HERMES_HOME="$SB/home" DZ_HERMES="$SB/hermes" FAKE_LOG="$SB/calls.log"
export GIT_CONFIG_GLOBAL="$SB/gitconfig" GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset HERMES_SESSION_ID DZ_HERMES_TOOLS DZ_HERMES_PYTHON
git config --global init.defaultBranch main; git config --global commit.gpgsign false
cp "$HERE/fake_hermes.py" "$DZ_HERMES"; chmod +x "$DZ_HERMES"
mkdir -p "$HERMES_HOME/kanban/boards/demo" "$HERMES_HOME/kanban/boards/other"
# dz-wrapup next to dz-kanban, as installed (unsubscribe delegates to it)
mkdir -p "$HERMES_HOME/skills/software-development"
cp -R "$HERE/../../dz-wrapup" "$HERMES_HOME/skills/software-development/dz-wrapup"

python3 - "$HERMES_HOME" <<'PY'
import sqlite3, sys, time, json, os
home = sys.argv[1]; now = int(time.time()); D = 86400
schema = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, assignee TEXT, status TEXT, created_at INT, started_at INT,
  completed_at INT, last_heartbeat_at INT, block_kind TEXT);
CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INT, kind TEXT, payload TEXT, created_at INT);
CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT, status TEXT, outcome TEXT, started_at INT,
  ended_at INT, error TEXT, metadata TEXT, summary TEXT);
CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT, thread_id TEXT, created_at INT);
CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INT);
"""
for b in ("demo", "other"):
    c = sqlite3.connect(os.path.join(home, "kanban", "boards", b, "kanban.db")); c.executescript(schema); c.commit()
c = sqlite3.connect(os.path.join(home, "kanban", "boards", "demo", "kanban.db"))
T = [  # id, title, assignee, status, created, started, completed, heartbeat
 ("t_a1", "Login (1): implement", "pimpl", "done", now-9*D, now-9*D, now-9*D+600, None),     # outside 7d
 ("t_b1", "Search (2): implement", "pimpl", "done", now-3*D, now-3*D, now-3*D+3000, None),
 ("t_b2", "Search (2): review", "preview", "done", now-3*D, now-3*D+3000, now-3*D+4000, None),
 ("t_b3", "Search (2): fix", "pfix", "done", now-3*D, now-3*D+4000, now-3*D+4030, None),
 ("t_c1", "Beta gate (3): implement", "pimpl", "blocked", now-D, None, None, None),          # chain head never released
 ("t_c2", "Beta gate (3): review", "preview", "blocked", now-D, None, None, None),            # waits in chain
 ("t_d1", "Release (4): implement", "pimpl", "blocked", now-D, now-D, None, None),           # real block
 ("t_e1", "Stuck (5): implement", "pimpl", "running", now-D, now-D, None, now-2*3600),       # no heartbeat 2h
 ("t_f1", "Lonely (6): implement", "pimpl", "todo", now-D, None, None, None),                # nobody subscribed
]
c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,NULL)", T)
c.executemany("INSERT INTO task_links VALUES (?,?)", [("t_b1","t_b2"),("t_b2","t_b3"),("t_c1","t_c2")])
E = [
 ("t_c1", "blocked", {"reason": "initial_status"}, now-D),
 ("t_c2", "blocked", {"reason": "initial_status"}, now-D),
 ("t_d1", "blocked", {"reason": "Run `pnpm install` in /wt/release: lockfile drift"}, now-3600),
 ("t_b1", "question", {"author": "pimpl", "body": "Question (2): should search include archived chats?"}, now-3*D+100),
 ("t_b1", "crashed", {"worker_output": "liveness watchdog 914s"}, now-3*D+50),
 ("t_b1", "gave_up", {"error": "stale_lock=host:1"}, now-3*D+60),
 ("t_b1", "model_fallback", {"from_model": "big", "to_provider": "cheap", "to_model": "small"}, now-3*D+70),
 ("t_b1", "rate_limited", {}, now-3*D+80),
 ("t_a1", "crashed", {"worker_output": "old"}, now-9*D),                                       # outside period
]
c.executemany("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
              [(t, k, json.dumps(p), ts) for t, k, p, ts in E])
R = [
 ("t_b1", "pimpl", "done", "crashed", now-3*D, now-3*D+50, "watchdog", None),
 ("t_b1", "pimpl", "done", "reclaimed", now-3*D+60, now-3*D+70, "stale", None),
 ("t_b1", "pimpl", "done", "completed", now-3*D+100, now-3*D+3000, None, json.dumps({"start_head": "a"})),
 ("t_b2", "preview", "done", "completed", now-3*D+3000, now-3*D+4000, None, json.dumps({"findings": [{"id": "F1"}, {"id": "F2"}]})),
 ("t_b3", "pfix", "done", "completed", now-3*D+4000, now-3*D+4030, None, json.dumps({"no_change": "rejected both with proof"})),
]
c.executemany("INSERT INTO task_runs (task_id, profile, status, outcome, started_at, ended_at, error, metadata) VALUES (?,?,?,?,?,?,?,?)", R)
c.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
              [("t_b1", "default", "answer", now-3*D+200), ("t_b1", "pimpl", "progress", now-3*D+300)])
S = [  # live orchestrator watches the open chain; a dead session still holds t_d1; telegram on everything but t_f1
 ("t_c1","tui","live"), ("t_c2","tui","live"), ("t_e1","tui","live"), ("t_b1","tui","live"),
 ("t_d1","tui","dead"),
] + [(t[0], "telegram", "-100") for t in T if t[0] != "t_f1"]
c.executemany("INSERT INTO kanban_notify_subs (task_id, platform, chat_id) VALUES (?,?,?)", S)
c.commit()
s = sqlite3.connect(os.path.join(home, "state.db"))
s.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT, started_at REAL, ended_at REAL, end_reason TEXT, pinned INT, title TEXT, model_config TEXT, last_activity_at REAL)")
s.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)", [
 ("live", "desktop", None, 1, None, None, 1, "Orchestrator", None, now),
 ("dead", "desktop", None, 1, 5, "startup_orphan_reap", 1, "Old orchestrator", None, 5),
 ("new", "desktop", None, 1, None, None, 0, "New session", None, now)])
s.commit()
PY

# ---- project repo -----------------------------------------------------------------
P="$SB/dev/proj"; mkdir -p "$P/.kanban"; git -C "$P" init -q
printf 'KANBAN_BOARD=demo\nKANBAN_PROFILE_PREFIX=p\nKANBAN_NOTIFY_ENV=.kanban/notify.env\n' > "$P/.kanban/config.env"
git -C "$P" add -A; git -C "$P" commit -qm init
cd "$P"

# ---- status ------------------------------------------------------------------------
python3 "$KB" status --json > "$SB/status.json"; rc=$?
check "status exits 0 and reads the board from .kanban/config.env" "[ $rc -eq 0 ] && grep -q '\"board\": \"demo\"' '$SB/status.json'"
j() { python3 -c "import json; d=json.load(open('$SB/status.json')); print($1)"; }
check "done in the last 7 days = 3 (9-day-old card excluded)" "[ \"\$(j 'd[\"done_in_period\"]')\" = 3 ]"
check "a chain counts as finished when its fix card is done" "[ \"\$(j 'd[\"chains_finished\"]')\" = \"['Search (2)']\" ]"
check "plan groups open cards by chain" "j 'list(d[\"plan\"])' | grep -q \"'Beta gate (3)', 'Release (4)', 'Stuck (5)', 'Lonely (6)'\""
check "an unreleased chain head is reported" "j 'd[\"problems\"]' | grep -q 't_c1 Beta gate (3): implement — waits for release'"
check "a card waiting in its chain is not a problem" "! j 'd[\"problems\"]' | grep -q 't_c2'"
check "a real block is reported with its reason" "j 'd[\"problems\"]' | grep -q 't_d1.*blocked: Run .pnpm install.'"
check "a running card without heartbeat is reported" "j 'd[\"problems\"]' | grep -q 't_e1.*no heartbeat'"
check "crash/gave-up/fallback/quota counted, old crash excluded" "j 'd[\"problems\"]' | grep -q '1× worker crashed' && j 'd[\"problems\"]' | grep -q '1× gave up' && j 'd[\"problems\"]' | grep -q '1 run(s) fell back' && j 'd[\"problems\"]' | grep -q '1 quota wall'"
check "a card nobody hears is reported" "j 'd[\"problems\"]' | grep -q 'notify nobody: t_f1'"
check "an ended session still subscribed is reported" "j 'd[\"problems\"]' | grep -q 'session dead (Old orchestrator) ended'"
check "sessions listed with titles and open counts" "j '[(s[\"session\"], s[\"open_cards\"]) for s in d[\"sessions\"]]' | grep -q \"('live', 3)\""
check "chat target shown without the chat id" "j 'd[\"chats\"]' | grep -q 'telegram:<chat>' && ! grep -q -- '-100' '$SB/status.json'"
touch "$HERMES_HOME/ESTOP"
check "hermes pause is reported" "python3 '$KB' status | grep -q 'hermes pause is engaged'"
rm "$HERMES_HOME/ESTOP"
check "text status has the sections" "python3 '$KB' status > '$SB/status.txt' && grep -q '== plan' '$SB/status.txt' && grep -q '== notifications' '$SB/status.txt' && grep -q '== problems' '$SB/status.txt'"
check "no board → usage error outside a project" "(cd '$SB' && ! python3 '$KB' status >/dev/null 2>&1)"
check "--board overrides the project" "(cd '$SB' && python3 '$KB' status --board other --json | grep -q '\"board\": \"other\"')"

# ---- signals ---------------------------------------------------------------------------
python3 "$KB" signals --json > "$SB/sig.json"
g() { python3 -c "import json; d=json.load(open('$SB/sig.json')); print($1)"; }
check "signals: review findings counted from run metadata" "[ \"\$(g 'd[\"reviews\"]')\" = \"{'count': 1, 'with_findings': 1, 'findings_total': 2}\" ]"
check "signals: no-change fix counted" "[ \"\$(g 'd[\"fix_no_change\"]')\" = 1 ]"
check "signals: worker question text" "g 'd[\"questions\"]' | grep -q 'include archived chats'"
check "signals: real block only, not initial_status" "[ \"\$(g 'len(d[\"blocks\"])')\" = 1 ]"
check "signals: comments by author" "[ \"\$(g 'd[\"comments_by_author\"]')\" = \"{'default': 1, 'pimpl': 1}\" ]"
check "signals: card with >2 runs" "g 'd[\"cards_with_retries\"]' | grep -q \"'task': 't_b1', 'runs': 3\""

# ---- subscribe / unsubscribe -------------------------------------------------------------
HERMES_SESSION_ID=new python3 "$KB" subscribe > "$SB/sub.txt"; rc=$?
subs() { python3 -c "import sqlite3; print(sqlite3.connect('$HERMES_HOME/kanban/boards/demo/kanban.db').execute(\"select group_concat(task_id) from (select task_id from kanban_notify_subs where platform='tui' and chat_id='$1' order by task_id)\").fetchone()[0])"; }
check "subscribe: the session gets every open card, not done ones" "[ $rc -eq 0 ] && [ \"\$(subs new)\" = 't_c1,t_c2,t_d1,t_e1,t_f1' ]"
HERMES_SESSION_ID=new python3 "$KB" subscribe > "$SB/sub2.txt"
check "subscribe is idempotent" "grep -q 'subscribing 0' '$SB/sub2.txt' && [ \"\$(subs new)\" = 't_c1,t_c2,t_d1,t_e1,t_f1' ]"
python3 "$KB" subscribe --session live > /dev/null
check "subscribe skips cards the session already has" "[ \"\$(subs live)\" = 't_b1,t_c1,t_c2,t_d1,t_e1,t_f1' ]"
FAKE_FAIL_SUB=t_f1 python3 "$KB" subscribe --session x1 > "$SB/subfail.txt"; rc=$?
check "a failed subscription is reported and fails the run" "[ $rc -ne 0 ] && grep -q 'FAILED t_f1' '$SB/subfail.txt'"
python3 "$KB" unsubscribe --session dead > "$SB/unsub-dry.txt"
check "unsubscribe without --yes is a dry run" "[ \"\$(subs dead)\" = 't_d1' ] && grep -q 'dry run' '$SB/unsub-dry.txt'"
python3 "$KB" unsubscribe --session dead --yes > "$SB/unsub.txt"
check "unsubscribe --yes drops the session's subscriptions on this board" "[ \"\$(subs dead)\" = 'None' ]"
check "unsubscribe never unpins" "! grep -q 'sessions unpin' '$FAKE_LOG'"
check "unsubscribe leaves the chat target" "[ \"\$(python3 -c \"import sqlite3; print(sqlite3.connect('$HERMES_HOME/kanban/boards/demo/kanban.db').execute(\\\"select count(*) from kanban_notify_subs where platform='telegram'\\\").fetchone()[0])\")\" = 8 ]"

# ---- where / setup -------------------------------------------------------------------------
T="$SB/tools"; mkdir -p "$T/kanban/scripts"; git -C "$T" init -q
printf -- '---\nname: hermes-kanban-development\n---\nv1\n' > "$T/kanban/SKILL.md"
echo 'echo v1' > "$T/kanban/scripts/kanban-sync.py"
git -C "$T" add -A; git -C "$T" commit -qm v1
I="$HERMES_HOME/skills/software-development/hermes-kanban-development"; mkdir -p "$I/scripts"
cp "$T/kanban/SKILL.md" "$I/SKILL.md"
printf '#!/usr/bin/env python3\nprint("[]")\n' > "$I/scripts/kanban-sync.py"   # installed: differs, unknown → local edit
printf 'import sys\nprint("[]")\n' > "$T/kanban/scripts/kanban-sync.py"; git -C "$T" commit -qam v2
cp "$T/kanban/scripts/kanban-sync.py" "$I/scripts/kanban-sync.py"            # now current
echo 'v2 skill' >> "$T/kanban/SKILL.md"; git -C "$T" commit -qam "skill v2"   # install is an older version of SKILL.md
echo 'local' > "$I/scripts/extra.py"                                             # only in install
python3 "$KB" where --tools "$T" > "$SB/where.txt"
check "where: board, method, tools, fork" "grep -q 'board demo' '$SB/where.txt' && grep -q 'method skill installed: $I' '$SB/where.txt' && grep -q 'fork.s kanban: yes' '$SB/where.txt'"
python3 "$KB" setup --tools "$T" --json > "$SB/setup.json"
s() { python3 -c "import json; d=json.load(open('$SB/setup.json')); print($1)"; }
check "setup: older installed file → install --force" "s 'd[\"steps\"]' | grep -q 'install.sh --force.*1 file(s) out of date'"
check "setup: a file only in the install is flagged before --force" "s 'd[\"notes\"]' | grep -q 'only in install (not in hermes-tools): scripts/extra.py'"
check "setup: missing profiles → kanban-profiles.sh" "s 'd[\"steps\"]' | grep -q 'kanban-profiles.sh p.*missing: pimpl, preview, pfix'"
check "setup: missing notify env noted" "s 'd[\"notes\"]' | grep -q 'no notify env'"
check "setup: an existing board is not recreated" "! s 'd[\"steps\"]' | grep -q 'boards create demo'"
FAKE_FORK=0 python3 "$KB" setup --tools "$T" --json > "$SB/setup-nofork.json"
check "setup: upstream hermes → install the fork first" "grep -q 'DZamataev/hermes-agent' '$SB/setup-nofork.json'"
N="$SB/dev/newproj"; mkdir -p "$N"; git -C "$N" init -q
python3 "$KB" setup --repo "$N" --tools "$T" > "$SB/setup-new.txt"
check "setup: a new repo gets init, profiles, board, bring-up" "grep -q 'kanban-init.sh <board-slug> $N' '$SB/setup-new.txt' && grep -q 'boards create' '$SB/setup-new.txt'"
check "setup is read-only (no writes through hermes)" "! grep -q 'boards create\|notify-subscribe .* x2' '$FAKE_LOG'"

python3 "$KB" help > "$SB/help.txt"
check "help lists every command of both scripts" "for c in where setup subscribe unsubscribe status signals boards board-create board-rename board-archive board-delete profiles providers profile-create profile-set profile-delete; do grep -q \"\$c\" '$SB/help.txt' || exit 1; done"
check "help works as --help too" "python3 '$KB' --help | grep -q 'profile-set'"

# ---- configure: boards ----------------------------------------------------------------------
KC="$HERE/../scripts/kb_config.py"
: > "$FAKE_LOG"
python3 "$KC" board-create new-board --name "New board" > "$SB/bc.txt"
check "board-create without --yes is a dry run" "grep -q 'dry run' '$SB/bc.txt' && [ ! -d '$HERMES_HOME/kanban/boards/new-board' ] && [ ! -s '$FAKE_LOG' ]"
python3 "$KC" board-create new-board --name "New board" --yes > /dev/null
check "board-create --yes creates it" "[ -f '$HERMES_HOME/kanban/boards/new-board/kanban.db' ]"
check "board-create refuses a non-kebab slug" "! python3 '$KC' board-create 'Bad Slug' --yes >/dev/null 2>&1"
python3 "$KC" board-rename demo "Demo board" --yes > "$SB/br.txt"
check "board-rename calls boards rename and keeps the slug" "grep -q 'kanban boards rename demo Demo board' '$FAKE_LOG' && grep -q 'slug stays demo' '$SB/br.txt'"
check "board-archive refused while a card runs" "! python3 '$KC' board-archive demo --yes > '$SB/ba.txt' 2>&1 && grep -q 'running on demo' '$SB/ba.txt' && [ -d '$HERMES_HOME/kanban/boards/demo' ]"
check "the default board is never removed" "! python3 '$KC' board-delete default --yes >/dev/null 2>&1"
python3 "$KC" board-archive new-board --yes > /dev/null
check "board-archive moves it to _archived" "[ -d '$HERMES_HOME/kanban/boards/_archived/new-board' ] && [ ! -d '$HERMES_HOME/kanban/boards/new-board' ]"
python3 "$KC" board-delete other --yes > "$SB/bd.txt"
check "board-delete exports first, then deletes" "ls '$HERMES_HOME'/backups/kanban/other-*.tar.gz >/dev/null 2>&1 && [ ! -d '$HERMES_HOME/kanban/boards/other' ] && grep -q 'restore: hermes kanban boards import' '$SB/bd.txt'"
check "export ran before delete" "[ \"\$(grep -n 'boards export other' '$FAKE_LOG' | cut -d: -f1)\" -lt \"\$(grep -n 'boards rm other --delete' '$FAKE_LOG' | cut -d: -f1)\" ]"

# ---- configure: profiles ------------------------------------------------------------------------
for p in pimpl preview pfix spare; do mkdir -p "$HERMES_HOME/profiles/$p"; done
printf '{"model": {"default": "opus", "provider": "teamclaude"}, "kanban": {"worker_fallback": "wait"}}' > "$HERMES_HOME/profiles/pimpl/config.yaml"
printf '{"model": {"default": "claude-sonnet", "provider": "teamclaude"}}' > "$HERMES_HOME/profiles/preview/config.yaml"
printf '{"model": {"default": "opus", "provider": "teamclaude"}}' > "$HERMES_HOME/profiles/pfix/config.yaml"
printf '{"providers": {"teamclaude": {"model": "opus", "transport": "anthropic_messages", "models": {"opus": {}}}, "codex-lb": {"model": "gpt-x"}}}' > "$HERMES_HOME/config.yaml"
python3 "$KC" profiles --board demo > "$SB/pr.txt"
check "profiles --board lists the board's assignees only" "grep -q '^pimpl' '$SB/pr.txt' && grep -q '^preview' '$SB/pr.txt' && ! grep -q '^spare' '$SB/pr.txt'"
check "profiles shows model and provider" "grep -q 'pimpl  *teamclaude:opus' '$SB/pr.txt'"
check "profiles warns impl and review share a family" "grep -q 'share a model family (claude)' '$SB/pr.txt'"
check "profiles warns fix without worker_fallback wait" "grep -q 'pfix: worker_fallback is allow' '$SB/pr.txt' && ! grep -q 'pimpl: worker_fallback' '$SB/pr.txt'"
# the fast path reads config.yaml with Hermes' own Python (PyYAML); JSON is valid YAML
YPY=$(for c in "$REAL_HERMES_PY" /usr/bin/python3 python3; do "$c" -c 'import yaml' 2>/dev/null && { echo "$c"; break; }; done)
if [ -n "$YPY" ]; then
  DZ_HERMES_PYTHON="$YPY" python3 "$KC" profiles --board demo > "$SB/pr-yaml.txt"
  check "profiles via YAML reader matches the CLI path" "diff -q '$SB/pr.txt' '$SB/pr-yaml.txt' >/dev/null"
else
  echo "skip YAML reader check (no python with PyYAML)"
fi
python3 "$KC" providers > "$SB/prov.txt"
check "providers lists configured providers and models" "grep -q 'teamclaude .*default opus.*models: opus' '$SB/prov.txt' && grep -q '^codex-lb' '$SB/prov.txt'"

: > "$FAKE_LOG"
python3 "$KC" profile-set preview --model codex-lb:gpt-x --effort high --fallback teamclaude:opus --fallback codex-lb:gpt-y > "$SB/ps-dry.txt"
check "profile-set without --yes changes nothing" "grep -q 'dry run' '$SB/ps-dry.txt' && grep -q 'claude-sonnet' '$HERMES_HOME/profiles/preview/config.yaml'"
python3 "$KC" profile-set preview --model codex-lb:gpt-x --effort high --fallback teamclaude:opus --fallback codex-lb:gpt-y --yes > "$SB/ps.txt"
cfg() { python3 -c "import json; print(json.load(open('$HERMES_HOME/profiles/$1/config.yaml'))$2)"; }
check "profile-set writes provider and model" "[ \"\$(cfg preview \"['model']['provider']\")\" = codex-lb ] && [ \"\$(cfg preview \"['model']['default']\")\" = gpt-x ]"
check "profile-set writes the effort (with --force)" "[ \"\$(cfg preview \"['agent']['reasoning_effort']\")\" = high ]"
check "profile-set writes an ordered fallback list" "[ \"\$(cfg preview \"['fallback_providers']\")\" = \"[{'provider': 'teamclaude', 'model': 'opus'}, {'provider': 'codex-lb', 'model': 'gpt-y'}]\" ]"
check "profile-set reports the result" "grep -q 'now: codex-lb:gpt-x effort=high' '$SB/ps.txt'"
check "profile-set leaves other profiles alone" "[ \"\$(cfg pimpl \"['model']['default']\")\" = opus ]"
python3 "$KC" profile-set preview --no-fallback --worker-fallback wait --yes > /dev/null
check "--no-fallback empties the list; --worker-fallback sets kanban.worker_fallback" "[ \"\$(cfg preview \"['fallback_providers']\")\" = '[]' ] && [ \"\$(cfg preview \"['kanban']['worker_fallback']\")\" = wait ]"
python3 "$KC" profile-set preview --effort inherit --yes > /dev/null
check "--effort inherit clears it" "[ \"\$(cfg preview \"['agent']['reasoning_effort']\")\" = '' ]"
check "profile-set rejects a bad effort and a bad model spec" "! python3 '$KC' profile-set preview --effort turbo >/dev/null 2>&1 && ! python3 '$KC' profile-set preview --model gpt-x >/dev/null 2>&1"
python3 "$KC" profile-set pimpl --model nosuch:m > "$SB/ps-unk.txt"
check "an unknown provider is flagged" "grep -q 'nosuch is not in the main config' '$SB/ps-unk.txt'"
python3 "$KC" profile-set pimpl --model teamclaude:opus2 > "$SB/ps-run.txt"
check "a running card on the profile is named" "grep -q 'running card(s) keep their current model: demo/t_e1' '$SB/ps-run.txt'"
python3 "$KC" profile-set spare --description "Spare worker" --yes > /dev/null
check "--description goes through profile describe --text" "[ \"\$(cat '$HERMES_HOME/profiles/spare/description')\" = 'Spare worker' ]"

check "profile-delete refused while a card is open on it" "! python3 '$KC' profile-delete pimpl --yes > '$SB/pd.txt' 2>&1 && grep -q 'open card(s) are assigned to pimpl' '$SB/pd.txt' && [ -d '$HERMES_HOME/profiles/pimpl' ]"
check "the default profile is never deleted" "! python3 '$KC' profile-delete default --yes >/dev/null 2>&1"
python3 "$KC" profile-delete spare --yes > "$SB/pd2.txt"
check "profile-delete exports first, then deletes" "ls '$HERMES_HOME'/backups/profiles/spare-*.tar.gz >/dev/null 2>&1 && [ ! -d '$HERMES_HOME/profiles/spare' ]"

mkdir -p "$HERMES_HOME/skills/software-development/hermes-kanban-development/scripts"
printf '#!/bin/bash\necho "kanban-profiles $*" >> "%s"\n' "$FAKE_LOG" > "$HERMES_HOME/skills/software-development/hermes-kanban-development/scripts/kanban-profiles.sh"
python3 "$KC" profile-create q --repo "$P" --model-impl teamclaude:opus --model-review teamclaude:sonnet > "$SB/pc.txt"
check "profile-create dry run names kanban-profiles.sh and warns on one family" "grep -q 'kanban-profiles.sh q --repo' '$SB/pc.txt' && grep -q 'same model family' '$SB/pc.txt' && ! grep -q 'kanban-profiles q' '$FAKE_LOG'"
python3 "$KC" profile-create q --repo "$P" --model-review codex-lb:gpt-x --yes > /dev/null
check "profile-create --yes runs kanban-profiles.sh with the pins" "grep -q 'kanban-profiles q --repo $P --model-review codex-lb:gpt-x' '$FAKE_LOG'"

echo
[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
