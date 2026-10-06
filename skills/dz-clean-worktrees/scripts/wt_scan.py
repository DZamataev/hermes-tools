#!/usr/bin/env python3
"""Find linked git worktrees under a directory and say which ones are safe to delete.

Usage:
  wt_scan.py ROOT [--json] [--depth N] [--target PATTERN]... [--no-size]
  wt_scan.py --only PATH [--json]        # just the worktree that contains PATH

ROOT is the only place looked at: a worktree is reported only when its own
directory is inside ROOT, whatever repository it belongs to.

Read-only. Git runs with GIT_OPTIONAL_LOCKS=0, so even `git status` leaves the
index alone; nothing is fetched, so remote refs are as fresh as the last fetch.

A worktree is a removal candidate when ALL hold:
  - its HEAD is in an integration branch (reachable, rebased, squashed, or
    every file it changed is identical there), or it never had a commit;
  - nothing uncommitted or untracked; no submodule commit missing on a remote;
  - not locked and no running process has its cwd inside it.
Integration branches: refs whose name (without the remote) matches --target
patterns (default: main master develop dev trunk dev/* release/* releases/*
release-*) plus whatever origin/HEAD points at.

Safe to delete is not the same as worth deleting. Each row also gets a
placement and a recommendation:
  managed  the worktree's parent directory ends with --managed-suffix
           (default -wt, e.g. ~/dev/hermes-wt/<name>): the operator keeps
           worktrees there on purpose → recommend keep
  hidden   inside its own repository (.claude/worktrees, .worktrees, tmp/…)
           or under any dot- or tmp directory → recommend delete
  other    anywhere else, including a worktree that is itself named *-wt
           → recommend delete
Only removable rows can be recommended for deletion.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import fnmatch
import json
import os
import subprocess
import sys

ENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0")
SKIP_DIRS = {"node_modules", "Pods", "DerivedData", "build", "dist", "target",
             "__pycache__", "venv", ".venv", ".gradle", ".build"}
DEFAULT_TARGETS = ["main", "master", "develop", "dev", "trunk",
                   "dev/*", "release/*", "releases/*", "release-*"]
SQUASH_WINDOW = 45 * 86400   # look for squash/rebase copies this far before the branch's first commit
MANAGED_SUFFIX = "-wt"
TMP_NAMES = {"tmp", "temp", ".tmp", "scratch"}


def placement(path, repo_main, root, suffix=MANAGED_SUFFIX):
    """managed / hidden / other — see the module docstring."""
    parent = os.path.dirname(path.rstrip(os.sep))
    if suffix and os.path.basename(parent).endswith(suffix):
        return "managed"
    if inside(path, repo_main.rstrip(os.sep)):
        return "hidden"
    rel = os.path.relpath(parent, root) if inside(path, root) else parent
    parts = [p for p in rel.split(os.sep) if p not in ("", ".")]
    if any(p.startswith(".") or p.lower() in TMP_NAMES for p in parts):
        return "hidden"
    return "other"


def run(cmd, cwd=None, input=None):
    r = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True, text=True,
                       env=ENV, errors="replace")
    return r.returncode, r.stdout, r.stderr


def git(common, *args, input=None):
    return run(["git", "--git-dir", common, *args], input=input)


def gwt(path, *args):
    return run(["git", "-C", path, *args])


def iso(ts):
    return dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M") if ts else None


def inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def real(p):
    return os.path.realpath(os.path.abspath(os.path.expanduser(p)))


# ---------------------------------------------------------------- discovery

def common_dir(path):
    rc, out, _ = gwt(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return real(out.strip()) if rc == 0 and out.strip() else None


def find_repos(root, depth):
    """Common git dirs of every working tree found under root (not descending into one)."""
    found = set()
    base = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames or ".git" in filenames:
            c = common_dir(dirpath)
            if c:
                found.add(c)
            dirnames[:] = []
            continue
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
    return sorted(found)


def list_worktrees(common):
    rc, out, _ = git(common, "worktree", "list", "--porcelain")
    if rc:
        return []
    res = []
    for i, block in enumerate(b for b in out.strip().split("\n\n") if b.strip()):
        d = {"main": i == 0}
        for line in block.splitlines():
            k, _, v = line.partition(" ")
            d[k] = v if v else True
        res.append(d)
    return res


def processes_by_cwd():
    """[(pid, command, cwd)] for every process lsof can see."""
    rc, out, _ = run(["lsof", "-nP", "-w", "-d", "cwd", "-Fpcn"])
    procs, pid, cmd = [], None, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid, cmd = line[1:], None
        elif line.startswith("c"):
            cmd = line[1:]
        elif line.startswith("n") and pid:
            procs.append((pid, cmd, real(line[1:])))
    return procs


# ---------------------------------------------------------------- repository

class Repo:
    def __init__(self, common, patterns):
        self.common = common
        self.worktrees = list_worktrees(common)
        self.main_path = self.worktrees[0].get("worktree", common) if self.worktrees else common
        self.name = os.path.basename(self.main_path.rstrip(os.sep))
        rc, out, _ = git(common, "remote")
        self.remotes = out.split()
        rc, out, _ = git(common, "for-each-ref", "--format=%(refname)%09%(objectname)",
                         "refs/heads", "refs/remotes")
        self.refs = {}
        for line in out.splitlines():
            ref, _, sha = line.partition("\t")
            if not ref.endswith("/HEAD"):
                self.refs[ref] = sha
        rc, out, _ = git(common, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        self.default = out.strip() or None
        self.targets = {r for r in self.refs if any(fnmatch.fnmatchcase(self.short(r), p) for p in patterns)}
        if self.default in self.refs:
            self.targets.add(self.default)
            local = "refs/heads/" + self.short(self.default)
            if local in self.refs:
                self.targets.add(local)

    def short(self, ref):
        if ref.startswith("refs/heads/"):
            return ref[len("refs/heads/"):]
        if ref.startswith("refs/remotes/"):
            rest = ref[len("refs/remotes/"):]
            for r in sorted(self.remotes, key=len, reverse=True):
                if rest.startswith(r + "/"):
                    return rest[len(r) + 1:]
            return rest.split("/", 1)[-1]
        return ref

    @staticmethod
    def display(ref):
        for p in ("refs/heads/", "refs/remotes/"):
            if ref.startswith(p):
                return ref[len(p):]
        return ref


# ---------------------------------------------------------------- analysis

def read_reflog(admin):
    """[(timestamp, new_sha, message)] of the worktree's own HEAD log."""
    entries = []
    try:
        with open(os.path.join(admin, "logs", "HEAD"), errors="replace") as f:
            for line in f:
                head, _, msg = line.rstrip("\n").partition("\t")
                parts = head.split()
                ts = int(parts[-2]) if len(parts) >= 2 and parts[-2].isdigit() else None
                entries.append((ts, parts[1] if len(parts) > 1 else None, msg))
    except OSError:
        pass
    return entries


# Reflog entries that are work done in this worktree (not checkouts, resets, pulls or merges).
WORK_PREFIXES = ("commit", "cherry-pick", "rebase (pick)", "rebase -i (pick)", "revert")


def own_subjects(common, shas):
    """Subjects of the non-merge commits among shas, newest first, without repeats."""
    if not shas:
        return []
    rc, out, _ = git(common, "log", "--no-walk=unsorted", "--no-merges", "--format=%s", "--stdin",
                     input="\n".join(reversed(shas)) + "\n")
    seen, res = set(), []
    for s in out.splitlines():
        if s and s not in seen and not s.startswith(("fixup! ", "squash! ")):
            seen.add(s)
            res.append(s)
    return res


def patch_ids(common, rev_range, since=None):
    args = ["log", "-p", "--no-merges", "--format=commit %H %ct"]
    if since:
        args.append("--since=%d" % since)
    rc, log, _ = git(common, *args, rev_range)
    if rc or not log.strip():
        return {}
    dates = {}
    for line in log.splitlines():
        if line.startswith("commit "):
            p = line.split()
            if len(p) == 3:
                dates[p[1]] = int(p[2])
    rc, out, _ = run(["git", "--git-dir", common, "patch-id", "--stable"], input=log)
    ids = {}
    for line in out.splitlines():
        pid, _, sha = line.partition(" ")
        ids.setdefault(pid, (sha, dates.get(sha)))
    return ids


def commit_time(common, rev):
    rc, out, _ = git(common, "log", "-1", "--format=%ct", rev)
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else None


def find_merge(repo, head, own_excluded):
    """How (if at all) HEAD's work is in an integration branch."""
    c = repo.common
    targets = repo.targets - own_excluded
    if not targets:
        return {"state": "no-target"}
    rc, out, _ = git(c, "for-each-ref", "--contains", head, "--format=%(refname)",
                     "refs/heads", "refs/remotes")
    containing = sorted(set(out.split()) & targets)
    if containing:
        remote = [r for r in containing if r.startswith("refs/remotes/")]
        pick = repo.default if repo.default in containing else (remote or containing)[0]
        rc, out, _ = git(c, "rev-list", "--first-parent", "--ancestry-path", "%s..%s" % (head, pick))
        chain = out.split()
        when = commit_time(c, chain[-1]) if chain else commit_time(c, head)
        return {"state": "merged", "how": "contained", "into": pick, "remote": bool(remote),
                "in": containing, "merged_at": when, "via": chain[-1] if chain else head}

    # Not reachable: maybe rebased, cherry-picked or squashed. Try the closest targets.
    ranked = []
    for t in targets:
        rc, mb, _ = git(c, "merge-base", t, head)
        mb = mb.strip()
        if rc == 0 and mb:
            ranked.append((commit_time(c, mb) or 0, t, mb))
    ranked.sort(reverse=True)
    for _, t, mb in ranked[:3]:
        rc, out, _ = git(c, "log", "--no-merges", "--format=%ct", "%s..%s" % (mb, head))
        times = [int(x) for x in out.split() if x.isdigit()]
        since = (min(times) - SQUASH_WINDOW) if times else None
        target_ids = patch_ids(c, "%s..%s" % (mb, t), since)
        if not target_ids:
            continue
        own_ids = patch_ids(c, "%s..%s" % (mb, head))
        hit = None
        if own_ids and set(own_ids) <= set(target_ids):
            hit = ("rebased", max((target_ids[p][1] or 0) for p in own_ids), None)
        else:
            rc, diff, _ = git(c, "diff", mb, head)
            rc, pid, _ = run(["git", "--git-dir", c, "patch-id", "--stable"], input=diff)
            pid = pid.split(" ")[0] if pid.strip() else None
            if pid and pid in target_ids:
                hit = ("squashed", target_ids[pid][1], target_ids[pid][0])
        if hit:
            return {"state": "merged", "how": hit[0], "into": t, "remote": t.startswith("refs/remotes/"),
                    "in": [t], "merged_at": hit[1], "via": hit[2]}
    for _, t, mb in ranked[:3]:
        rc, files, _ = git(c, "diff", "--name-only", mb, head)
        files = files.split()
        if files and git(c, "diff", "--quiet", head, t, "--", *files)[0] == 0:
            return {"state": "merged", "how": "content-in-target", "into": t,
                    "remote": t.startswith("refs/remotes/"), "in": [t], "merged_at": None, "via": None}
    best = ranked[0] if ranked else None
    ahead = None
    if best:
        rc, out, _ = git(c, "rev-list", "--count", "--no-merges", "%s..%s" % (best[1], head))
        ahead = int(out.strip()) if out.strip().isdigit() else None
    return {"state": "unmerged", "closest": best[1] if best else None, "ahead": ahead}


def du_kb(path):
    rc, out, _ = run(["du", "-sk", path])
    try:
        return int(out.split()[0])
    except (IndexError, ValueError):
        return None


def analyze(repo, wt, procs):
    path = wt["worktree"]
    head = wt.get("HEAD")
    branch = wt["branch"][len("refs/heads/"):] if isinstance(wt.get("branch"), str) else None
    info = {"path": path, "repo": repo.name, "repo_path": repo.main_path, "common_dir": repo.common,
            "branch": branch, "head": head, "detached": "detached" in wt,
            "locked": wt.get("locked") if "locked" in wt else None,
            "blockers": [], "warnings": []}
    if "prunable" in wt or not os.path.isdir(path):
        info.update(state="prunable", removable=True, size_kb=0,
                    note=wt.get("prunable") if isinstance(wt.get("prunable"), str) else "directory is gone")
        return info

    rc, admin, _ = gwt(path, "rev-parse", "--path-format=absolute", "--git-dir")
    admin = admin.strip()
    reflog = read_reflog(admin) if admin else []
    work = [sha for _, sha, m in reflog if sha and m.startswith(WORK_PREFIXES)]
    subjects = own_subjects(repo.common, work)
    try:
        st = os.stat(admin)
        created = int(getattr(st, "st_birthtime", 0)) or None
    except OSError:
        created = None
    if reflog and reflog[0][0] and (not created or reflog[0][0] < created):
        created = reflog[0][0]
    head_time = commit_time(repo.common, head) if head else None
    activity = [t for t, _, _ in reflog if t] + [head_time or 0]
    try:
        activity.append(int(os.stat(os.path.join(admin, "index")).st_mtime))
    except OSError:
        pass
    rc, subj, _ = git(repo.common, "log", "-1", "--format=%s", head) if head else (1, "", "")
    info.update(created=created, last_commit=head_time, last_activity=max(activity) or None,
                commits_made=len(subjects), subjects=subjects[:8], head_subject=subj.strip())

    # Upstream of the branch itself.
    excluded = set()
    if branch:
        excluded.add("refs/heads/" + branch)
        excluded |= {r for r in repo.refs if r.startswith("refs/remotes/") and repo.short(r) == branch}
        rc, out, _ = git(repo.common, "for-each-ref", "--format=%(upstream)%09%(upstream:track)",
                         "refs/heads/" + branch)
        up, _, track = out.strip().partition("\t")
        if up:
            excluded.add(up)
        info["upstream"] = Repo.display(up) if up else None
        info["upstream_track"] = track.strip("[]") or ("in sync" if up else None)

    # Working tree state.
    rc, out, _ = gwt(path, "status", "--porcelain", "--ignored=traditional",
                     "--untracked-files=normal", "--ignore-submodules=none")
    modified = untracked = 0
    env_files = []
    for line in out.splitlines():
        code, name = line[:2], line[3:]
        if code == "??":
            untracked += 1
        elif code == "!!":
            base = os.path.basename(name.rstrip("/"))
            if base.startswith(".env") and not base.endswith((".example", ".sample", ".template")):
                env_files.append(name)
        else:
            modified += 1
    if rc:
        info["blockers"].append("git status failed")
    if modified or untracked:
        info["blockers"].append("dirty: %d changed, %d untracked" % (modified, untracked))
    if env_files:
        info["warnings"].append("ignored env files are deleted with it: " + ", ".join(env_files[:4]))

    subs = []
    rc, out, _ = gwt(path, "submodule", "status", "--recursive")
    for line in out.splitlines():
        if not line or line[0] == "-":
            continue
        parts = line[1:].split()
        if len(parts) < 2:
            continue
        sub = os.path.join(path, parts[1])
        subs.append(parts[1])
        rc2, o2, _ = gwt(sub, "for-each-ref", "--contains", "HEAD", "--count=1", "--format=%(refname)", "refs/remotes")
        if not o2.strip():
            info["blockers"].append("submodule %s: HEAD %s is on no remote branch" % (parts[1], parts[0][:9]))
    info["submodules"] = subs

    if info["locked"] is not None:
        info["blockers"].append("locked" + (": " + info["locked"] if isinstance(info["locked"], str) else ""))
    users = [(p, c) for p, c, cwd in procs if inside(cwd, real(path)) and p != str(os.getpid())]
    if users:
        info["blockers"].append("in use: " + ", ".join("%s(%s)" % (c, p) for p, c in users[:5]))
        info["in_use"] = [{"pid": p, "command": c} for p, c in users]

    m = find_merge(repo, head, excluded) if head else {"state": "unmerged"}
    info["merge"] = m
    if m["state"] == "merged" and not work and m["how"] == "contained":
        state = "no-commits"
    elif m["state"] == "merged":
        state = "merged" if m.get("remote") else "merged-local"
    else:
        state = m["state"]
        if state == "unmerged":
            what = "%s commit(s) not in %s" % (m.get("ahead"), Repo.display(m["closest"])) if m.get("closest") else "no integration branch shares history"
            info["blockers"].append("unmerged: " + what)
        else:
            info["blockers"].append("no integration branch matched the --target patterns")
    if state == "merged-local":
        info["warnings"].append("merged only into local %s, which is not pushed" % Repo.display(m["into"]))
    info["state"] = state
    info["removable"] = state in ("merged", "merged-local", "no-commits") and not info["blockers"]
    return info


# ---------------------------------------------------------------- entry points

def scan(root, depth=4, patterns=None, size=True, only=None, managed_suffix=MANAGED_SUFFIX):
    patterns = patterns or DEFAULT_TARGETS
    procs = processes_by_cwd()
    if only:
        commons = sorted({c for c in (common_dir(p) for p in only) if c})
    else:
        commons = find_repos(root, depth)
    rows = []
    for c in commons:
        repo = Repo(c, patterns)
        for wt in repo.worktrees:
            if wt["main"] or "bare" in wt or "worktree" not in wt:
                continue
            p = wt["worktree"]
            rp = real(p) if os.path.exists(p) else os.path.abspath(p)
            if only:
                if not any(inside(real(o), rp) for o in only):
                    continue
            elif not inside(rp, root):
                continue
            r = analyze(repo, wt, procs)
            r["placement"] = placement(rp, real(repo.main_path), root, managed_suffix)
            # A gone directory is only a stale admin entry: pruning it is always worth it.
            r["recommend"] = "delete" if r["removable"] and (
                r["placement"] != "managed" or r["state"] == "prunable") else "keep"
            rows.append(r)
    if size:
        with cf.ThreadPoolExecutor(8) as ex:
            todo = [r for r in rows if "size_kb" not in r]
            for r, kb in zip(todo, ex.map(lambda r: du_kb(r["path"]), todo)):
                r["size_kb"] = kb
    rows.sort(key=lambda r: (r["recommend"] != "delete", not r["removable"], -(r.get("size_kb") or 0)))
    return rows


def human_size(kb):
    if kb is None:
        return "?"
    for unit in ("K", "M", "G"):
        if kb < 1024 or unit == "G":
            return ("%.1f%s" % (kb, unit)) if unit == "G" else ("%d%s" % (kb, unit))
        kb /= 1024


def describe_merge(r):
    m = r.get("merge") or {}
    if r["state"] == "prunable":
        return "directory already gone (git worktree prune)"
    if m.get("state") != "merged":
        return "-"
    if r["state"] == "no-commits":
        return "no commits made here; HEAD is already in %s" % Repo.display(m["into"])
    when = iso(m.get("merged_at")) or "date unknown"
    how = {"contained": "merged", "rebased": "rebased/cherry-picked", "squashed": "squash-merged",
           "content-in-target": "every changed file identical"}[m["how"]]
    where = "on remote" if m.get("remote") else "LOCAL ONLY, not pushed"
    return "%s into %s, %s (%s)" % (how, Repo.display(m["into"]), when, where)


def print_row(i, r, short):
    print("\n[%d] %s  (repo %s)  %s  state=%s  placement=%s" % (
        i, short(r["path"]), r["repo"], human_size(r.get("size_kb")), r["state"], r.get("placement")))
    if r["state"] == "prunable":
        print("    " + describe_merge(r))
        return
    ref = r["branch"] or "detached"
    print("    branch %s @%s  upstream: %s %s" % (ref, (r["head"] or "")[:9], r.get("upstream") or "none",
                                                 "(%s)" % r["upstream_track"] if r.get("upstream_track") else ""))
    print("    %s" % describe_merge(r))
    print("    created %s, last commit %s, last activity %s, commits made here: %d" % (
        iso(r.get("created")), iso(r.get("last_commit")), iso(r.get("last_activity")), r.get("commits_made", 0)))
    for s in (r.get("subjects") or [r.get("head_subject")])[:8]:
        print("    - " + (s or ""))
    for w in r["warnings"]:
        print("    ! " + w)


def print_text(rows, root):
    home = os.path.expanduser("~")
    short = lambda p: p.replace(home, "~", 1) if p.startswith(home) else p
    rec = [r for r in rows if r["recommend"] == "delete"]
    managed = [r for r in rows if r["removable"] and r["recommend"] != "delete"]
    kept = [r for r in rows if not r["removable"]]
    total = sum(r.get("size_kb") or 0 for r in rec)
    print("root: %s — %d worktrees: %d recommended for deletion (%s), %d safe but in a managed -wt folder, %d blocked" % (
        short(root), len(rows), len(rec), human_size(total), len(managed), len(kept)))
    n = 0
    if rec:
        print("\n== recommended: delete")
    for r in rec:
        n += 1
        print_row(n, r, short)
    if managed:
        print("\n== safe to delete, recommended: keep (managed folder)")
    for r in managed:
        n += 1
        print_row(n, r, short)
    if kept:
        print("\n== blocked (%d):" % len(kept))
        for r in kept:
            print("  %s  %s  %s  — %s" % (short(r["path"]), r["branch"] or "detached", human_size(r.get("size_kb")),
                                         "; ".join(r["blockers"]) or r["state"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?")
    ap.add_argument("--only", action="append", help="analyze the worktree containing this path")
    ap.add_argument("--depth", type=int, default=4, help="how deep to look for repositories (default 4)")
    ap.add_argument("--target", action="append", help="integration branch pattern (repeatable; replaces the defaults)")
    ap.add_argument("--managed-suffix", default=MANAGED_SUFFIX,
                    help="a worktree whose parent folder ends with this is recommended to keep (default -wt; '' disables)")
    ap.add_argument("--no-size", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.root and not a.only:
        ap.error("give ROOT or --only PATH")
    root = real(a.root) if a.root else "/"
    if a.root and not os.path.isdir(root):
        ap.error("not a directory: %s" % a.root)
    rows = scan(root, a.depth, a.target, not a.no_size, a.only, a.managed_suffix)
    if a.json:
        json.dump({"root": root, "worktrees": rows}, sys.stdout, indent=1, ensure_ascii=False)
        print()
    else:
        print_text(rows, root)


if __name__ == "__main__":
    main()
