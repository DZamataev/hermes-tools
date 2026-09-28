#!/usr/bin/env python3
"""Find the projects that run development on a Hermes Kanban board and bring their role templates up to the
method in this skill.

  kanban-sync.py [--root DIR] [--depth N] [--json]         report (default root: ~/dev)
  kanban-sync.py --apply PROJECT [--allow-dirty]           insert the missing rule bullets into that checkout

A project is a git checkout holding `.kanban/config.env` (set up by kanban-init.sh), `.kanban/config.json` or
`docs/hermes_kanban_development.md` (its own tooling). The method lives in two places:
  - the scripts (card/chain creation, contracts, subscriptions): a project on this skill's scripts gets them from
    the installed skill (install.sh --force); a project with its own card script does not, and the report says so;
  - the role templates copied into the project once by kanban-init.sh: never refreshed on their own, so every
    rule in templates/rules.tsv is checked in the project's copy.
--apply inserts a missing bullet verbatim from this skill's template, after the bullet that precedes it there
(matched by first line; else after the nearest earlier rule bullet, else before `## Task`). It never commits; a
file with uncommitted changes is skipped unless --allow-dirty. Rules whose bullet carries a {{PLACEHOLDER}}, and
`absent` rules, are reported for a manual edit, never rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ROLES_DIR = SKILL_DIR / "templates" / "roles"
RULES_FILE = SKILL_DIR / "templates" / "rules.tsv"
SKIP_DIRS = {"node_modules", ".git", "build", "dist", ".venv", "venv", "Pods", "DerivedData", ".gradle"}
MARKERS = (".kanban/config.env", ".kanban/config.json", "docs/hermes_kanban_development.md")
TEMPLATE_DIRS = ("docs/agents/kanban-templates", "docs/agents/kanban")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def load_rules() -> list[dict]:
    rules = []
    for line in RULES_FILE.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        rid, roles, want, marker = line.split("\t", 3)
        rules.append({"id": rid, "roles": roles.split(","), "want": want, "marker": marker})
    return rules


def _git(path: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def find_projects(root: Path, depth: int) -> list[Path]:
    found: set[Path] = set()
    root = root.expanduser().resolve()
    for dirpath, dirnames, _files in os.walk(root):
        rel_depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and rel_depth < depth]
        here = Path(dirpath)
        if any((here / m).is_file() for m in MARKERS):
            top = _git(here, "rev-parse", "--show-toplevel")
            if top and Path(top).resolve() == here.resolve():
                found.add(here.resolve())
    return sorted(found)


def _config_env(project: Path) -> dict:
    cfg = {}
    path = project / ".kanban" / "config.env"
    for line in path.read_text().splitlines():
        m = re.match(r'\s*([A-Z_]+)=(.*)$', line)
        if m:
            cfg[m.group(1)] = m.group(2).strip().strip('"')
    return cfg


def describe(project: Path) -> dict:
    info: dict = {"path": str(project), "branch": _git(project, "branch", "--show-current")}
    common = _git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    primary = str(Path(common).parent) if common else str(project)
    info["worktree_of"] = None if Path(primary).resolve() == project else primary
    if (project / ".kanban" / "config.env").is_file():
        cfg = _config_env(project)
        info["kind"] = "skill"
        info["board"] = cfg.get("KANBAN_BOARD", "")
        templates = cfg.get("KANBAN_TEMPLATES") or TEMPLATE_DIRS[0]
    elif (project / ".kanban" / "config.json").is_file():
        cfg = json.loads((project / ".kanban" / "config.json").read_text())
        info["kind"] = "own"
        info["board"] = cfg.get("board", "")
        templates = cfg.get("templates") or TEMPLATE_DIRS[0]
    else:
        info["kind"] = "own"
        info["board"] = ""
        templates = next((d for d in TEMPLATE_DIRS if (project / d).is_dir()), "")
    info["templates"] = templates
    card = project / "scripts" / "kanban-card.sh"
    info["own_card_script"] = bool(card.is_file() and "hermes-kanban-development" not in card.read_text())
    return info


def check(project: Path, info: dict, rules: list[dict]) -> list[dict]:
    """One row per (rule, role) whose template exists in the project and breaks the rule."""
    gaps = []
    tdir = project / info["templates"] if info["templates"] else None
    for rule in rules:
        for role in rule["roles"]:
            path = tdir / f"{role}.md" if tdir else None
            if path is None or not path.is_file():
                continue
            has = _norm(rule["marker"]) in _norm(path.read_text())
            if has != (rule["want"] == "present"):
                gaps.append({"rule": rule["id"], "role": role, "want": rule["want"], "file": str(path)})
    return gaps


def _bullets(text: str) -> list[tuple[int, int]]:
    """(start, end) character spans of top-level `- ` bullets, each running to the next bullet, blank line or
    heading."""
    spans, lines, pos, start = [], text.splitlines(keepends=True), 0, None
    for line in lines:
        is_bullet = line.startswith("- ")
        ends = is_bullet or not line.strip() or line.startswith("#") or line.startswith("<!--")
        if start is not None and ends:
            spans.append((start, pos))
            start = None
        if is_bullet:
            start = pos
        pos += len(line)
    if start is not None:
        spans.append((start, pos))
    return spans


def _bullet_with(text: str, marker: str) -> tuple[int, int] | None:
    for s, e in _bullets(text):
        if _norm(marker) in _norm(text[s:e]):
            return s, e
    return None


def apply(project: Path, info: dict, rules: list[dict], allow_dirty: bool) -> list[str]:
    report = []
    by_id = {r["id"]: r for r in rules}
    order = [r["id"] for r in rules]
    for gap in check(project, info, rules):
        rule, role, path = by_id[gap["rule"]], gap["role"], Path(gap["file"])
        rel = path.relative_to(project)
        if rule["want"] == "absent":
            report.append(f"manual   {rel}: remove the text that tells this role `{rule['marker']}` ({rule['id']})")
            continue
        src = (ROLES_DIR / f"{role}.md").read_text()
        span = _bullet_with(src, rule["marker"])
        bullet = src[span[0]:span[1]] if span else ""
        if not bullet or "{{" in bullet:
            report.append(f"manual   {rel}: add the `{rule['id']}` bullet from templates/roles/{role}.md")
            continue
        if not allow_dirty and _git(project, "status", "--porcelain", "--", str(rel)):
            report.append(f"skipped  {rel}: uncommitted changes (commit them, or pass --allow-dirty)")
            continue
        text = path.read_text()
        # Anchor: the bullet that precedes this one in the skill's template, matched by its first line (projects
        # keep those verbatim far more often than whole bullets); else the nearest earlier rule bullet this file
        # has; else the last bullet before `## Task`.
        at = None
        src_spans = _bullets(src)
        idx = next(i for i, sp in enumerate(src_spans) if sp == span)
        for s, e in reversed(src_spans[:idx]):
            first = src[s:e].splitlines()[0]
            if "{{" in first:
                continue
            found = _bullet_with(text, first)
            if found:
                at = found[1]
                break
        for prev in ([] if at is not None else reversed(order[: order.index(rule["id"])])):
            p = by_id[prev]
            if role in p["roles"] and p["want"] == "present":
                found = _bullet_with(text, p["marker"])
                if found:
                    at = found[1]
                    break
        if at is None:
            m = re.search(r"^## Task", text, re.M)
            if m is None:
                report.append(f"manual   {rel}: no anchor and no `## Task` heading for the `{rule['id']}` bullet")
                continue
            before = text[: m.start()].rstrip("\n")
            last = _bullets(before + "\n")
            at = last[-1][1] if last else m.start()
        path.write_text(text[:at] + bullet + text[at:])
        report.append(f"added    {rel}: {rule['id']}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="~/dev")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", metavar="PROJECT")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()
    rules = load_rules()

    if args.apply:
        project = Path(args.apply).expanduser().resolve()
        if not any((project / m).is_file() for m in MARKERS):
            print(f"kanban-sync: {project} is not a kanban project", file=sys.stderr)
            return 2
        for line in apply(project, describe(project), rules, args.allow_dirty):
            print(line)
        return 0

    rows = []
    for project in find_projects(Path(args.root), args.depth):
        info = describe(project)
        info["gaps"] = check(project, info, rules)
        info["dirty_templates"] = bool(info["templates"] and _git(project, "status", "--porcelain", "--",
                                                                  info["templates"]))
        rows.append(info)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        where = f"  (worktree of {r['worktree_of']})" if r["worktree_of"] else ""
        print(f"{r['path']}  [{r['kind']}] board={r['board'] or '-'} branch={r['branch'] or '-'}{where}")
        if not r["templates"]:
            print("  no role templates found")
        else:
            print(f"  templates {r['templates']}" + ("  (uncommitted changes)" if r["dirty_templates"] else ""))
        if r["own_card_script"]:
            print("  own scripts/kanban-card.sh: contracts, subscriptions and chain checks of the skill do not apply")
        for g in r["gaps"]:
            verb = "missing" if g["want"] == "present" else "forbidden"
            print(f"  {verb:9} {g['rule']:28} {g['role']}.md")
        if r["templates"] and not r["gaps"]:
            print("  role templates up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
