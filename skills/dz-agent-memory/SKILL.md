---
name: dz-agent-memory
description: "Use when setting up, checking or reviewing the git mirror of agent memory and skills (agent-memory, Obsidian vault)."
version: 1.0.0
author: Denis Zamataev
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [memory, skills, git, backup, obsidian, launchd, gitleaks]
    related_skills: [hermes-memory-layer]
---

# Agent memory in git

A **one-way mirror**: a separate git repository receives copies of the coding
agents' memory and skills on a schedule, scans them for secrets, commits and
pushes. The agents' homes are never a git repository and never read from the
mirror. Open the repository in Obsidian to browse it and its history.

| Mirrored | From |
|---|---|
| Hermes memory, `SOUL.md`, skills (no hub/curator caches), each profile's memory | `~/.hermes` |
| Claude Code `CLAUDE.md`, real skill dirs, per-project auto-memory | `~/.claude` |
| Codex `AGENTS.md`, skills, rules, memories | `~/.codex` |
| skills shared through symlinks | `~/.agents/skills` |

A missing source is skipped. Chat history, `config.yaml`, `.env`, auth files
and `state.db` are never mirrored.

Everything goes through `python3 ${HERMES_SKILL_DIR}/scripts/am.py`
(`AM` below). Requires `git`, `rsync`, `gitleaks`.

## Commands

| Ask | Run |
|---|---|
| `dz-agent-memory setup` | see "Setup" |
| `dz-agent-memory status` — is it working? | `AM status <repo>` |
| `dz-agent-memory review [since]` — what did the agents learn? | `AM review <repo> --since 7d` |
| `dz-agent-memory sync` — now | `AM sync <repo>` |
| stop the schedule | `AM unschedule <repo>` (the repository stays) |

`<repo>` is the mirror's path. If the operator did not name it, find it:
`grep -o '<string>[^<]*/sync.sh' ~/Library/LaunchAgents/com.dzamataev.agent-memory-sync.plist`;
else ask. Denis's is `~/dev/agent-memory` → `git@gitlab.com:dzamataev/agent-memory.git`.

## Setup

1. Ask the three inputs in one question: repository path (default
   `~/dev/agent-memory`), remote URL (a **private** repository — memory holds
   host names, IPs and chat ids even without credentials; empty = local only),
   interval in hours (default 2).
2. With a remote: `git ls-remote <url>` must succeed before anything else.
   An empty new remote is fine.
3. Dry look at what would be committed — sizes, and a secret scan of the
   sources: `gitleaks dir --redact ~/.hermes/memories ~/.hermes/skills ~/.claude ~/.codex ~/.agents/skills`
   (whichever exist). Triage each finding with the operator: a placeholder in
   a skill example goes to the allowlist in `.gitleaks.toml` as an exact
   regex with `regexTarget = "secret"`; a real secret is removed at its
   source. The scheduled sync refuses to commit while one is present.
4. `AM setup <repo> --remote <url> --interval-hours <h> --push`. It creates
   or adopts the repository, installs `sync.sh`/README/`.gitleaks.toml`/
   `.gitignore`, runs the first sync, pushes, and schedules it (launchd on
   macOS; elsewhere it prints the crontab line). Re-running is safe: it
   updates `sync.sh` from this skill (the previous copy is kept in
   `.git/am-backups/` and the diff is printed), leaves README and
   gitleaks/gitignore alone unless `--force`, and touches launchd only if
   the schedule changed.
5. `AM status <repo>` must end with `problems: none`.
6. Tell the operator: open `<repo>` in Obsidian as a vault (Obsidian Git
   plugin for history); the mirror is read-only — edits there are
   overwritten, edit memory and skills at the source.

Adding a source = editing `templates/sync.sh` in this skill and the table in
`templates/README.md`, then `setup` again. Never point it at a directory with
credentials or chat history.

## Status

`AM status <repo>` (`--json`): schedule loaded and its last exit code, last
commit and its age, unpushed commits, the last log lines, a stale
`.git/sync.lock`, `sync.sh` drift from the template, a sync blocked by
gitleaks. Exit 1 when there is a problem. Each problem line says what to do.
No commit for hours is not a problem by itself: no change, no commit — the
log shows `no changes` runs.

## Review

`AM review <repo> --since 7d` (or a date; `--json` for processing). Shows:

- fill of Hermes `MEMORY.md`/`USER.md` against `memory_char_limit` /
  `user_char_limit` from `~/.hermes/config.yaml`;
- memory entries added and removed per file (Hermes entries are compared
  whole, so an edited entry shows as one removed + one added);
- skills added, removed and changed per agent.

Then, as the reviewer, add what the script cannot judge — and propose, do
not apply:

- **Near the limit** (≥ 90 %): the next `memory add` fails or pushes out a
  fact. Propose which entries move into a skill (project or host facts) and
  which merge.
- **Entries that belong in a skill**: a procedure, a project path, a tool
  quirk. Name the target skill.
- **Contradictions and duplicates**: a new entry that contradicts an old one
  or repeats a skill's rule.
- **A memory entry removed by an agent** that the operator may want back —
  quote it.
- **Skills changed many times** in the period: churn worth a look.

Report in the operator's language. Apply changes only after the operator
picks them, through the agent's own memory and skill tools, never by editing
the mirror.

## Tests

`bash ${HERMES_SKILL_DIR}/tests/run.sh` runs setup, sync, status and review
against a fake `$HOME`, a bare remote and a fake launchctl: the scope
(caches, profile skills and symlinked skills excluded, missing agents
skipped), deletions mirrored, idempotent re-setup, a planted token blocked
while the allowlisted placeholder passes, and each status problem.
