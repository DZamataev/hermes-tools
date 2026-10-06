#!/usr/bin/env python3
"""Fake `hermes` for the dz-wrapup bench: answers the few kanban/sessions commands
session_close.py uses from $FAKE_STATE (JSON) and logs every call to $FAKE_LOG.
`sessions unpin` writes sessions.pinned in $HERMES_HOME/state.db like the real one."""
import json
import os
import sqlite3
import sys

state_path, log = os.environ["FAKE_STATE"], os.environ["FAKE_LOG"]
args = sys.argv[1:]
with open(log, "a") as f:
    f.write(" ".join(args) + "\n")
st = json.load(open(state_path))


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


if args[:1] == ["sessions"] and args[1] == "unpin":
    db = sqlite3.connect(os.path.join(os.environ["HERMES_HOME"], "state.db"))
    for sid in args[2:]:
        db.execute("UPDATE sessions SET pinned = 0 WHERE id = ?", (sid,))
    db.commit()
    print("Unpinned")
    sys.exit(0)
if args[:1] != ["kanban"]:
    sys.exit("fake hermes: unsupported " + " ".join(args))
board = None
if args[1] == "--board":
    board, args = args[2], args[3:]
else:
    args = args[1:]
if args[:2] == ["boards", "list"]:
    out([{"slug": b, "archived": False} for b in st["boards"]])
if board in st.get("broken", []):
    sys.exit("board is broken")
b = st["boards"][board]
if args[0] == "notify-list":
    out(b["subs"])
if args[0] == "list":
    out(b["tasks"])
if args[0] == "notify-unsubscribe":
    task, opts = args[1], dict(zip(args[2::2], args[3::2]))
    keep = [s for s in b["subs"] if not (s["task_id"] == task and s["platform"] == opts["--platform"]
                                         and s["chat_id"] == opts["--chat-id"])]
    if len(keep) == len(b["subs"]):
        sys.exit("(no such subscription)")
    b["subs"] = keep
    json.dump(st, open(state_path, "w"))
    print("Unsubscribed")
    sys.exit(0)
sys.exit("fake hermes: unsupported " + " ".join(args))
