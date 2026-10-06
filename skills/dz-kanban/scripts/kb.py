#!/usr/bin/env python3
"""dz-kanban: the entry point to the Hermes Kanban development method.

  kb.py where      [--repo DIR]                         which project/board this is, which tools are installed
  kb.py setup      [--repo DIR] [--tools DIR]           is the method installed and current? (read-only plan)
  kb.py subscribe  [--board B] [--session S] [--all]    subscribe a session to the board's open cards
  kb.py unsubscribe [--board B] [--session S] [--yes]   drop a session's subscriptions on one board
  kb.py status     [--board B] [--since 7d] [--json]    summarised board state
  kb.py signals    [--board B] [--since 7d] [--json]    friction facts for an orchestrator review

The board comes from --board, else from .kanban/config.env (KANBAN_BOARD) of --repo or the cwd.
The session defaults to $HERMES_SESSION_ID. Reads go to the board's SQLite file read-only; writes go
through `hermes kanban` (DZ_HERMES overrides the binary for tests; HERMES_HOME locates boards).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

HERMES = os.environ.get("DZ_HERMES") or "hermes"
HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
SKILLS = os.path.join(HOME, "skills")
METHOD = "hermes-kanban-development"
OPEN = {"todo", "ready", "running", "blocked", "review", "scheduled", "triage"}
SECRET_KEYS = ("CHAT_ID", "USER_ID", "THREAD_ID")


def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")


def hermes(*args):
    r = run([HERMES, *args])
    return r.returncode, r.stdout, r.stderr


def hermes_json(*args):
    rc, out, err = hermes(*args, "--json")
    if rc:
        raise RuntimeError("hermes %s: %s" % (" ".join(args), (err or out).strip()[:300]))
    return json.loads(out or "[]")


def ago(ts):
    if not ts:
        return "never"
    s = time.time() - ts
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return "%d%s ago" % (s // n, unit)
    return "just now"


def since_ts(spec):
    m = re.fullmatch(r"(\d+)([dhw])", spec)
    if m:
        return time.time() - int(m.group(1)) * {"d": 86400, "h": 3600, "w": 604800}[m.group(2)]
    return dt.datetime.fromisoformat(spec).timestamp()


# ---------------------------------------------------------------- project / board

def git_top(path):
    r = run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    return r.stdout.strip() if r.returncode == 0 else None


def primary_checkout(top):
    r = run(["git", "-C", top, "rev-parse", "--path-format=absolute", "--git-common-dir"])
    common = r.stdout.strip()
    return os.path.dirname(common) if common.endswith("/.git") else top


def read_env(path):
    env = {}
    try:
        for line in open(path):
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)=(.*)", line)
            if m:
                v = m.group(2).strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                env[m.group(1)] = v
    except OSError:
        pass
    return env


def project(repo=None):
    top = git_top(repo or os.getcwd())
    if not top:
        return None
    cfg = os.path.join(top, ".kanban", "config.env")
    if not os.path.exists(cfg):
        prim = primary_checkout(top)
        cfg = os.path.join(prim, ".kanban", "config.env")
        if not os.path.exists(cfg):
            return {"top": top, "config": None}
    env = read_env(cfg)
    prim = primary_checkout(top)
    notify = os.path.join(prim, env.get("KANBAN_NOTIFY_ENV", ".kanban/notify.env"))
    return {"top": top, "primary": prim, "config": cfg, "env": env,
            "board": env.get("KANBAN_BOARD"), "notify_env": notify if os.path.exists(notify) else None}


def board_db(board):
    if board == "default":
        return os.path.join(HOME, "kanban.db")
    return os.path.join(HOME, "kanban", "boards", board, "kanban.db")


def connect(board):
    path = board_db(board)
    if not os.path.exists(path):
        sys.exit("no board %r (looked for %s); list: hermes kanban boards list" % (board, path))
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_board(a):
    if getattr(a, "board", None):
        return a.board
    p = project(getattr(a, "repo", None))
    if p and p.get("board"):
        return p["board"]
    sys.exit("which board? pass --board, or run inside a repo with .kanban/config.env")


def table_cols(conn, t):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}


# ---------------------------------------------------------------- sessions

def state_db():
    return os.path.join(HOME, "state.db")


def session_rows(ids):
    if not ids or not os.path.exists(state_db()):
        return {}
    conn = sqlite3.connect("file:%s?mode=ro" % state_db(), uri=True)
    conn.row_factory = sqlite3.Row
    cols = table_cols(conn, "sessions")
    want = [c for c in ("id", "title", "ended_at", "end_reason", "pinned", "last_activity_at", "started_at") if c in cols]
    q = "SELECT %s FROM sessions WHERE id IN (%s)" % (",".join(want), ",".join("?" * len(ids)))
    out = {r["id"]: dict(r) for r in conn.execute(q, list(ids))}
    conn.close()
    return out


def lineage(session_id):
    """Compression lineage of a session (same rule as dz-wrapup's session_close.py)."""
    sc = os.path.join(SKILLS, "software-development", "dz-wrapup", "scripts")
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dz-wrapup", "scripts")
    for d in (here, sc):
        if os.path.exists(os.path.join(d, "session_close.py")):
            sys.path.insert(0, d)
            try:
                import session_close  # noqa: E402
                return [s["id"] for s in session_close.lineage(session_id)]
            except Exception:
                break
    return [session_id]


# ---------------------------------------------------------------- where / setup

def installed_method():
    for root, dirs, files in os.walk(SKILLS):
        if os.path.basename(root) == METHOD and "SKILL.md" in files:
            return root
        if root.count(os.sep) - SKILLS.count(os.sep) >= 2:
            dirs[:] = []
    return None


def find_tools(explicit=None):
    for c in (explicit, os.environ.get("DZ_HERMES_TOOLS"), os.path.expanduser("~/dev/hermes/hermes-tools"),
              os.path.expanduser("~/dev/hermes-tools")):
        if c and os.path.exists(os.path.join(c, "kanban", "SKILL.md")):
            return c
    return None


def install_drift(src, dst):
    """Files that differ between the source package and the installed copy, classified."""
    out = []
    skip = {"tests", "PLAN.md", ".DS_Store", "__pycache__"}
    rel = lambda r, b: os.path.relpath(r, b)
    srcf, dstf = set(), set()
    for base, acc in ((src, srcf), (dst, dstf)):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                if f in skip or f.endswith(".pyc"):
                    continue
                acc.add(os.path.join(rel(root, base), f).lstrip("./"))
    tools = git_top(src)
    for f in sorted(srcf | dstf):
        s, d = os.path.join(src, f), os.path.join(dst, f)
        if f not in dstf:
            out.append(("missing in install", f))
        elif f not in srcf:
            out.append(("only in install (not in hermes-tools)", f))
        elif open(s, "rb").read() != open(d, "rb").read():
            h = run(["git", "hash-object", d]).stdout.strip()
            hist = run(["git", "-C", tools, "log", "--all", "--format=", "--raw", "--no-abbrev", "--",
                        os.path.relpath(s, tools)]).stdout if tools else ""
            out.append(("install is an older version" if (" %s " % h) in hist else "edited in install (not in hermes-tools)", f))
    return out


def cmd_where(a):
    p = project(a.repo)
    inst = installed_method()
    tools = find_tools(getattr(a, "tools", None))
    info = {"project": p, "method_installed": inst, "hermes_tools": tools}
    rc, out, _ = hermes("kanban", "create", "--help")
    info["fork_kanban"] = "local-commit-or-none" in re.sub(r"\s", "", out)
    if a.json:
        print(json.dumps(info, indent=1))
        return 0
    if not p:
        print("not in a git repository")
    elif not p.get("config"):
        print("repo %s: no .kanban/config.env — not on the method yet (setup)" % p["top"])
    else:
        print("repo %s → board %s (config %s)" % (p["top"], p["board"], os.path.relpath(p["config"], p["top"])))
        print("notify env: %s" % (p["notify_env"] or "none (cards notify only the orchestrating session)"))
    print("method skill installed: %s" % (inst or "NO"))
    print("hermes-tools checkout: %s" % (tools or "not found"))
    print("hermes has the fork's kanban: %s" % ("yes" if info["fork_kanban"] else "NO — install Hermes from DZamataev/hermes-agent develop"))
    return 0


def cmd_setup(a):
    """Read-only: what has to happen to put this repo on the method or bring it up to date."""
    p = project(a.repo)
    tools = find_tools(a.tools)
    inst = installed_method()
    steps, notes = [], []
    rc, out, _ = hermes("kanban", "create", "--help")
    if "local-commit-or-none" not in re.sub(r"\s", "", out):
        steps.append("install Hermes from the develop branch of github.com/DZamataev/hermes-agent (the method refuses to create cards otherwise)")
    if not tools:
        steps.append("clone github.com/DZamataev/hermes-tools (the method's source) and pass --tools <dir>")
    else:
        dirty = run(["git", "-C", tools, "status", "--porcelain", "kanban"]).stdout.strip()
        behind = run(["git", "-C", tools, "rev-list", "--count", "HEAD..@{upstream}"]).stdout.strip()
        if behind and behind != "0":
            steps.append("git -C %s pull --ff-only   # %s commit(s) behind its upstream (fetch first for a fresh count)" % (tools, behind))
        if dirty:
            notes.append("hermes-tools/kanban has uncommitted changes — install would ship them")
    if not inst:
        steps.append("bash %s/kanban/install.sh" % (tools or "<hermes-tools>"))
    elif tools:
        drift = install_drift(os.path.join(tools, "kanban"), inst)
        older = [f for k, f in drift if k == "install is an older version" or k == "missing in install"]
        local = [(k, f) for k, f in drift if k.startswith(("edited", "only"))]
        for k, f in local:
            notes.append("%s: %s — `install.sh --force` would drop it; port it into hermes-tools/kanban first" % (k, f))
        if older or local:
            steps.append("bash %s/kanban/install.sh --force   # %d file(s) out of date%s" % (
                tools, len(older), ", AFTER porting the local edits above" if local else ""))
    if not p:
        steps.append("run inside the project's git repository (or pass --repo)")
    elif not p.get("config"):
        steps.append("bash %s/scripts/kanban-init.sh <board-slug> %s   # scaffolds .kanban/, role templates, runbook" % (inst or "<method>", p["top"]))
        steps.append("bash %s/scripts/kanban-profiles.sh <prefix> --repo %s" % (inst or "<method>", p["top"]))
        steps.append("hermes kanban boards create <board-slug> --name \"<title>\"")
        steps.append("then follow the method skill's section 3 (bring-up): edit the scaffold, warm the worktree, notify env")
    else:
        b = p["board"]
        boards = []
        try:
            boards = [x["slug"] for x in hermes_json("kanban", "boards", "list")]
        except (RuntimeError, ValueError) as e:
            notes.append(str(e))
        if b not in boards:
            steps.append("hermes kanban boards create %s --name \"<title>\"" % b)
        prefix = p["env"].get("KANBAN_PROFILE_PREFIX")
        if prefix:
            missing = [r for r in ("impl", "review", "fix") if not os.path.isdir(os.path.join(HOME, "profiles", prefix + r))]
            if missing:
                steps.append("bash %s/scripts/kanban-profiles.sh %s --repo %s   # missing: %s" % (inst, prefix, p["primary"], ", ".join(prefix + m for m in missing)))
        if inst:
            r = run(["python3", os.path.join(inst, "scripts", "kanban-sync.py"), "--root", p["primary"], "--json"])
            try:
                rep = [x for x in json.loads(r.stdout) if os.path.realpath(x["path"]) in (os.path.realpath(p["top"]), os.path.realpath(p["primary"]))]
            except ValueError:
                rep = []
                notes.append("kanban-sync.py failed: %s" % (r.stderr or r.stdout)[:200])
            for x in rep:
                if x.get("gaps"):
                    steps.append("python3 %s/scripts/kanban-sync.py --apply %s   # %d rule gap(s) in role templates; review the diff, commit on the operator's word" % (inst, x["path"], len(x["gaps"])))
                if x.get("kind") == "own":
                    notes.append("%s uses its own card script %s — script changes never reach it" % (x["path"], x.get("own_card_tools")))
                if not x.get("runbook_names_stack"):
                    notes.append("%s: runbook lacks the Stack section naming the fork and hermes-tools" % x["path"])
                if x.get("dirty_templates"):
                    notes.append("%s: role templates have uncommitted edits — --apply skips them" % x["path"])
        if p["notify_env"] is None:
            notes.append("no notify env (%s): only the orchestrating session hears the cards" % p["env"].get("KANBAN_NOTIFY_ENV", ".kanban/notify.env"))
    res = {"project": p and p.get("top"), "board": p and p.get("board"), "steps": steps, "notes": notes}
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print("setup plan for %s (board %s):" % (res["project"], res["board"]))
        if not steps:
            print("  nothing to do — the method is installed and the project is current")
        for i, s in enumerate(steps, 1):
            print("  %d. %s" % (i, s))
        for n in notes:
            print("  ! " + n)
    return 0


# ---------------------------------------------------------------- subscribe

def cmd_subscribe(a):
    board = resolve_board(a)
    sid = a.session or os.environ.get("HERMES_SESSION_ID")
    if not sid:
        sys.exit("no session: pass --session or run inside a Hermes session")
    conn = connect(board)
    q = "SELECT id, status, title FROM tasks"
    tasks = [dict(r) for r in conn.execute(q)]
    targets = [t for t in tasks if a.all and t["status"] != "archived" or t["status"] in OPEN]
    have = {r["task_id"] for r in conn.execute(
        "SELECT task_id FROM kanban_notify_subs WHERE platform='tui' AND chat_id=?", (sid,))}
    todo = [t for t in targets if t["id"] not in have]
    print("board %s: %d card(s) to watch, %d already subscribed, subscribing %d to tui:%s" % (
        board, len(targets), len(targets) - len(todo), len(todo), sid))
    fails = 0
    for t in todo:
        rc, out, err = hermes("kanban", "--board", board, "notify-subscribe", t["id"], "--platform", "tui",
                              "--chat-id", sid, "--delivery-mode", "notify")
        if rc:
            fails += 1
            print("  FAILED %s: %s" % (t["id"], (err or out).strip()[:200]))
    print("note: cards created later are subscribed by kanban-card.sh / kanban-chain.py when run from this session")
    return 1 if fails else 0


def cmd_unsubscribe(a):
    board = resolve_board(a)
    sid = a.session or os.environ.get("HERMES_SESSION_ID")
    if not sid:
        sys.exit("no session: pass --session or run inside a Hermes session")
    sc = None
    for d in (os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dz-wrapup", "scripts"),
              os.path.join(SKILLS, "software-development", "dz-wrapup", "scripts")):
        if os.path.exists(os.path.join(d, "session_close.py")):
            sc = os.path.join(d, "session_close.py")
            break
    if not sc:
        sys.exit("dz-wrapup is not installed next to this skill (session_close.py does the unsubscribe)")
    args = ["python3", sc, "--session", sid, "--board", board, "--keep-pin"] + (["--yes"] if a.yes else [])
    r = run(args)
    sys.stdout.write(r.stdout + r.stderr)
    return r.returncode


# ---------------------------------------------------------------- status

def chain_key(title):
    """'Beta access gate (93): implement' -> 'Beta access gate (93)'."""
    m = re.match(r"(.*?):\s*(implement|review|fix|gate|research)\s*$", title or "", re.I)
    return m.group(1).strip() if m else None


def gather(board, since):
    conn = connect(board)
    tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks")]
    by_id = {t["id"]: t for t in tasks}
    counts = collections.Counter(t["status"] for t in tasks)
    events = [dict(r) for r in conn.execute(
        "SELECT id, task_id, run_id, kind, payload, created_at FROM task_events WHERE created_at >= ? ORDER BY id", (since,))]
    for e in events:
        try:
            e["payload"] = json.loads(e["payload"]) if e["payload"] else {}
        except ValueError:
            e["payload"] = {}
    runs = [dict(r) for r in conn.execute(
        "SELECT id, task_id, profile, status, outcome, started_at, ended_at, error, metadata, summary FROM task_runs WHERE started_at >= ?",
        (since,))]
    subs = [dict(r) for r in conn.execute("SELECT * FROM kanban_notify_subs")]
    links = [dict(r) for r in conn.execute("SELECT parent_id, child_id FROM task_links")]
    last_q = conn.execute("SELECT payload, created_at FROM task_events WHERE kind='board_quiescent' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return tasks, by_id, counts, events, runs, subs, links, last_q


def cmd_status(a):
    board = resolve_board(a)
    since = since_ts(a.since)
    tasks, by_id, counts, events, runs, subs, links, last_q = gather(board, since)
    res = {"board": board, "since": dt.datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M"),
           "counts": dict(counts), "problems": []}

    # Throughput
    done = [t for t in tasks if t["status"] == "done" and (t.get("completed_at") or 0) >= since]
    res["done_in_period"] = len(done)
    chains = collections.OrderedDict()
    for t in sorted(done, key=lambda t: t["completed_at"]):
        k = chain_key(t["title"]) or t["title"]
        chains.setdefault(k, []).append(t)
    landed = [k for k, v in chains.items() if any(re.search(r":\s*fix\s*$", t["title"] or "", re.I) for t in v)]
    res["chains_finished"] = landed
    last_done = max((t.get("completed_at") or 0 for t in tasks if t["status"] == "done"), default=0)
    res["last_completion"] = last_done or None

    # Plans: open cards grouped by chain, in creation order
    open_cards = sorted([t for t in tasks if t["status"] in OPEN], key=lambda t: t["created_at"])
    plan = collections.OrderedDict()
    for t in open_cards:
        plan.setdefault(chain_key(t["title"]) or t["title"], []).append(
            {"id": t["id"], "status": t["status"], "assignee": t["assignee"], "title": t["title"],
             "block_kind": t.get("block_kind")})
    res["plan"] = plan

    # Subscriptions
    sessions = collections.defaultdict(list)
    others = collections.defaultdict(list)
    for s in subs:
        if by_id.get(s["task_id"], {}).get("status") == "archived":
            continue
        if s["platform"] == "tui":
            sessions[s["chat_id"]].append(s["task_id"])
        else:
            others["%s:%s" % (s["platform"], "<chat>" + (":%s" % s["thread_id"] if s.get("thread_id") else ""))].append(s["task_id"])
    srows = session_rows(list(sessions))
    res["sessions"] = []
    for sid, tids in sessions.items():
        row = srows.get(sid, {})
        open_n = sum(1 for t in tids if by_id.get(t, {}).get("status") in OPEN)
        res["sessions"].append({"session": sid, "title": row.get("title"), "cards": len(tids), "open_cards": open_n,
                                "ended": bool(row.get("ended_at")), "end_reason": row.get("end_reason"),
                                "last_activity": row.get("last_activity_at")})
    res["chats"] = [{"target": k, "cards": len(v)} for k, v in others.items()]
    orphaned = [t for t in open_cards if not any(t["id"] in v for v in sessions.values())]
    unheard = [t for t in open_cards if not any(t["id"] in v for v in sessions.values()) and not any(t["id"] in v for v in others.values())]

    # Problems
    for t in tasks:
        if t["status"] == "blocked":
            reason = None
            for e in reversed(events):
                if e["task_id"] == t["id"] and e["kind"] == "blocked":
                    reason = e["payload"].get("reason")
                    break
            parents = [l["parent_id"] for l in links if l["child_id"] == t["id"]]
            open_parents = [p for p in parents if by_id.get(p, {}).get("status") in OPEN]
            if reason == "initial_status" and open_parents:
                continue  # waiting in its chain, by design
            if reason == "initial_status" or reason is None:
                kind = "waits for release (created blocked; nothing upstream is open)"
            else:
                kind = "blocked: %s" % (reason or "")[:160]
            res["problems"].append("%s %s — %s" % (t["id"], t["title"][:60], kind))
        elif t["status"] == "running":
            hb = t.get("last_heartbeat_at") or t.get("started_at")
            if hb and time.time() - hb > 1800:
                res["problems"].append("%s %s — running, no heartbeat for %s" % (t["id"], t["title"][:60], ago(hb)))
    for kind, label in (("gave_up", "gave up after retries"), ("crashed", "worker crashed"),
                        ("protocol_violation", "protocol violation"), ("block_loop_detected", "block loop")):
        n = [e for e in events if e["kind"] == kind]
        if n:
            res["problems"].append("%d× %s in the period (last: %s %s)" % (
                len(n), label, n[-1]["task_id"], ago(n[-1]["created_at"])))
    fb = [e for e in events if e["kind"] == "model_fallback"]
    if fb:
        res["problems"].append("%d run(s) fell back to another model (last %s → %s)" % (
            len(fb), fb[-1]["payload"].get("from_model"), "%s/%s" % (fb[-1]["payload"].get("to_provider"), fb[-1]["payload"].get("to_model"))))
    rl = [e for e in events if e["kind"] == "rate_limited"]
    if rl:
        res["problems"].append("%d quota wall(s) — cards requeued" % len(rl))
    if unheard:
        res["problems"].append("%d open card(s) notify nobody: %s" % (len(unheard), ", ".join(t["id"] for t in unheard[:6])))
    elif orphaned:
        res["problems"].append("%d open card(s) have no subscribed session (chat only): %s" % (
            len(orphaned), ", ".join(t["id"] for t in orphaned[:6])))
    dead = [s for s in res["sessions"] if s["open_cards"] and s["ended"] and s["end_reason"] != "compression"]
    for s in dead:
        res["problems"].append("session %s (%s) ended (%s) but is subscribed to %d open card(s)" % (
            s["session"], s["title"], s["end_reason"], s["open_cards"]))
    if os.path.exists(os.path.join(HOME, "ESTOP")):
        res["problems"].append("hermes pause is engaged (%s/ESTOP): no card will be dispatched" % HOME)
    if last_q:
        try:
            res["last_quiescent"] = {"at": last_q["created_at"], **json.loads(last_q["payload"])}
        except ValueError:
            pass

    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
        return 0
    c = res["counts"]
    print("board %s — %s" % (board, ", ".join("%s %d" % (k, c[k]) for k in sorted(c))))
    print("since %s: %d card(s) done, %d chain(s) finished%s; last completion %s" % (
        res["since"], res["done_in_period"], len(landed), (": " + "; ".join(landed[-6:])) if landed else "", ago(res["last_completion"])))
    print("\n== plan (open cards)")
    if not plan:
        print("  none — the board is idle")
    for k, cards in plan.items():
        print("  %s: %s" % (k, ", ".join("%s %s%s" % (x["id"], x["status"], "/" + x["block_kind"] if x.get("block_kind") else "") for x in cards)))
    print("\n== notifications")
    for s in sorted(res["sessions"], key=lambda s: -s["open_cards"]):
        print("  session %s «%s»: %d card(s), %d open%s" % (s["session"], (s["title"] or "?")[:50], s["cards"], s["open_cards"],
                                                           ", ENDED (%s)" % s["end_reason"] if s["ended"] else ""))
    for ch in res["chats"]:
        print("  chat %s: %d card(s)" % (ch["target"], ch["cards"]))
    if not res["sessions"] and not res["chats"]:
        print("  nobody is subscribed")
    print("\n== problems")
    if not res["problems"]:
        print("  none")
    for p in res["problems"]:
        print("  - " + p)
    return 0


# ---------------------------------------------------------------- signals (for review)

def cmd_signals(a):
    board = resolve_board(a)
    since = since_ts(a.since)
    tasks, by_id, counts, events, runs, subs, links, last_q = gather(board, since)
    k = collections.Counter(e["kind"] for e in events)
    res = {"board": board, "since": dt.datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M"), "event_counts": dict(k)}

    done_runs = [r for r in runs if r["outcome"] == "completed" and r["ended_at"] and r["started_at"]]
    by_role = collections.defaultdict(list)
    for r in done_runs:
        by_role[r["profile"]].append(r["ended_at"] - r["started_at"])
    res["wall_minutes_by_profile"] = {p: {"runs": len(v), "median": round(sorted(v)[len(v) // 2] / 60, 1),
                                          "max": round(max(v) / 60, 1)} for p, v in by_role.items()}
    res["outcomes"] = dict(collections.Counter(r["outcome"] or r["status"] for r in runs))

    review_findings, no_change = [], 0
    for r in runs:
        try:
            md = json.loads(r["metadata"]) if r["metadata"] else {}
        except ValueError:
            md = {}
        if "findings" in md and isinstance(md["findings"], list) and (r["profile"] or "").endswith("review"):
            review_findings.append(len(md["findings"]))
        if md.get("no_change"):
            no_change += 1
    res["reviews"] = {"count": len(review_findings), "with_findings": sum(1 for n in review_findings if n),
                      "findings_total": sum(review_findings)}
    res["fix_no_change"] = no_change

    res["questions"] = [{"task": e["task_id"], "at": e["created_at"], "text": (e["payload"].get("body") or "")[:400]}
                        for e in events if e["kind"] == "question"]
    res["blocks"] = [{"task": e["task_id"], "title": by_id.get(e["task_id"], {}).get("title"),
                      "reason": (e["payload"].get("reason") or "")[:300]}
                     for e in events if e["kind"] == "blocked" and e["payload"].get("reason") not in (None, "initial_status")]
    res["failures"] = [{"task": e["task_id"], "kind": e["kind"],
                        "error": (e["payload"].get("error") or e["payload"].get("worker_output") or "")[:300]}
                       for e in events if e["kind"] in ("crashed", "gave_up", "protocol_violation", "block_loop_detected")]
    res["fallbacks"] = [e["payload"] for e in events if e["kind"] == "model_fallback"]
    comments = collections.Counter()
    conn = connect(board)
    for r in conn.execute("SELECT author, task_id FROM task_comments WHERE created_at >= ?", (since,)):
        comments[r["author"] or "?"] += 1
    res["comments_by_author"] = dict(comments)
    res["archived_in_period"] = sum(1 for e in events if e["kind"] == "archived")
    res["replaced_or_respawned"] = k.get("respawn_guarded", 0)
    retried = collections.Counter(r["task_id"] for r in runs)
    res["cards_with_retries"] = [{"task": t, "runs": n, "title": by_id.get(t, {}).get("title")}
                                 for t, n in retried.most_common(8) if n > 2]
    if a.json:
        print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
        return 0
    print("board %s, since %s" % (board, res["since"]))
    print("runs: %s" % res["outcomes"])
    print("wall time (min) by profile: %s" % res["wall_minutes_by_profile"])
    print("reviews: %(count)d, %(with_findings)d with findings, %(findings_total)d findings total" % res["reviews"])
    print("fix cards closed no-change: %d" % no_change)
    print("orchestrator comments: %s" % res["comments_by_author"])
    print("questions from workers: %d" % len(res["questions"]))
    for q in res["questions"][-8:]:
        print("  ? %s: %s" % (q["task"], q["text"].replace("\n", " ")[:200]))
    print("real blocks: %d" % len(res["blocks"]))
    for b in res["blocks"][-8:]:
        print("  ⛔ %s %s: %s" % (b["task"], (b["title"] or "")[:40], b["reason"].replace("\n", " ")[:160]))
    print("failures: %d" % len(res["failures"]))
    for f in res["failures"][-8:]:
        print("  ✗ %s %s: %s" % (f["task"], f["kind"], f["error"].replace("\n", " ")[:160]))
    print("model fallbacks: %d; archived: %d; cards with >2 runs: %s" % (
        len(res["fallbacks"]), res["archived_in_period"], ", ".join("%s(%d)" % (c["task"], c["runs"]) for c in res["cards_with_retries"]) or "none"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("where", "setup", "subscribe", "unsubscribe", "status", "signals"):
        s = sub.add_parser(name)
        s.add_argument("--repo")
        s.add_argument("--json", action="store_true")
        if name not in ("where", "setup"):
            s.add_argument("--board")
        if name in ("where", "setup"):
            s.add_argument("--tools")
        if name in ("subscribe", "unsubscribe"):
            s.add_argument("--session")
        if name == "subscribe":
            s.add_argument("--all", action="store_true", help="also done cards (default: open cards only)")
        if name == "unsubscribe":
            s.add_argument("--yes", action="store_true")
        if name in ("status", "signals"):
            s.add_argument("--since", default="7d")
    a = ap.parse_args(argv)
    return {"where": cmd_where, "setup": cmd_setup, "subscribe": cmd_subscribe, "unsubscribe": cmd_unsubscribe,
            "status": cmd_status, "signals": cmd_signals}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
