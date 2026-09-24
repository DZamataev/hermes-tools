# hermes-kanban-development

A [Hermes Agent](https://hermes-agent.nousresearch.com/docs) skill for building a
project with many unattended Hermes workers on a Kanban board. One foreground
session plans and lands work; each slice runs as **implement → blind review →
fix**, each role in its own Hermes profile, and a monitor-gated cron job
supervises the board overnight.

This directory is the whole package: the method (`SKILL.md`), detailed field
notes (`references/`), templates, and repo-agnostic scripts. It has no
dependencies outside itself.

## Requirements

- Hermes Agent with the Kanban board and cron (`hermes kanban`, `hermes cron`)
- `bash`, `python3` (stdlib only), `git`
- macOS or Linux

## Install

```bash
git clone https://github.com/DZamataev/hermes-tools
bash hermes-tools/kanban/install.sh            # copies into ~/.hermes/skills/software-development/
```

`--hermes-home <dir>` targets another Hermes home or profile; `--force`
replaces an installed copy (the old one moves to `<hermes-home>/backups/skills/`). Only this directory
is needed. Copy or vendor it on its own if you like.

## Use in a repository

```bash
K=~/.hermes/skills/software-development/hermes-kanban-development/scripts
cd <your-repo>
bash $K/kanban-init.sh <board-slug>          # scaffolds .kanban/, role templates, runbook
bash $K/kanban-profiles.sh <prefix>          # <prefix>impl / review / fix profiles
hermes kanban boards create <board-slug>
python3 $K/kanban-chain.py --title "<slice>" --task <task.md> --workdir <abs worktree>
bash $K/kanban-coordinator.sh up             # optional overnight supervisor
```

Then ask the agent to load the skill (`/hermes-kanban-development`) and follow
it. Every script prints its usage when run without arguments.

Per-repo settings live in the repository, not in the skill:

| File | Tracked | Holds |
|---|---|---|
| `.kanban/config.env` | yes | board, profile prefix, gate command, base branch, templates dir |
| `.kanban/notify.env` | **no** (gitignored by init) | delivery chat / thread ids |
| `docs/agents/kanban-templates/*.md` | yes | role preambles, coordinator prompt |
| `docs/hermes_kanban_development.md` | yes | the repo's own runbook |

## Layout

| Path | What |
|---|---|
| `SKILL.md` | the method: map → AFK cards → chains → landing, roles, gates, overnight |
| `references/pitfalls.md` | short rules from real board runs, grouped by area |
| `references/*.md` | monitor-gated supervision, upstream merges, parameter sweeps, baseline audits, per-repo runbook |
| `templates/` | scaffolds copied by `kanban-init.sh`; `card-body.md` is a card skeleton |
| `scripts/` | `kanban-init.sh`, `kanban-profiles.sh`, `kanban-card.sh`, `kanban-chain.py`, `kanban-monitor.py`, `kanban-coordinator.sh` |
| `tests/run.sh` | tests against a fake `hermes`; no board, profile or cron job is touched |

## Test

```bash
bash tests/run.sh
```

`KANBAN_HERMES=<path>` points every script at another `hermes` binary.

## License

MIT, see `LICENSE`.
