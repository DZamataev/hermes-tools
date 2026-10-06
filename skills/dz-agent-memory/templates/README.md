# agent-memory

Read-only mirror of my coding agents' memory and skills. Filled by `sync.sh`
(launchd, every __INTERVAL_H__ h): rsync from the agents' homes → gitleaks → commit → push.
Set up and checked by the `dz-agent-memory` skill (`setup`, `status`, `review`).

**One-way.** Edits made here are overwritten on the next sync; edit in the source.

| Folder | Source |
|---|---|
| `hermes/memories/` | `~/.hermes/memories/` (MEMORY.md, USER.md) |
| `hermes/SOUL.md` | `~/.hermes/SOUL.md` |
| `hermes/skills/` | `~/.hermes/skills/` without hub/curator caches |
| `hermes/profiles/<p>/` | memories + SOUL.md of each Hermes profile |
| `claude/CLAUDE.md`, `claude/skills/` | `~/.claude/` (real dirs only; symlinked skills → `shared/`) |
| `claude/projects/<project>/` | `~/.claude/projects/*/memory/` (auto-memory) |
| `codex/` | `~/.codex/AGENTS.md`, `skills/` (no `.system`), `rules/`, `memories/` |
| `shared/agents-skills/` | `~/.agents/skills/` (shared by Claude via symlinks) |

A source that does not exist on this machine is skipped.

Run by hand: `./sync.sh` (`--no-push` to commit only).
Logs: `~/Library/Logs/agent-memory-sync.log`.
If gitleaks blocks a commit, a macOS notification appears; inspect with
`git add -A && gitleaks git --staged --redact -v -c .gitleaks.toml .`, then `git reset`.

Open this folder in Obsidian as a vault; the Obsidian Git plugin shows history.
