#!/usr/bin/env python3
"""dz-agent-memory: a git mirror of coding agents' memory and skills.

  am.py setup  REPO [--remote URL] [--interval-hours N] [--push] [--no-schedule] [--force]
  am.py status REPO [--json]
  am.py review REPO [--since 7d|2026-10-01] [--json]
  am.py sync   REPO [--no-push]
  am.py unschedule REPO

setup    creates or adopts REPO: installs sync.sh, README, .gitleaks.toml, .gitignore,
         sets origin, runs a first sync without pushing, schedules it (launchd on macOS,
         a crontab line is printed elsewhere). Re-running updates sync.sh and keeps the
         rest; a replaced sync.sh is kept in .git/am-backups/.
status   is it running: schedule loaded, last sync and its result, unpushed or
         uncommitted state, a stale lock, sync.sh drift from this skill's template.
review   what changed in memory and skills since a date: memory entries added and
         removed per file, skills added/removed/changed per agent, fill of Hermes'
         MEMORY.md/USER.md against their char limits.

Test hooks (env): AM_LAUNCH_AGENTS (plist dir), AM_LAUNCHCTL (launchctl binary),
AM_LOG (log file), AM_HERMES_CONFIG (config.yaml for limits).
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(SKILL, "templates")
LABEL = "com.dzamataev.agent-memory-sync"
HOME = os.path.expanduser("~")
LAUNCH_AGENTS = os.environ.get("AM_LAUNCH_AGENTS") or os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHCTL = os.environ.get("AM_LAUNCHCTL") or "launchctl"
LOG = os.environ.get("AM_LOG") or os.path.join(HOME, "Library", "Logs", "agent-memory-sync.log")
SEP = "\n§\n"


def run(cmd, cwd=None, check=False):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    if check and r.returncode:
        sys.exit("%s failed (%d): %s" % (" ".join(cmd), r.returncode, (r.stderr or r.stdout).strip()))
    return r


def git(repo, *args, check=False):
    return run(["git", "-C", repo, *args], check=check)


def plist_path():
    return os.path.join(LAUNCH_AGENTS, LABEL + ".plist")


def plist(repo, hours):
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%s</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>%s/sync.sh</string></array>
  <key>StartInterval</key><integer>%d</integer>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>HOME</key><string>%s</string></dict>
  <key>StandardOutPath</key><string>%s</string>
  <key>StandardErrorPath</key><string>%s</string>
</dict>
</plist>
""" % (LABEL, repo, int(hours * 3600), HOME, LOG, LOG)


def scheduled_interval():
    try:
        m = re.search(r"<key>StartInterval</key><integer>(\d+)</integer>", open(plist_path()).read())
        return int(m.group(1)) if m else None
    except OSError:
        return None


# ---------------------------------------------------------------- setup

def install_file(src, dst, replace, note):
    """Copy a template; returns 'created' / 'updated' / 'same' / 'kept'."""
    new = open(src).read()
    if os.path.exists(dst):
        old = open(dst).read()
        if old == new:
            return "same"
        if not replace:
            return "kept"
        # Inside .git: a backup in the work tree would be committed by the next sync.
        bdir = os.path.join(os.path.dirname(dst), ".git", "am-backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, "%s.%s" % (os.path.basename(dst), time.strftime("%Y%m%d-%H%M%S")))
        shutil.move(dst, backup)
        note.append("%s replaced; previous copy: %s" % (os.path.basename(dst), backup))
        diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "old", "new", n=1))
        note.append(diff[:3000])
        state = "updated"
    else:
        state = "created"
    with open(dst, "w") as f:
        f.write(new)
    return state


def cmd_setup(a):
    for tool, hint in (("git", "xcode-select --install"), ("rsync", "brew install rsync"),
                       ("gitleaks", "brew install gitleaks")):
        if not shutil.which(tool):
            sys.exit("%s is required — install it first: %s" % (tool, hint))
    repo = os.path.abspath(os.path.expanduser(a.repo))
    note = []
    if not os.path.isdir(os.path.join(repo, ".git")):
        if os.path.exists(repo) and os.listdir(repo):
            sys.exit("%s exists, is not empty and is not a git repository; pick another path" % repo)
        if a.remote and git(os.path.dirname(repo) or ".", "ls-remote", a.remote).stdout.strip():
            run(["git", "clone", "-q", a.remote, repo], check=True)
            note.append("cloned %s" % a.remote)
        else:
            os.makedirs(repo, exist_ok=True)
            git(repo, "init", "-q", check=True)
            note.append("initialised %s" % repo)
    states = {}
    states["sync.sh"] = install_file(os.path.join(TPL, "sync.sh"), os.path.join(repo, "sync.sh"), True, note)
    os.chmod(os.path.join(repo, "sync.sh"), 0o755)
    hours = a.interval_hours
    readme_src = open(os.path.join(TPL, "README.md")).read().replace("__INTERVAL_H__", ("%g" % hours))
    readme = os.path.join(repo, "README.md")
    if not os.path.exists(readme) or a.force:
        open(readme, "w").write(readme_src)
        states["README.md"] = "written"
    else:
        states["README.md"] = "kept"
    states[".gitleaks.toml"] = install_file(os.path.join(TPL, "gitleaks.toml"), os.path.join(repo, ".gitleaks.toml"), a.force, note)
    states[".gitignore"] = install_file(os.path.join(TPL, "gitignore"), os.path.join(repo, ".gitignore"), a.force, note)

    if a.remote:
        cur = git(repo, "remote", "get-url", "origin").stdout.strip()
        if not cur:
            git(repo, "remote", "add", "origin", a.remote, check=True)
        elif cur != a.remote:
            sys.exit("origin is %s, not %s; change it by hand if that is intended" % (cur, a.remote))
        r = git(repo, "ls-remote", "origin")
        if r.returncode:
            sys.exit("cannot reach %s: %s" % (a.remote, r.stderr.strip()))
        note.append("origin reachable")

    r = run(["bash", os.path.join(repo, "sync.sh"), "--no-push"], cwd=repo)
    print((r.stdout + r.stderr).strip())
    if r.returncode:
        print("first sync FAILED; nothing scheduled")
        for n in note:
            print(n)
        return 1
    if a.push and git(repo, "remote", "get-url", "origin").stdout.strip():
        br = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        git(repo, "push", "-q", "-u", "origin", br, check=True)
        note.append("pushed %s" % br)

    if not a.no_schedule:
        if sys.platform == "darwin" or os.environ.get("AM_LAUNCH_AGENTS"):
            os.makedirs(LAUNCH_AGENTS, exist_ok=True)
            p = plist_path()
            body = plist(repo, hours)
            if not os.path.exists(p) or open(p).read() != body:
                run([LAUNCHCTL, "bootout", "gui/%d/%s" % (os.getuid(), LABEL)])
                open(p, "w").write(body)
                r = run([LAUNCHCTL, "bootstrap", "gui/%d" % os.getuid(), p])
                if r.returncode:
                    sys.exit("launchctl bootstrap failed: %s" % r.stderr.strip())
                note.append("scheduled every %gh: %s" % (hours, p))
            else:
                note.append("schedule unchanged: %s" % p)
        else:
            note.append("add to crontab (crontab -e):\n0 */%d * * * /bin/bash %s/sync.sh >> %s 2>&1"
                        % (max(1, int(hours)), repo, LOG))
    for k, v in states.items():
        print("%-15s %s" % (k, v))
    for n in note:
        print(n)
    return 0


# ---------------------------------------------------------------- status

def last_log_lines(n=200):
    try:
        with open(LOG, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def cmd_status(a):
    repo = os.path.abspath(os.path.expanduser(a.repo))
    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.exit("%s is not set up (no .git); run setup" % repo)
    st = {"repo": repo, "problems": []}
    interval = scheduled_interval()
    st["schedule"] = {"plist": plist_path() if interval else None, "interval_s": interval}
    if sys.platform == "darwin" or os.environ.get("AM_LAUNCH_AGENTS"):
        r = run([LAUNCHCTL, "list", LABEL])
        st["schedule"]["loaded"] = r.returncode == 0
        m = re.search(r'"LastExitStatus" = (\d+)', r.stdout)
        st["schedule"]["last_exit"] = int(m.group(1)) if m else None
        if not interval:
            st["problems"].append("no launchd plist — sync is not scheduled (run setup)")
        elif r.returncode:
            st["problems"].append("plist exists but the job is not loaded (run setup)")
        elif st["schedule"]["last_exit"]:
            st["problems"].append("last scheduled run exited %d" % st["schedule"]["last_exit"])
    log = last_log_lines()
    st["log_tail"] = log[-5:]
    if any("SECRET FOUND" in l for l in log[-3:]):
        st["problems"].append("gitleaks blocked the latest sync — see the README for how to inspect")
    lc = git(repo, "log", "-1", "--format=%ct %s").stdout.strip()
    if lc:
        ts, _, subj = lc.partition(" ")
        age = time.time() - int(ts)
        st["last_commit"] = {"at": dt.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M"),
                             "age_h": round(age / 3600, 1), "subject": subj}
    else:
        st["last_commit"] = None
    up = git(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    br = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    tracking = up.stdout.strip() if up.returncode == 0 else (
        "origin/" + br if git(repo, "rev-parse", "-q", "--verify", "refs/remotes/origin/" + br).returncode == 0 else None)
    if tracking:
        ahead = git(repo, "rev-list", "--count", "%s..HEAD" % tracking).stdout.strip()
        st["unpushed"] = int(ahead or 0)
        if st["unpushed"]:
            st["problems"].append("%d commit(s) not pushed (network or auth? run sync and read the error)" % st["unpushed"])
    else:
        st["unpushed"] = None
        if git(repo, "remote").stdout.strip():
            st["problems"].append("nothing pushed to origin/%s yet (sync pushes on the next change)" % br)
        else:
            st["problems"].append("no remote — the mirror lives only on this disk")
    dirty = git(repo, "status", "--porcelain").stdout.splitlines()
    st["uncommitted"] = len(dirty)
    lock = os.path.join(repo, ".git", "sync.lock")
    if os.path.isdir(lock) and time.time() - os.stat(lock).st_mtime > 3600:
        st["problems"].append("stale lock %s blocks every sync (older than 1 h): rmdir it" % lock)
    installed = os.path.join(repo, "sync.sh")
    try:
        st["sync_sh"] = "current" if open(installed).read() == open(os.path.join(TPL, "sync.sh")).read() else "differs from the skill template"
    except OSError:
        st["sync_sh"] = "missing"
    if st["sync_sh"] != "current":
        st["problems"].append("sync.sh %s — setup updates it (old copy kept)" % st["sync_sh"])
    if interval and st.get("last_commit") and st["last_commit"]["age_h"] * 3600 > 3 * interval and not st["uncommitted"]:
        # Not a problem by itself: no change means no commit. Only report it.
        st["note"] = "no commit for %.0f h — fine if nothing changed; the log shows 'no changes' runs" % st["last_commit"]["age_h"]
    if a.json:
        print(json.dumps(st, indent=1, ensure_ascii=False))
        return 0
    print("repo: %s" % repo)
    s = st["schedule"]
    print("schedule: %s" % ("every %gh, %s, last exit %s" % (s["interval_s"] / 3600, "loaded" if s.get("loaded") else "NOT loaded", s.get("last_exit")) if s["interval_s"] else "none"))
    lc = st["last_commit"]
    print("last commit: %s" % ("%s (%.1f h ago) %s" % (lc["at"], lc["age_h"], lc["subject"]) if lc else "none"))
    print("unpushed: %s   uncommitted in repo: %d   sync.sh: %s" % (st["unpushed"], st["uncommitted"], st["sync_sh"]))
    for l in st["log_tail"]:
        print("  log: " + l)
    if st.get("note"):
        print("note: " + st["note"])
    print("problems: none" if not st["problems"] else "problems:")
    for p in st["problems"]:
        print("  - " + p)
    return 1 if st["problems"] else 0


# ---------------------------------------------------------------- review

def since_rev(repo, since):
    m = re.fullmatch(r"(\d+)([dhw])", since)
    if m:
        secs = int(m.group(1)) * {"d": 86400, "h": 3600, "w": 604800}[m.group(2)]
        when = dt.datetime.fromtimestamp(time.time() - secs).strftime("%Y-%m-%d %H:%M")
    else:
        when = since
    rev = git(repo, "rev-list", "-1", "--before=" + when, "HEAD").stdout.strip()
    return rev or None, when


def show(repo, rev, path):
    if rev is None:
        return ""
    r = git(repo, "show", "%s:%s" % (rev, path))
    return r.stdout if r.returncode == 0 else ""


def entries(text):
    return [e.strip() for e in text.split(SEP) if e.strip()] if text.strip() else []


def hermes_limits():
    path = os.environ.get("AM_HERMES_CONFIG") or os.path.join(HOME, ".hermes", "config.yaml")
    lim = {"MEMORY.md": 2200, "USER.md": 1375}
    try:
        t = open(path).read()
        for key, f in (("memory_char_limit", "MEMORY.md"), ("user_char_limit", "USER.md")):
            m = re.search(r"^\s*%s:\s*(\d+)" % key, t, re.M)
            if m:
                lim[f] = int(m.group(1))
    except OSError:
        pass
    return lim


AGENT_OF = {"hermes": "Hermes", "claude": "Claude Code", "codex": "Codex", "shared": "shared (~/.agents)"}


def skill_name(path):
    parts = path.split("/")
    if parts[-1] != "SKILL.md" or len(parts) < 3:
        return None
    return parts[0], "/".join(parts[2:-1]) if parts[1] == "skills" else "/".join(parts[1:-1])


def cmd_review(a):
    repo = os.path.abspath(os.path.expanduser(a.repo))
    rev, when = since_rev(repo, a.since)
    out = {"since": when, "base": rev, "commits": 0, "memory": [], "skills": {}, "fill": []}
    rng = ("%s..HEAD" % rev) if rev else "HEAD"
    out["commits"] = int(git(repo, "rev-list", "--count", rng).stdout.strip() or 0)

    # memory files: Hermes (and profiles), Codex, Claude per-project memory
    changed = git(repo, "diff", "--name-status", rev or "4b825dc642cb6eb9a060e54bf8d69288fbee4904", "HEAD").stdout.splitlines()
    mem_files = sorted({l.split("\t")[-1] for l in changed
                        if re.search(r"(^|/)memories/[^/]+\.md$|^claude/projects/.+\.md$|^claude/CLAUDE\.md$|^codex/AGENTS\.md$|^hermes/SOUL\.md$", l.split("\t")[-1])})
    for f in mem_files:
        old, new = show(repo, rev, f), show(repo, "HEAD", f)
        if f.endswith(("MEMORY.md", "USER.md")) and "/memories/" in f:
            o, n = entries(old), entries(new)
            added = [e for e in n if e not in o]
            removed = [e for e in o if e not in n]
        else:
            ol, nl = old.splitlines(), new.splitlines()
            added = [l for l in nl if l.strip() and l not in ol]
            removed = [l for l in ol if l.strip() and l not in nl]
        if added or removed:
            out["memory"].append({"file": f, "added": added, "removed": removed})

    for l in changed:
        st, path = l.split("\t")[0], l.split("\t")[-1]
        sn = skill_name(path)
        if not sn:
            continue
        agent, name = sn
        kind = {"A": "added", "D": "removed"}.get(st[0], "changed")
        out["skills"].setdefault(AGENT_OF.get(agent, agent), {}).setdefault(kind, []).append(name)
    # supporting files of skills whose SKILL.md did not change
    for l in changed:
        path = l.split("\t")[-1]
        parts = path.split("/")
        if len(parts) > 3 and parts[1] == "skills" and parts[-1] != "SKILL.md":
            agent = AGENT_OF.get(parts[0], parts[0])
            for i in range(3, len(parts)):
                if git(repo, "cat-file", "-e", "HEAD:%s/SKILL.md" % "/".join(parts[:i])).returncode == 0:
                    name = "/".join(parts[2:i])
                    kinds = out["skills"].setdefault(agent, {})
                    if not any(name in v for v in kinds.values()):
                        kinds.setdefault("changed", []).append(name)
                    break

    lim = hermes_limits()
    for f in ("hermes/memories/MEMORY.md", "hermes/memories/USER.md"):
        text = show(repo, "HEAD", f)
        if text:
            used = len(SEP.join(entries(text)))
            cap = lim[os.path.basename(f)]
            out["fill"].append({"file": f, "chars": used, "limit": cap, "pct": round(100 * used / cap)})

    if a.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0
    print("since %s: %d sync commit(s)" % (when, out["commits"]))
    for fl in out["fill"]:
        print("%s: %d/%d chars (%d%%)" % (fl["file"], fl["chars"], fl["limit"], fl["pct"]))
    print("\n== memory")
    if not out["memory"]:
        print("  no changes")
    for m in out["memory"]:
        print("  %s: +%d -%d" % (m["file"], len(m["added"]), len(m["removed"])))
        for e in m["added"]:
            print("    + " + e.replace("\n", " ")[:220])
        for e in m["removed"]:
            print("    - " + e.replace("\n", " ")[:220])
    print("\n== skills")
    if not out["skills"]:
        print("  no changes")
    for agent, kinds in sorted(out["skills"].items()):
        for kind in ("added", "removed", "changed"):
            if kinds.get(kind):
                print("  %s %s (%d): %s" % (agent, kind, len(kinds[kind]), ", ".join(sorted(set(kinds[kind])))))
    return 0


def cmd_sync(a):
    repo = os.path.abspath(os.path.expanduser(a.repo))
    args = ["bash", os.path.join(repo, "sync.sh")] + (["--no-push"] if a.no_push else [])
    r = run(args, cwd=repo)
    print((r.stdout + r.stderr).strip())
    return r.returncode


def cmd_unschedule(a):
    run([LAUNCHCTL, "bootout", "gui/%d/%s" % (os.getuid(), LABEL)])
    p = plist_path()
    if os.path.exists(p):
        os.remove(p)
        print("removed %s; the repository is untouched" % p)
    else:
        print("not scheduled")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("repo")
    s.add_argument("--remote")
    s.add_argument("--interval-hours", type=float, default=2)
    s.add_argument("--push", action="store_true", help="push after the first sync")
    s.add_argument("--no-schedule", action="store_true")
    s.add_argument("--force", action="store_true", help="also overwrite README, .gitleaks.toml, .gitignore")
    s = sub.add_parser("status"); s.add_argument("repo"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("review"); s.add_argument("repo"); s.add_argument("--since", default="7d"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("sync"); s.add_argument("repo"); s.add_argument("--no-push", action="store_true")
    s = sub.add_parser("unschedule"); s.add_argument("repo")
    a = ap.parse_args(argv)
    return {"setup": cmd_setup, "status": cmd_status, "review": cmd_review,
            "sync": cmd_sync, "unschedule": cmd_unschedule}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
