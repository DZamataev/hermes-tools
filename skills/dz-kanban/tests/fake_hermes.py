#!/usr/bin/env python3
"""Fake `hermes` for the dz-kanban bench. Reads/writes the sandbox board DBs under
$HERMES_HOME/kanban/boards/<slug>/kanban.db; logs every call to $FAKE_LOG.
Supports what kb.py and dz-wrapup's session_close.py call."""
import json
import os
import sqlite3
import sys
import time

home = os.environ["HERMES_HOME"]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]


def db(board):
    return sqlite3.connect(os.path.join(home, "kanban", "boards", board, "kanban.db"))


def opts(xs):
    return dict(zip(xs[::2], xs[1::2]))


if args[:1] == ["sessions"]:
    print("ok")
    sys.exit(0)
if args[:1] != ["kanban"]:
    sys.exit("fake hermes: unsupported")
board = None
args = args[1:]
if args[:1] == ["--board"]:
    board, args = args[1], args[2:]
if args[:2] == ["create", "--help"]:
    print("--completion-contract {none,local-commit,local-commit-or-none}" if os.environ.get("FAKE_FORK", "1") == "1" else "--title")
    sys.exit(0)
if args[:2] == ["boards", "list"]:
    root = os.path.join(home, "kanban", "boards")
    print(json.dumps([{"slug": b, "archived": False} for b in sorted(os.listdir(root))]))
    sys.exit(0)
c = db(board)
if args[0] == "notify-list":
    rows = c.execute("SELECT task_id, platform, chat_id, thread_id FROM kanban_notify_subs").fetchall()
    print(json.dumps([dict(zip(("task_id", "platform", "chat_id", "thread_id"), r)) for r in rows]))
elif args[0] == "list":
    rows = c.execute("SELECT id, status, title FROM tasks").fetchall()
    print(json.dumps([dict(zip(("id", "status", "title"), r)) for r in rows]))
elif args[0] == "notify-subscribe":
    o = opts(args[2:])
    if os.environ.get("FAKE_FAIL_SUB") == args[1]:
        sys.exit("refused")
    c.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, thread_id, created_at) VALUES (?,?,?,?,?)",
              (args[1], o["--platform"], o["--chat-id"], o.get("--thread-id"), int(time.time())))
    c.commit()
elif args[0] == "notify-unsubscribe":
    o = opts(args[2:])
    cur = c.execute("DELETE FROM kanban_notify_subs WHERE task_id=? AND platform=? AND chat_id=?",
                    (args[1], o["--platform"], o["--chat-id"]))
    c.commit()
    if not cur.rowcount:
        sys.exit("(no such subscription)")
else:
    sys.exit("fake hermes: unsupported " + " ".join(args))
