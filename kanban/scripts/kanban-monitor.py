#!/usr/bin/env python3
"""Change-detector for a Hermes Kanban board, used as a cron `monitor` script.

  kanban-monitor.py <board-slug>
  kanban-monitor.py --drill            # prove every token on synthetic boards

Prints one line of stable tokens and nothing else. The cron scheduler hashes the
output and skips the model run when it is unchanged, so a healthy tick is free:
no timestamps, no durations, ids sorted.

  DONE:<n>          count of finished cards (each finish wakes the coordinator)
  BLOCKED:<ids>     blocked cards (stays visible until cleared)
  STALL             queued work, nothing running
  RETRYING:<id>     a running card is on a retry after a failure
  ALL-DONE          nothing running or queued
  BOARD-UNREADABLE  the board CLI failed

The board slug may also come from KANBAN_BOARD. Installed copies are
self-contained: kanban-coordinator.sh copies this file into ~/.hermes/scripts/
with the slug baked in, because cron runs monitors only from there.
"""
import json
import os
import subprocess
import sys

BOARD = None  # set by kanban-coordinator.sh in the installed copy
HERMES = os.environ.get("KANBAN_HERMES", "hermes")


def board(slug):
    out = subprocess.run([HERMES, "kanban", "--board", slug, "list", "--json"],
                         capture_output=True, text=True, timeout=90)
    data = json.loads(out.stdout)
    return data if isinstance(data, list) else data.get("tasks", [])


def signature(rows):
    rows = [t for t in rows if t.get("status") != "archived"]
    by = {}
    for t in rows:
        by.setdefault(t.get("status"), []).append(t)
    tokens = [f"DONE:{len(by.get('done', []))}"]
    blocked = sorted(t["id"] for t in by.get("blocked", []))
    if blocked:
        tokens.append("BLOCKED:" + ",".join(blocked))
    running = by.get("running", [])
    queued = by.get("todo", []) + by.get("ready", [])
    if queued and not running:
        tokens.append("STALL")
    for t in sorted(running, key=lambda t: t["id"]):
        # There is no attempt counter on a listed card; last_failure_error is the signal.
        if t.get("last_failure_error"):
            tokens.append(f"RETRYING:{t['id']}")
    if not running and not queued:
        tokens.append("ALL-DONE")
    return " ".join(tokens)


def run(slug):
    try:
        return signature(board(slug))
    except Exception:
        return "BOARD-UNREADABLE"


def drill():
    """Each scenario must yield its token; a healthy board must yield no alarm."""
    cases = [
        ("healthy", [{"id": "a", "status": "running"}, {"id": "b", "status": "todo"}],
         lambda s: s == "DONE:0"),
        ("done counts", [{"id": "a", "status": "done"}, {"id": "b", "status": "running"}],
         lambda s: s == "DONE:1"),
        ("stall", [{"id": "a", "status": "todo"}], lambda s: "STALL" in s),
        ("ready stall", [{"id": "a", "status": "ready"}], lambda s: "STALL" in s),
        ("blocked sorted", [{"id": "b", "status": "blocked"}, {"id": "a", "status": "blocked"}],
         lambda s: "BLOCKED:a,b" in s),
        ("retrying", [{"id": "a", "status": "running", "last_failure_error": "boom"}],
         lambda s: "RETRYING:a" in s and "STALL" not in s),
        ("all done", [{"id": "a", "status": "done"}, {"id": "x", "status": "archived"}],
         lambda s: s == "DONE:1 ALL-DONE"),
        ("archived ignored", [{"id": "x", "status": "archived"}, {"id": "a", "status": "running"}],
         lambda s: s == "DONE:0"),
    ]
    failed = 0
    for name, rows, ok in cases:
        got = signature(rows)
        mark = "ok " if ok(got) else "BAD"
        failed += not ok(got)
        print(f"{mark} {name:18} -> {got}")
    global board
    real = board
    board = lambda slug: (_ for _ in ()).throw(RuntimeError("cli down"))  # noqa: E731
    got = run("x")
    board = real
    unreadable_ok = got == "BOARD-UNREADABLE"
    failed += not unreadable_ok
    print(f"{'ok ' if unreadable_ok else 'BAD'} {'unreadable':18} -> {got}")
    stable = signature([{"id": "b", "status": "blocked"}, {"id": "a", "status": "blocked"}]) == \
        signature([{"id": "a", "status": "blocked"}, {"id": "b", "status": "blocked"}])
    failed += not stable
    print(f"{'ok ' if stable else 'BAD'} {'order-stable':18}")
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--drill"]:
        sys.exit(drill())
    slug = BOARD or (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("KANBAN_BOARD"))
    if not slug:
        sys.exit(__doc__)
    print(run(slug))
