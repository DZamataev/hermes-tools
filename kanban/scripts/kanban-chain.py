#!/usr/bin/env python3
"""Create one slice as an implement -> review -> fix chain, race-free.

  kanban-chain.py --title "<slice>" --task <task.md> --workdir <abs worktree>
                  [--after <id> ...] [--gate-task <gate.md>] [--hold] [--dry-run]

Every card is created BLOCKED through kanban-card.sh (so each carries its role
preamble and notify subscriptions), the edges are checked, then only the head is
released and dispatched — nothing can be claimed before its parent is linked.
Run from a desktop/TUI session, every card is also subscribed to that session
(checked like the edges), so the orchestrator is told when a card blocks or ends.

--after      parents of the implement card (usually the previous slice's fix)
--gate-task  add an operator gate card parented on the fix; it stays blocked
--hold       create everything but release nothing (review the board first)

Prints `impl=<id> review=<id> fix=<id> [gate=<id>] [session=<key>]`.
Run it from inside the repository (or set KANBAN_REPO).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HERMES = os.environ.get("KANBAN_HERMES", "hermes")
CARD = os.path.join(HERE, "kanban-card.sh")
REVIEW_TASK = "Review the implementation chained before you (parent card).\n"
FIX_TASK = "Apply or reject the findings of the review chained before you (parent card).\n"


def board_slug():
    out = subprocess.run(
        ["bash", "-c", f'. "{HERE}/lib.sh"; kanban_load_config; printf %s "$KANBAN_BOARD"'],
        capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout:
        sys.exit(out.stderr.strip() or "cannot read KANBAN_BOARD")
    return out.stdout


def card(role, title, task, workdir, parents, dry):
    if dry:
        print(f"[dry-run] {role}: {title!r} parents={parents}", file=sys.stderr)
        return f"<{role}>"
    env = dict(os.environ, KANBAN_BLOCKED="1")
    out = subprocess.run(["bash", CARD, role, title, task, workdir, *parents],
                         capture_output=True, text=True, env=env)
    card_id = out.stdout.strip()
    if out.returncode != 0 or not card_id.startswith("t_"):
        sys.exit(f"{role} card failed: {out.stderr.strip() or out.stdout.strip()}")
    return card_id


def parents_of(board, card_id):
    out = subprocess.run([HERMES, "kanban", "--board", board, "show", card_id, "--json"],
                         capture_output=True, text=True)
    return json.loads(out.stdout).get("parents", [])


def session_key():
    """The calling desktop/TUI session that kanban-card.sh subscribes (lib.sh decides), or ''."""
    out = subprocess.run(["bash", "-c", f'. "{HERE}/lib.sh"; kanban_session_key'],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def follows(board, card_id, session):
    out = subprocess.run([HERMES, "kanban", "--board", board, "notify-list", card_id, "--json"],
                         capture_output=True, text=True)
    try:
        subs = json.loads(out.stdout or "[]")
    except ValueError:
        return False
    return any((s.get("platform") or "").lower() == "tui" and s.get("chat_id") == session for s in subs)


def hermes(board, *args):
    subprocess.run([HERMES, "kanban", "--board", board, *args], check=True,
                   stdout=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    ap.add_argument("--title", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--after", action="append", default=[])
    ap.add_argument("--gate-task")
    ap.add_argument("--hold", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not os.path.isabs(a.workdir) or not os.path.isdir(a.workdir):
        sys.exit(f"--workdir must be an existing absolute path: {a.workdir}")
    for f in [a.task] + ([a.gate_task] if a.gate_task else []):
        if not os.path.isfile(f):
            sys.exit(f"task file does not exist: {f}")
    os.environ.pop("HERMES_DELEGATED_CHILD_CONTEXT", None)
    board = None if a.dry_run else board_slug()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        rev_task = os.path.join(tmp, "review.md")
        fix_task = os.path.join(tmp, "fix.md")
        open(rev_task, "w").write(REVIEW_TASK)
        open(fix_task, "w").write(FIX_TASK)
        ids = {}
        ids["impl"] = card("impl", f"{a.title}: implement", a.task, a.workdir, a.after, a.dry_run)
        ids["review"] = card("review", f"{a.title}: review", rev_task, a.workdir, [ids["impl"]], a.dry_run)
        ids["fix"] = card("fix", f"{a.title}: fix", fix_task, a.workdir, [ids["review"]], a.dry_run)
        if a.gate_task:
            ids["gate"] = card("gate", f"{a.title}: operator gate", a.gate_task, a.workdir,
                               [ids["fix"]], a.dry_run)

    if not a.dry_run:
        expect = [("impl", a.after), ("review", [ids["impl"]]), ("fix", [ids["review"]])]
        if "gate" in ids:
            expect.append(("gate", [ids["fix"]]))
        for role, want in expect:
            got = parents_of(board, ids[role])
            if sorted(got) != sorted(want):
                sys.exit(f"{role} {ids[role]} has parents {got}, expected {want} — board left blocked")
        # The orchestrating session must hear this chain's blocks and completions: check it follows every card
        # before anything runs, the same way the edges are checked.
        session = session_key()
        if session:
            missing = [f"{role} {ids[role]}" for role in ids if not follows(board, ids[role], session)]
            if missing:
                sys.exit(f"session {session} is not subscribed to {', '.join(missing)} — board left blocked")
            ids["session"] = session
        if not a.hold:
            # Only the implementation head and its two followers are released;
            # dependency edges keep review/fix waiting. The gate stays blocked.
            hermes(board, "unblock", ids["impl"], ids["review"], ids["fix"])
            hermes(board, "dispatch")

    print(" ".join(f"{k}={v}" for k, v in ids.items()))


if __name__ == "__main__":
    main()
