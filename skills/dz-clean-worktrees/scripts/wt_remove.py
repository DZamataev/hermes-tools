#!/usr/bin/env python3
"""Remove worktrees the operator picked, after checking each one again.

Usage:
  wt_remove.py PATH [PATH...]            # dry run: prints what would happen
  wt_remove.py --yes PATH [PATH...]      # does it
  wt_remove.py --yes --keep-branch PATH  # keep the local branch

Each PATH is re-analyzed right before removal (wt_scan.analyze), so a worktree
that got a new commit, an edit or a running process since the listing is
refused, not removed. Per worktree it then runs, from the main checkout:
  git worktree remove <path>       (--force only to get past initialized
                                    submodules, which the check proved clean
                                    and pushed)
  git branch -D <branch>           (merged work only; the sha is printed so
                                    `git branch <name> <sha>` restores it)
  git worktree prune
Remote branches are never touched. An already-missing directory is pruned.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wt_scan as s  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--yes", action="store_true", help="actually remove (default: dry run)")
    ap.add_argument("--keep-branch", action="store_true", help="do not delete the local branch")
    a = ap.parse_args(argv)

    rows = s.scan("/", only=a.paths, size=True)
    found = {s.real(r["path"]) if os.path.exists(r["path"]) else r["path"] for r in rows}
    failures = 0
    for p in a.paths:
        rp = s.real(p)
        if not any(s.inside(rp, f) for f in found):
            print("SKIP %s: not a linked worktree (a main checkout is never removed)" % p)
            failures += 1
    freed = 0
    for r in rows:
        path = r["path"]
        if not r["removable"]:
            print("REFUSE %s: %s" % (path, "; ".join(r["blockers"]) or r["state"]))
            failures += 1
            continue
        main = r["repo_path"]
        branch = r.get("branch")
        cmds = []
        if r["state"] == "prunable":
            cmds.append(["git", "-C", main, "worktree", "prune"])
        else:
            rm = ["git", "-C", main, "worktree", "remove"]
            if r.get("submodules"):
                rm.append("--force")
            cmds.append(rm + [path])
            if branch and not a.keep_branch:
                cmds.append(["git", "-C", main, "branch", "-D", branch])
            cmds.append(["git", "-C", main, "worktree", "prune"])
        print("%s %s  (%s, %s)" % ("REMOVE" if a.yes else "WOULD REMOVE", path, r["state"],
                                   s.human_size(r.get("size_kb"))))
        for w in r["warnings"]:
            print("  ! " + w)
        for c in cmds:
            print("  $ " + " ".join(c))
        if branch and not a.keep_branch and r["state"] != "prunable":
            print("  restore branch: git -C %s branch %s %s" % (main, branch, r["head"]))
        if not a.yes:
            continue
        ok = True
        for c in cmds:
            rc, out, err = s.run(c)
            if rc:
                print("  FAILED (%d): %s" % (rc, (err or out).strip()))
                ok = False
                break
        if ok and os.path.exists(path):
            # git removes the tree it knows; leftovers (ignored build output) can remain only
            # when git refused nothing yet left the directory — report rather than rm -rf.
            print("  note: %s still exists; inspect before deleting by hand" % path)
        if ok:
            freed += r.get("size_kb") or 0
        else:
            failures += 1
    if a.yes:
        print("freed about %s" % s.human_size(freed))
    elif rows:
        print("dry run; re-run with --yes to remove")
    return 1 if failures else 0


if __name__ == "__main__":
    shutil.which("git") or sys.exit("git is required")
    sys.exit(main())
