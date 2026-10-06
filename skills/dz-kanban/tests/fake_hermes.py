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
if args[:1] == ["profile"]:
    pd = os.path.join(home, "profiles")
    if args[1] == "export":
        out = args[args.index("-o") + 1]
        open(out, "w").write("archive of " + args[2])
    elif args[1] == "delete":
        import shutil
        shutil.rmtree(os.path.join(pd, args[2]))
    elif args[1] == "describe":
        open(os.path.join(pd, args[2], "description"), "w").write(args[args.index("--text") + 1])
    print("ok")
    sys.exit(0)
if args[:1] == ["config"]:
    cfgp = os.path.join(home, "config.yaml")   # HERMES_HOME is the profile dir here
    cfg = json.load(open(cfgp)) if os.path.exists(cfgp) and open(cfgp).read().strip() else {}
    if args[1] == "set":
        rest = [x for x in args[2:] if x != "--force"]
        key, val = rest
        if "--force" not in args and key == "agent.reasoning_effort":
            sys.exit("unknown key (needs --force)")
        try:
            val = json.loads(val)
        except ValueError:
            pass
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
        json.dump(cfg, open(cfgp, "w"))   # JSON is valid YAML
    elif args[1] == "get":
        node = cfg
        for p in args[2].split("."):
            node = node.get(p) if isinstance(node, dict) else None
        print(json.dumps(node))
    sys.exit(0)
if args[:1] != ["kanban"]:
    sys.exit("fake hermes: unsupported")
board = None
args = args[1:]
if args[:1] == ["--board"]:
    board, args = args[1], args[2:]
if args[:2] == ["boards", "create"]:
    os.makedirs(os.path.join(home, "kanban", "boards", args[2]))
    sqlite3.connect(os.path.join(home, "kanban", "boards", args[2], "kanban.db")).executescript(
        "CREATE TABLE tasks (id TEXT, title TEXT, assignee TEXT, status TEXT);")
    sys.exit(0)
if args[:2] in (["boards", "rename"], ["boards", "set-default-workdir"]):
    sys.exit(0)
if args[:2] == ["boards", "export"]:
    open(args[args.index("-o") + 1], "w").write("board " + args[2])
    sys.exit(0)
if args[:2] == ["boards", "rm"]:
    import shutil
    src = os.path.join(home, "kanban", "boards", args[2])
    if "--delete" in args:
        shutil.rmtree(src)
    else:
        os.makedirs(os.path.join(home, "kanban", "boards", "_archived"), exist_ok=True)
        shutil.move(src, os.path.join(home, "kanban", "boards", "_archived", args[2]))
    sys.exit(0)
if args[:2] == ["create", "--help"]:
    print("--completion-contract {none,local-commit,local-commit-or-none}" if os.environ.get("FAKE_FORK", "1") == "1" else "--title")
    sys.exit(0)
if args[:2] == ["boards", "list"]:
    root = os.path.join(home, "kanban", "boards")
    print(json.dumps([{"slug": b, "archived": False, "db_path": os.path.join(root, b, "kanban.db")}
                      for b in sorted(os.listdir(root)) if b != "_archived"]))
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
