#!/usr/bin/env python3
"""Detach a finished Hermes session: drop its Kanban notifications and unpin it.

Usage:
  session_close.py [--session ID] [--json]          # dry run: what would change
  session_close.py [--session ID] --yes [--keep-pin] [--keep-subs]

ID defaults to $HERMES_SESSION_ID (the calling session). The session's whole
compression lineage counts as the session: a long chat continues under new
ids, and card subscriptions made before a compression still carry the old one.
Branches (/branch) and subagent sessions are separate sessions and are left
alone.

Kanban: every board is searched for `tui:<lineage id>` subscriptions; each is
removed with `hermes kanban --board B notify-unsubscribe`. Cards that are not
done/archived are listed as a warning — nothing reports on them to this
session any more, so whoever orchestrates them next must subscribe.

Pin: every pinned id of the lineage is unpinned with `hermes sessions unpin`.
Hermes Desktop picks the change up live (it treats sessions.pinned in
state.db as the source of truth).

Environment: DZ_HERMES overrides the hermes binary (tests use a fake);
HERMES_HOME locates state.db.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys

HERMES = os.environ.get("DZ_HERMES") or "hermes"
CLOSED = {"done", "archived"}


def hermes(*args):
    r = subprocess.run([HERMES, *args], capture_output=True, text=True, errors="replace")
    return r.returncode, r.stdout, r.stderr


def hermes_json(*args):
    rc, out, err = hermes(*args, "--json")
    if rc:
        raise RuntimeError("hermes %s: %s" % (" ".join(args), (err or out).strip()[:300]))
    return json.loads(out or "[]")


def state_db():
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, "state.db")


def lineage(session_id):
    """[{id, pinned, title}] of the compression lineage containing session_id, root first.

    Same edge Hermes uses (_COMPRESSION_CHILD_SQL): a child continues its parent only when
    the parent ended with end_reason 'compression'. Branches, resets and subagents are
    separate sessions."""
    path = state_db()
    if not os.path.exists(path):
        return [{"id": session_id, "pinned": None, "title": None}]
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    q = "SELECT id, parent_session_id, end_reason, ended_at, pinned, title FROM sessions WHERE "
    # The continuation, not a subagent or branch spawned from the parent before it rotated.
    cont = (" AND started_at >= ? AND COALESCE(source,'') != 'tool'"
            " AND json_extract(COALESCE(model_config,'{}'), '$._delegate_from') IS NULL"
            " AND json_extract(COALESCE(model_config,'{}'), '$._branched_from') IS NULL")
    try:
        row = conn.execute(q + "id = ?", (session_id,)).fetchone()
        if row is None:
            return [{"id": session_id, "pinned": None, "title": None}]
        root, seen = row, {row["id"]}
        nxt = lambda n: conn.execute(q + "parent_session_id = ?" + cont + " ORDER BY started_at LIMIT 1",
                                     (n["id"], n["ended_at"] or 0)).fetchone()
        while root["parent_session_id"]:
            parent = conn.execute(q + "id = ?", (root["parent_session_id"],)).fetchone()
            if parent is None or parent["end_reason"] != "compression" or parent["id"] in seen:
                break
            succ = nxt(parent)
            if succ is None or succ["id"] != root["id"]:
                break   # root is a subagent/branch of parent, not its continuation
            seen.add(parent["id"])
            root = parent
        chain, node = [root], root
        while node["end_reason"] == "compression":
            child = nxt(node)
            if child is None or child["id"] in {c["id"] for c in chain}:
                break
            chain.append(child)
            node = child
        return [{"id": c["id"], "pinned": bool(c["pinned"]), "title": c["title"]} for c in chain]
    finally:
        conn.close()


def survey(session_id):
    ids = lineage(session_id)
    idset = {s["id"] for s in ids}
    subs, open_cards, errors = [], [], []
    try:
        boards = [b["slug"] for b in hermes_json("kanban", "boards", "list") if not b.get("archived")]
    except (RuntimeError, ValueError) as e:
        boards, errors = [], ["kanban boards: %s" % e]
    for b in boards:
        try:
            mine = [s for s in hermes_json("kanban", "--board", b, "notify-list")
                    if s.get("platform") == "tui" and s.get("chat_id") in idset]
        except (RuntimeError, ValueError) as e:
            errors.append("board %s: %s" % (b, e))
            continue
        if not mine:
            continue
        try:
            status = {t["id"]: (t.get("status"), t.get("title"))
                      for t in hermes_json("kanban", "--board", b, "list", "--archived")}
        except (RuntimeError, ValueError):
            status = {}
        for s in mine:
            st, title = status.get(s["task_id"], (None, None))
            row = {"board": b, "task_id": s["task_id"], "chat_id": s["chat_id"],
                   "thread_id": s.get("thread_id"), "status": st, "title": title}
            subs.append(row)
            if st not in CLOSED:
                open_cards.append(row)
    return {"session": session_id, "lineage": ids, "subscriptions": subs,
            "open_cards": open_cards, "errors": errors}


def apply(rep, keep_pin, keep_subs):
    done = {"unsubscribed": 0, "unpinned": [], "failed": []}
    if not keep_subs:
        for s in rep["subscriptions"]:
            args = ["kanban", "--board", s["board"], "notify-unsubscribe", s["task_id"],
                    "--platform", "tui", "--chat-id", s["chat_id"]]
            if s.get("thread_id"):
                args += ["--thread-id", s["thread_id"]]
            rc, out, err = hermes(*args)
            if rc:
                done["failed"].append("unsubscribe %s/%s: %s" % (s["board"], s["task_id"], (err or out).strip()))
            else:
                done["unsubscribed"] += 1
    if not keep_pin:
        pinned = [s["id"] for s in rep["lineage"] if s["pinned"]]
        if pinned:
            rc, out, err = hermes("sessions", "unpin", *pinned)
            if rc:
                done["failed"].append("unpin: %s" % (err or out).strip())
            else:
                done["unpinned"] = pinned
    return done


def print_report(rep, done=None):
    ids = rep["lineage"]
    print("session %s — lineage: %s" % (rep["session"], ", ".join(
        s["id"] + (" (pinned)" if s["pinned"] else "") for s in ids)))
    by_board = {}
    for s in rep["subscriptions"]:
        by_board.setdefault(s["board"], []).append(s)
    if by_board:
        print("kanban subscriptions: %d on %s" % (len(rep["subscriptions"]), ", ".join(
            "%s (%d)" % (b, len(v)) for b, v in sorted(by_board.items()))))
    else:
        print("kanban subscriptions: none")
    if rep["open_cards"]:
        print("! %d card(s) not done — nothing will report them to this session after unsubscribing:"
              % len(rep["open_cards"]))
        for c in rep["open_cards"]:
            print("  %s %s [%s] %s" % (c["board"], c["task_id"], c["status"], (c["title"] or "")[:70]))
    for e in rep["errors"]:
        print("! " + e)
    if done is None:
        pins = [s["id"] for s in ids if s["pinned"]]
        print("pin: %s" % ("would unpin " + ", ".join(pins) if pins else "not pinned"))
        print("dry run; re-run with --yes")
        return
    print("unsubscribed %d; unpinned %s" % (done["unsubscribed"], ", ".join(done["unpinned"]) or "nothing"))
    for f in done["failed"]:
        print("FAILED " + f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", default=os.environ.get("HERMES_SESSION_ID"))
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--keep-pin", action="store_true")
    ap.add_argument("--keep-subs", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.session:
        ap.error("no --session and no HERMES_SESSION_ID in the environment")
    rep = survey(a.session)
    done = apply(rep, a.keep_pin, a.keep_subs) if a.yes else None
    if a.json:
        json.dump(dict(rep, applied=done), sys.stdout, indent=1, ensure_ascii=False)
        print()
    else:
        print_report(rep, done)
    return 1 if (done and done["failed"]) or rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
