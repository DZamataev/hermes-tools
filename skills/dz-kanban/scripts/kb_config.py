#!/usr/bin/env python3
"""dz-kanban configure: boards and the role profiles that work them.

Boards
  kb_config.py boards                                  list (slug, name, counts, archived)
  kb_config.py board-create SLUG [--name N] [--workdir DIR] [--description D]
  kb_config.py board-rename SLUG "NEW NAME"            display name only; the slug is immutable
  kb_config.py board-workdir SLUG DIR                  default workspace for new cards
  kb_config.py board-archive SLUG [--yes]              moves it to boards/_archived/ (recoverable)
  kb_config.py board-delete SLUG [--yes]               export to a .tar.gz, then hard delete

Profiles
  kb_config.py profiles [--board SLUG|--prefix P]      role profiles with model, provider, effort, fallbacks
  kb_config.py providers                               providers configured in the main Hermes config
  kb_config.py profile-create PREFIX [--repo DIR] [--model-impl P:M] [--model-review P:M] [--model-fix P:M]
                                                       the method's three role profiles (kanban-profiles.sh)
  kb_config.py profile-set NAME [--model P:M] [--effort E|inherit] [--fallback P:M ...|--no-fallback]
                              [--worker-fallback wait|allow] [--description TEXT] [--yes]
  kb_config.py profile-delete NAME [--yes]             refused while a card assigned to it is open

Every write prints what it will run and needs --yes; without it the command is a dry run.
A config change binds at the worker's next spawn: a running card keeps the model it started with.
Env: DZ_HERMES (hermes binary), HERMES_HOME.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

HERMES = os.environ.get("DZ_HERMES") or "hermes"
HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
OPEN = ("todo", "ready", "running", "blocked", "review", "scheduled", "triage")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def run(cmd, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env)


def hermes(*args, home=None):
    env = dict(os.environ, HERMES_HOME=home) if home else None
    return run([HERMES, *args], env=env)


def hjson(*args, home=None):
    r = hermes(*args, "--json", home=home)
    if r.returncode:
        raise RuntimeError("hermes %s: %s" % (" ".join(args), (r.stderr or r.stdout).strip()[:300]))
    return json.loads(r.stdout or "null")


def profile_dir(name):
    return os.path.join(HOME, "profiles", name)


def board_db(slug):
    return os.path.join(HOME, "kanban.db") if slug == "default" else os.path.join(HOME, "kanban", "boards", slug, "kanban.db")


def plan(cmds, yes, why=None):
    """Print the commands; run them with --yes. cmds: list of (argv, home or None)."""
    for argv, home in cmds:
        print("  $ %s%s" % ("HERMES_HOME=%s " % home if home else "", " ".join(_q(x) for x in argv)))
    if why:
        print("  " + why)
    if not yes:
        print("dry run; re-run with --yes")
        return 0
    for argv, home in cmds:
        r = run(argv, env=dict(os.environ, HERMES_HOME=home) if home else None)
        if r.returncode:
            print("FAILED (%d): %s" % (r.returncode, (r.stderr or r.stdout).strip()[:400]))
            return 1
    print("done")
    return 0


def _q(s):
    return s if re.fullmatch(r"[\w@%+=:,./-]+", s) else "'" + s.replace("'", "'\\''") + "'"


def open_cards_for(profile):
    hits = []
    for b in hjson("kanban", "boards", "list"):
        p = b.get("db_path") or board_db(b["slug"])
        if not os.path.exists(p):
            continue
        c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        for tid, st, title in c.execute(
                "SELECT id, status, title FROM tasks WHERE assignee = ? AND status IN (%s)" % ",".join("?" * len(OPEN)),
                (profile, *OPEN)):
            hits.append((b["slug"], tid, st, title))
        c.close()
    return hits


# ---------------------------------------------------------------- boards

def cmd_boards(a):
    bs = hjson("kanban", "boards", "list")
    if a.json:
        print(json.dumps(bs, indent=1))
        return 0
    for b in bs:
        counts = ", ".join("%s %d" % kv for kv in sorted((b.get("counts") or {}).items())) or "empty"
        print("%-28s %-40s %s%s" % (b["slug"], (b.get("name") or "")[:40], counts, "  [archived]" if b.get("archived") else ""))
    return 0


def cmd_board_create(a):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", a.slug):
        sys.exit("slug must be kebab-case: %s" % a.slug)
    argv = [HERMES, "kanban", "boards", "create", a.slug]
    for flag, v in (("--name", a.name), ("--description", a.description), ("--default-workdir", a.workdir)):
        if v:
            argv += [flag, v]
    return plan([(argv, None)], a.yes)


def cmd_board_rename(a):
    return plan([([HERMES, "kanban", "boards", "rename", a.slug, a.name], None)], a.yes,
                "the slug stays %s: .kanban/config.env and scripts keep working" % a.slug)


def cmd_board_workdir(a):
    return plan([([HERMES, "kanban", "boards", "set-default-workdir", a.slug, a.dir], None)], a.yes)


def _board_guard(slug):
    if slug == "default":
        sys.exit("the default board cannot be removed")
    p = board_db(slug)
    if not os.path.exists(p):
        sys.exit("no board %s" % slug)
    c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
    running = c.execute("SELECT id, title FROM tasks WHERE status = 'running'").fetchall()
    open_n = c.execute("SELECT count(*) FROM tasks WHERE status IN (%s)" % ",".join("?" * len(OPEN)), OPEN).fetchone()[0]
    c.close()
    if running:
        sys.exit("refused: %d card(s) running on %s (%s). Block them or wait." % (
            len(running), slug, ", ".join(t for t, _ in running[:5])))
    return open_n


def cmd_board_archive(a):
    n = _board_guard(a.slug)
    note = "%d open card(s) stop being dispatched" % n if n else "no open cards"
    return plan([([HERMES, "kanban", "boards", "rm", a.slug], None)], a.yes,
                "archived boards live in kanban/boards/_archived/ and can be moved back; %s" % note)


def cmd_board_delete(a):
    n = _board_guard(a.slug)
    out = os.path.join(HOME, "backups", "kanban", "%s-%s.tar.gz" % (a.slug, time.strftime("%Y%m%d-%H%M%S")))
    os.makedirs(os.path.dirname(out), exist_ok=True) if a.yes else None
    return plan([([HERMES, "kanban", "boards", "export", a.slug, "-o", out], None),
                 ([HERMES, "kanban", "boards", "rm", a.slug, "--delete"], None)], a.yes,
                "export first (restore: hermes kanban boards import %s); %d open card(s) lost from dispatch" % (out, n))


# ---------------------------------------------------------------- profiles

def hermes_python():
    """The interpreter Hermes runs on (it has PyYAML); None if not found."""
    for c in (os.environ.get("DZ_HERMES_PYTHON"), os.path.join(HOME, "hermes-agent", "venv", "bin", "python")):
        if c and os.path.exists(c):
            return c
    return None


_READ = r'''
import json, sys, yaml
out = {}
for path in sys.argv[1:]:
    try:
        c = yaml.safe_load(open(path)) or {}
    except Exception as e:
        out[path] = {"error": str(e)}; continue
    m = c.get("model") or {}
    if not isinstance(m, dict): m = {"default": m}
    out[path] = {"model": m.get("default"), "provider": m.get("provider"),
                 "effort": (c.get("agent") or {}).get("reasoning_effort"),
                 "fallbacks": c.get("fallback_providers") or [],
                 "worker_fallback": (c.get("kanban") or {}).get("worker_fallback")}
print(json.dumps(out))
'''


def read_configs(names):
    py = hermes_python()
    paths = [os.path.join(profile_dir(n), "config.yaml") for n in names]
    if py and paths:
        r = run([py, "-c", _READ, *paths])
        if r.returncode == 0:
            data = json.loads(r.stdout)
            return {n: data.get(p, {}) for n, p in zip(names, paths)}
    return {n: _info_via_cli(n) for n in names}


def _info_via_cli(name):
    home = profile_dir(name)

    def get(key):
        r = hermes("config", "get", key, "--json", home=home)
        try:
            return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
        except ValueError:
            return r.stdout.strip() or None
    model = get("model") or {}
    if not isinstance(model, dict):
        model = {"default": model}
    k = get("kanban")
    return {"model": model.get("default"), "provider": model.get("provider"),
            "effort": get("agent.reasoning_effort"), "fallbacks": get("fallback_providers") or [],
            "worker_fallback": k.get("worker_fallback") if isinstance(k, dict) else None}


def profile_info(name, cfg=None):
    i = dict(cfg if cfg is not None else read_configs([name])[name])
    i["profile"] = name
    return i


def role_profiles(a):
    names = sorted(d for d in os.listdir(os.path.join(HOME, "profiles")) if os.path.isdir(profile_dir(d))) \
        if os.path.isdir(os.path.join(HOME, "profiles")) else []
    if a.prefix:
        return [n for n in names if n.startswith(a.prefix)]
    if a.board:
        p = board_db(a.board)
        c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
        used = {r[0] for r in c.execute("SELECT DISTINCT assignee FROM tasks WHERE assignee IS NOT NULL")}
        c.close()
        return [n for n in names if n in used]
    return names


def cmd_profiles(a):
    names = role_profiles(a)
    cfgs = read_configs(names)
    rows = [profile_info(n, cfgs[n]) for n in names]
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    for r in rows:
        fb = " → ".join("%s:%s" % (f.get("provider"), f.get("model")) for f in r["fallbacks"] if isinstance(f, dict)) or "none"
        print("%-18s %s:%s  effort=%s  worker_fallback=%s\n%18s fallbacks: %s" % (
            r["profile"], r["provider"], r["model"], r["effort"] or "-", r["worker_fallback"] or "allow", "", fb))
    fams = {}
    for r in rows:
        role = next((x for x in ("impl", "review", "fix") if r["profile"].endswith(x)), None)
        if role:
            fams.setdefault(role, set()).add(family(r["provider"], r["model"]))
            if role in ("impl", "fix") and (r["worker_fallback"] or "allow") != "wait":
                print("! %s: worker_fallback is allow — a quota wall finishes the card on the fallback model instead of requeueing (method: wait; profile-set %s --worker-fallback wait)" % (r["profile"], r["profile"]))
    if fams.get("impl") and fams.get("review") and fams["impl"] & fams["review"]:
        print("! reviewer and implementer share a model family (%s): the review is a second pass, not an independent one" % ", ".join(fams["impl"] & fams["review"]))
    return 0


def family(provider, model):
    m = (model or "").lower()
    aliases = {"claude": ("claude", "opus", "sonnet", "haiku", "fable"), "gpt": ("gpt", "o1", "o3", "o4", "codex"),
               "gemini": ("gemini",), "qwen": ("qwen",), "deepseek": ("deepseek",), "grok": ("grok",),
               "llama": ("llama",), "mistral": ("mistral", "codestral")}
    for fam, keys in aliases.items():
        if any(m.startswith(k) or k + "-" in m or "/" + k in m for k in keys):
            return fam
    return provider


def cmd_providers(a):
    prov = hjson("config", "get", "providers") or {}
    if a.json:
        print(json.dumps({k: {x: v.get(x) for x in ("name", "model", "transport", "api_mode")} | {"models": list(v.get("models") or [])}
                          for k, v in prov.items()}, indent=1))
        return 0
    for k, v in prov.items():
        models = list(v.get("models") or [])
        print("%-20s default %-22s %s%s" % (k, v.get("model") or "-", v.get("transport") or v.get("api_mode") or "",
                                             ("  models: " + ", ".join(models[:8])) if models else ""))
    print("built-in providers (openrouter, anthropic, …) work too when their keys are set; `hermes model` lists them")
    return 0


def _pm(spec, flag):
    if ":" not in spec:
        sys.exit("%s wants <provider>:<model>, got %s" % (flag, spec))
    return spec.split(":", 1)


def cmd_profile_create(a):
    kp = None
    for root, dirs, files in os.walk(os.path.join(HOME, "skills")):
        if root.endswith(os.sep + "hermes-kanban-development") and "SKILL.md" in files:
            kp = os.path.join(root, "scripts", "kanban-profiles.sh")
            break
        if root.count(os.sep) - HOME.count(os.sep) > 4:
            dirs[:] = []
    if not kp or not os.path.exists(kp):
        sys.exit("the hermes-kanban-development skill is not installed (dz-kanban setup)")
    argv = ["bash", kp, a.prefix]
    if a.repo:
        argv += ["--repo", a.repo]
    for role in ("impl", "review", "fix"):
        v = getattr(a, "model_" + role)
        if v:
            _pm(v, "--model-" + role)
            argv += ["--model-" + role, v]
    if a.model_impl and a.model_review and family(*_pm(a.model_impl, "")) == family(*_pm(a.model_review, "")):
        print("! impl and review on the same model family: the method wants the reviewer on another one")
    return plan([(argv, None)], a.yes,
                "creates %simpl/%sreview/%sfix (existing ones are kept), role-only MEMORY.md, worker_fallback=wait on impl/fix" % ((a.prefix,) * 3))


def cmd_profile_set(a):
    home = profile_dir(a.name)
    if not os.path.isdir(home):
        sys.exit("no profile %s" % a.name)
    cmds = []
    if a.model:
        prov, model = _pm(a.model, "--model")
        cmds += [([HERMES, "config", "set", "model.provider", prov], home),
                 ([HERMES, "config", "set", "model.default", model], home)]
    if a.effort:
        if a.effort != "inherit" and a.effort not in EFFORTS:
            sys.exit("--effort: one of %s or inherit" % ", ".join(EFFORTS))
        cmds.append(([HERMES, "config", "set", "--force", "agent.reasoning_effort", "" if a.effort == "inherit" else a.effort], home))
    if a.fallback or a.no_fallback:
        fb = [dict(zip(("provider", "model"), _pm(f, "--fallback"))) for f in (a.fallback or [])]
        cmds.append(([HERMES, "config", "set", "fallback_providers", json.dumps(fb)], home))
    if a.worker_fallback:
        cmds.append(([HERMES, "config", "set", "kanban.worker_fallback", a.worker_fallback], home))
    if a.description:
        cmds.append(([HERMES, "profile", "describe", a.name, "--text", a.description, "--overwrite"], None))
    if not cmds:
        sys.exit("nothing to change: pass --model, --effort, --fallback/--no-fallback, --worker-fallback or --description")
    if a.model:
        prov = a.model.split(":", 1)[0]
        known = hjson("config", "get", "providers") or {}
        if prov not in known and not prov.startswith("custom:"):
            print("! %s is not in the main config's providers (%s); fine for a built-in provider, else a typo" % (prov, ", ".join(known)))
    busy = [h for h in open_cards_for(a.name) if h[2] == "running"]
    why = "binds at the next spawn"
    if busy:
        why += "; %d running card(s) keep their current model: %s" % (len(busy), ", ".join("%s/%s" % (b, t) for b, t, _, _ in busy))
    rc = plan(cmds, a.yes, why)
    if a.yes and rc == 0:
        i = profile_info(a.name)
        print("now: %s:%s effort=%s fallbacks=%s worker_fallback=%s" % (
            i["provider"], i["model"], i["effort"] or "-", i["fallbacks"], i["worker_fallback"] or "allow"))
    return rc


def cmd_profile_delete(a):
    if a.name == "default":
        sys.exit("refusing to delete the default profile")
    if not os.path.isdir(profile_dir(a.name)):
        sys.exit("no profile %s" % a.name)
    hits = open_cards_for(a.name)
    if hits:
        sys.exit("refused: %d open card(s) are assigned to %s: %s — reassign or finish them first" % (
            len(hits), a.name, ", ".join("%s/%s[%s]" % (b, t, s) for b, t, s, _ in hits[:6])))
    out = os.path.join(HOME, "backups", "profiles", "%s-%s.tar.gz" % (a.name, time.strftime("%Y%m%d-%H%M%S")))
    if a.yes:
        os.makedirs(os.path.dirname(out), exist_ok=True)
    return plan([([HERMES, "profile", "export", a.name, "-o", out], None),
                 ([HERMES, "profile", "delete", a.name, "--yes"], None)], a.yes,
                "exported first (restore: hermes profile import %s)" % out)


def main(argv=None):
    if (argv if argv is not None else sys.argv[1:]) == ["--help-full"]:
        print(__doc__.strip())
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("boards"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("board-create"); s.add_argument("slug"); s.add_argument("--name"); s.add_argument("--workdir")
    s.add_argument("--description"); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("board-rename"); s.add_argument("slug"); s.add_argument("name"); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("board-workdir"); s.add_argument("slug"); s.add_argument("dir"); s.add_argument("--yes", action="store_true")
    for n in ("board-archive", "board-delete"):
        s = sub.add_parser(n); s.add_argument("slug"); s.add_argument("--yes", action="store_true")
    s = sub.add_parser("profiles"); s.add_argument("--board"); s.add_argument("--prefix"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("providers"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("profile-create"); s.add_argument("prefix"); s.add_argument("--repo")
    for r in ("impl", "review", "fix"):
        s.add_argument("--model-" + r, dest="model_" + r)
    s.add_argument("--yes", action="store_true")
    s = sub.add_parser("profile-set"); s.add_argument("name"); s.add_argument("--model"); s.add_argument("--effort")
    s.add_argument("--fallback", action="append"); s.add_argument("--no-fallback", action="store_true")
    s.add_argument("--worker-fallback", choices=("wait", "allow")); s.add_argument("--description")
    s.add_argument("--yes", action="store_true")
    s = sub.add_parser("profile-delete"); s.add_argument("name"); s.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    fn = {"boards": cmd_boards, "board-create": cmd_board_create, "board-rename": cmd_board_rename,
          "board-workdir": cmd_board_workdir, "board-archive": cmd_board_archive, "board-delete": cmd_board_delete,
          "profiles": cmd_profiles, "providers": cmd_providers, "profile-create": cmd_profile_create,
          "profile-set": cmd_profile_set, "profile-delete": cmd_profile_delete}[a.cmd]
    try:
        return fn(a)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
