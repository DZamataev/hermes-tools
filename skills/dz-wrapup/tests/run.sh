#!/usr/bin/env bash
# Bench for dz-wrapup's session_close.py: a fake hermes (DZ_HERMES) and a
# throwaway state.db (HERMES_HOME). No real board or session is touched.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../scripts"
SB="$(mktemp -d)"; trap 'rm -rf "$SB"' EXIT
FAILS=0
ok()  { printf 'ok   %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export HERMES_HOME="$SB/home" FAKE_STATE="$SB/state.json" FAKE_LOG="$SB/calls.log" DZ_HERMES="$SB/hermes"
unset HERMES_SESSION_ID
mkdir -p "$HERMES_HOME"; cp "$HERE/fake_hermes.py" "$DZ_HERMES"; chmod +x "$DZ_HERMES"

# Sessions: root --compression--> mid --compression--> tip (the live one).
# mid also spawned a subagent and has a branch; other is unrelated.
python3 - "$HERMES_HOME/state.db" <<'PY'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("""CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT,
  started_at REAL, ended_at REAL, end_reason TEXT, pinned INTEGER DEFAULT 0, title TEXT, model_config TEXT)""")
rows = [
 ("root", "desktop", None, 1, 10, "compression", 1, "work", None),
 ("sub1", "tool", "mid", 12, 13, "agent_close", 0, "Subagent: x", '{"_delegate_from": "mid"}'),
 ("mid", "desktop", "root", 10.5, 20, "compression", 0, "work", None),
 ("sub0", "tool", "root", 10, 11, "agent_close", 0, "Subagent: y", '{"_delegate_from": "root"}'),
 ("prev", "desktop", None, 1, 30, "session_reset", 1, "before /new", None),
 ("fresh", "desktop", "prev", 30, None, None, 0, "after /new", None),
 ("branch", "desktop", "mid", 15, None, None, 1, "work (branch)", '{"_branched_from": "mid"}'),
 ("tip", "desktop", "mid", 20, None, None, 1, "work", None),
 ("other", "desktop", None, 5, None, None, 1, "other", None),
]
db.executemany("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", rows)
db.commit()
PY

sub() { printf '{"task_id":"%s","platform":"%s","chat_id":"%s","thread_id":null}' "$1" "$2" "$3"; }
cat > "$FAKE_STATE" <<EOF
{"boards": {
  "alpha": {"subs": [$(sub t1 tui root), $(sub t1 telegram -100), $(sub t2 tui tip), $(sub t3 tui other), $(sub t4 tui branch)],
            "tasks": [{"id":"t1","status":"done","title":"one"},{"id":"t2","status":"blocked","title":"two"},
                      {"id":"t3","status":"ready","title":"three"},{"id":"t4","status":"done","title":"four"}]},
  "beta":  {"subs": [$(sub t9 tui mid)], "tasks": [{"id":"t9","status":"archived","title":"nine"}]},
  "empty": {"subs": [], "tasks": []}
}}
EOF

pinned() { python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("select pinned from sessions where id=?",(sys.argv[2],)).fetchone()[0])' "$HERMES_HOME/state.db" "$1"; }
subs_of() { python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(sum(1 for b in s["boards"].values() for x in b["subs"] if x["platform"]=="tui" and x["chat_id"]==sys.argv[2]))' "$FAKE_STATE" "$1"; }

# ---- dry run -----------------------------------------------------------------
python3 "$S/session_close.py" --session tip --json > "$SB/dry.json"; rc=$?
check "dry run exits 0" "[ $rc -eq 0 ]"
lin() { python3 -c 'import json,sys; print(" ".join(s["id"] for s in json.load(open(sys.argv[1]))["lineage"]))' "$1"; }
check "lineage is root mid tip, no subagent/branch/other (got: $(lin "$SB/dry.json"))" "[ \"\$(lin '$SB/dry.json')\" = 'root mid tip' ]"
check "from the root the lineage is the same" "python3 '$S/session_close.py' --session root --json > '$SB/r.json' && [ \"\$(lin '$SB/r.json')\" = 'root mid tip' ]"
check "a subagent is its own lineage" "python3 '$S/session_close.py' --session sub1 --json > '$SB/s.json' && [ \"\$(lin '$SB/s.json')\" = 'sub1' ]"
check "a subagent started after the rotation is not the continuation" "python3 '$S/session_close.py' --session sub0 --json > '$SB/s0.json' && [ \"\$(lin '$SB/s0.json')\" = 'sub0' ]"
check "a reset child is a new session" "python3 '$S/session_close.py' --session fresh --json > '$SB/f.json' && [ \"\$(lin '$SB/f.json')\" = 'fresh' ]"
check "a branch is its own lineage" "python3 '$S/session_close.py' --session branch --json > '$SB/b.json' && [ \"\$(lin '$SB/b.json')\" = 'branch' ]"
check "dry run finds 3 subscriptions across boards" "python3 -c 'import json,sys; assert len(json.load(open(sys.argv[1]))[\"subscriptions\"])==3' '$SB/dry.json'"
check "open card t2 warned, done/archived not" "python3 -c 'import json,sys; assert [c[\"task_id\"] for c in json.load(open(sys.argv[1]))[\"open_cards\"]]==[\"t2\"]' '$SB/dry.json'"
check "dry run changed nothing" "[ \$(subs_of tip) = 1 ] && [ \$(pinned tip) = 1 ] && ! grep -q 'unsubscribe\|unpin' '$FAKE_LOG'"

# ---- apply -------------------------------------------------------------------
HERMES_SESSION_ID=tip python3 "$S/session_close.py" --yes > "$SB/apply.txt"; rc=$?
check "apply exits 0 even with an open card" "[ $rc -eq 0 ]"
check "open card is listed in the output" "grep -q 'alpha t2 \[blocked\] two' '$SB/apply.txt'"
check "lineage subscriptions removed" "[ \$(subs_of root) = 0 ] && [ \$(subs_of mid) = 0 ] && [ \$(subs_of tip) = 0 ]"
check "other sessions' and chat subscriptions kept" "[ \$(subs_of other) = 1 ] && [ \$(subs_of branch) = 1 ] && grep -q telegram '$FAKE_STATE'"
check "pinned lineage ids unpinned" "[ \$(pinned root) = 0 ] && [ \$(pinned tip) = 0 ]"
check "other and branch stay pinned" "[ \$(pinned other) = 1 ] && [ \$(pinned branch) = 1 ]"
check "summary line" "grep -q 'unsubscribed 3; unpinned root, tip' '$SB/apply.txt'"

# ---- idempotent, flags, failures ----------------------------------------------
python3 "$S/session_close.py" --session tip --yes > "$SB/again.txt"; rc=$?
check "second run is a clean no-op" "[ $rc -eq 0 ] && grep -q 'kanban subscriptions: none' '$SB/again.txt'"
python3 "$S/session_close.py" --session other --yes --keep-pin > /dev/null
check "--keep-pin unsubscribes but keeps the pin" "[ \$(subs_of other) = 0 ] && [ \$(pinned other) = 1 ]"
python3 "$S/session_close.py" --session branch --yes --keep-subs > /dev/null
check "--keep-subs unpins but keeps subscriptions" "[ \$(subs_of branch) = 1 ] && [ \$(pinned branch) = 0 ]"
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); s["broken"]=["beta"]; json.dump(s,open(sys.argv[1],"w"))' "$FAKE_STATE"
python3 "$S/session_close.py" --session branch > "$SB/broken.txt"; rc=$?
check "an unreadable board is reported and fails the run" "[ $rc -ne 0 ] && grep -q '! board beta' '$SB/broken.txt'"
env -u HERMES_SESSION_ID python3 "$S/session_close.py" > /dev/null 2>&1; rc=$?
check "no session id is a usage error" "[ $rc -eq 2 ]"

echo
[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
