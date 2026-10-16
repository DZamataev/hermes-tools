#!/usr/bin/env python3
"""Pin a kanban card to a profile and a reasoning effort.

  kanban-effort.py --board <slug> <card_id> <effort> [--profile <name>]

effort: minimal|low|medium|high|xhigh|max|ultra|none, or 'inherit' to clear
(the worker then uses the profile's agent.reasoning_effort). The dispatcher
passes it as `--reasoning <effort>` when it spawns the worker. `hermes kanban
create` has no flag for it, hence this script. Run it while the card is still
blocked/todo: a running worker keeps the effort it started with.
"""
import argparse
import os
import subprocess
import sys

# The board database API lives in the running Hermes install, not on PYTHONPATH.
sys.path.insert(0, os.environ.get("HERMES_AGENT_DIR",
                                  os.path.expanduser("~/.hermes/hermes-agent")))
from hermes_cli import kanban_db  # noqa: E402
from hermes_cli.kanban_db_connect import connect  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("card")
    ap.add_argument("effort")
    ap.add_argument("--profile")
    a = ap.parse_args()
    effort = None if a.effort == "inherit" else a.effort
    conn = connect(board=a.board)
    try:
        if not kanban_db.set_reasoning_effort(conn, a.card, effort):
            sys.exit(f"card {a.card} not found or archived")
        conn.commit()
    finally:
        conn.close()
    if a.profile:
        subprocess.run(["hermes", "kanban", "--board", a.board, "reassign", a.card, a.profile], check=True)
    print(f"{a.card}: effort={effort or 'inherit'}" + (f" profile={a.profile}" if a.profile else ""))


if __name__ == "__main__":
    main()
