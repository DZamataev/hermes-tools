#!/usr/bin/env python3
"""A stand-in for `hermes` that records calls and keeps a tiny board in JSON.

Only the subcommands the kanban scripts use are implemented. Knobs:
FAKE_NO_ID=1          create --json returns no id
FAKE_WRONG_PARENT=1   show --json reports no parents
"""
import json
import os
import shlex
import sys

state_path = os.environ["FAKE_STATE"]
home = os.environ["HERMES_HOME"]
argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write("hermes " + " ".join(a.replace("\n", "\\n") for a in argv) + "\n")
state = json.load(open(state_path))


def save():
    json.dump(state, open(state_path, "w"))


def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def opts(name):
    return [argv[i + 1] for i, a in enumerate(argv) if a == name]


if argv[:1] == ["kanban"]:
    rest = argv[1:]
    if rest[:1] == ["--board"]:
        rest = rest[2:]
    cmd = rest[0]
    if cmd == "create":
        body = sys.stdin.read() if opt("--body-file") == "-" else ""
        if os.environ.get("FAKE_NO_ID"):
            print("{}")
            sys.exit(0)
        tid = f"t_{state['next']}"
        state["next"] += 1
        status = "blocked" if opt("--initial-status") == "blocked" else "todo"
        state["tasks"].append({"id": tid, "title": rest[1], "body": body, "status": status,
                               "parents": opts("--parent"), "assignee": opt("--assignee")})
        save()
        print(json.dumps({"id": tid}))
    elif cmd == "show":
        t = next(t for t in state["tasks"] if t["id"] == rest[1])
        parents = [] if os.environ.get("FAKE_WRONG_PARENT") else t["parents"]
        if os.environ.get("FAKE_WRONG_PARENT") and not t["parents"]:
            parents = ["t_bogus"]
        print(json.dumps({"task": t, "parents": parents}))
    elif cmd == "list":
        print(json.dumps(state["tasks"]))
    elif cmd == "unblock":
        for t in state["tasks"]:
            if t["id"] in rest[1:]:
                t["status"] = "todo"
        save()
    elif cmd in ("dispatch", "notify-subscribe"):
        pass
    else:
        sys.exit(f"fake hermes: unsupported kanban {cmd}")
elif argv[:2] == ["cron", "create"]:
    jobs_path = os.path.join(home, "cron", "jobs.json")
    jobs = json.load(open(jobs_path)) if os.path.exists(jobs_path) else {"jobs": []}
    jobs["jobs"].append({"id": "job1", "name": opt("--name")})
    json.dump(jobs, open(jobs_path, "w"))
elif argv[:2] == ["cron", "remove"]:
    jobs_path = os.path.join(home, "cron", "jobs.json")
    jobs = json.load(open(jobs_path))
    jobs["jobs"] = [j for j in jobs["jobs"] if j["id"] != argv[2]]
    json.dump(jobs, open(jobs_path, "w"))
elif argv[:2] == ["profile", "create"]:
    d = os.path.join(home, "profiles", argv[2], "memories")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "MEMORY.md"), "w").write("INHERITED notes from the source profile\n")
elif argv[:2] == ["config", "set"]:
    pass
elif argv[:2] == ["config", "get"]:
    print("fake-value")
else:
    sys.exit("fake hermes: unsupported " + shlex.join(argv))
