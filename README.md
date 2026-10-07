# Hermes Tools

Companion repository for running [Hermes Agent](https://hermes-agent.nousresearch.com/docs) day to
day on a Mac: things Hermes itself does not ship, built around the
[DZamataev/hermes-agent](https://github.com/DZamataev/hermes-agent) fork (branch `develop`).

| Part | What it is | Why you would want it |
|---|---|---|
| [Skills](#skills) | Hermes skills: Kanban development, session wrap-up, worktree cleanup, memory mirror, fork rollout | repeatable procedures the agent runs the same way every time |
| [Desktop plugins](#desktop-plugins) | `provider-limits`, `comp-count` for Hermes Desktop | provider quotas and a session's models, tokens and cost in the status bar |
| [Hermes WebUI runner](#hermes-webui-runner) | a Dock app and a LaunchAgent for the pinned Hermes WebUI | the web interface on `:8787`, started by a click or at login |
| [GUIDE.md](GUIDE.md) | the whole stack from scratch | the fork, TeamClaude on a VPS, codex-lb, keys, plugins, Kanban |

## Quick start

```bash
git clone https://github.com/DZamataev/hermes-tools ~/dev/hermes-tools
cd ~/dev/hermes-tools
./setup_hermes_tools.sh          # desktop plugins (or: bun run setup)
bash skills/install.sh           # the dz-* skills into ~/.hermes/skills
bash kanban/install.sh           # the Kanban method skill
```

Then quit and reopen Hermes Desktop and turn on the plugins' desktop halves in
**Capabilities → Plugins** (see [Desktop plugins](#desktop-plugins)).

**No Hermes yet?** Let another coding agent set it up: install the rollout skill into it
(`bash skills/install.sh --target claude dz-hermes-rollout`, or `--target codex`) and ask it to
"set up Hermes with dz-hermes-rollout". It installs Hermes (the DZamataev fork, vanilla, or your own
fork prepared for rollout), then offers the plugins, skills, backends and compression settings.
The same steps by hand are in [GUIDE.md](GUIDE.md).

The WebUI submodule is needed only for the [WebUI runner](#hermes-webui-runner): `bun run bootstrap`
(it clones over SSH). To update: `git pull`, then rerun the same commands (`install.sh` needs
`--force` to replace an installed skill; the previous copy goes to `~/.hermes/backups/skills/`).

## Skills

A skill is a `SKILL.md` with its scripts: Hermes loads it when a request matches its description,
or when you name it. Skills install **by copy**, never as a symlink to this checkout, so editing
the repository does not change a running agent until you reinstall.

| Skill | Use it to | Say | Needs |
|---|---|---|---|
| [`hermes-kanban-development`](#hermes-kanban-development) | build a repository with many unattended workers on a Hermes Kanban board | "run this on the board" | the fork's Kanban, `bash`, `python3`, `git` |
| [`dz-kanban`](#dz-kanban) | set up, watch and review that board work | `dz-kanban`, `dz-kanban status` | the fork's Kanban, `python3` |
| [`dz-wrapup`](#dz-wrapup) | close a work session without losing anything | "wrap up", `dz-wrapup close` | `git` |
| [`dz-clean-worktrees`](#dz-clean-worktrees) | find and remove git worktrees whose work is merged | "which worktrees can I delete in ~/dev" | `python3`, `git` |
| [`dz-agent-memory`](#dz-agent-memory) | mirror agent memory and skills into a git repository | `dz-agent-memory setup` | `git`, `rsync`, `gitleaks` |
| [`dz-hermes-rollout`](#dz-hermes-rollout) | install Hermes from scratch, or roll your fork out to the local install | "set up Hermes", "roll out my Hermes" | macOS |

Every `dz-*` skill answers `<name> help` with an explanation in your language and runs nothing.
`skills/install.sh` installs into `${HERMES_HOME:-~/.hermes}/skills/software-development/`
(`--category`, `--hermes-home`, or skill names to narrow it); `--target claude` or `--target codex`
installs into Claude Code or Codex instead. Keep `dz-wrapup` and
`dz-clean-worktrees` in the same category: the first calls the second's scripts.

### hermes-kanban-development

Source: [`kanban/`](kanban/) · install: `bash kanban/install.sh` · details:
[kanban/README.md](kanban/README.md).

The method for work that spans more sessions than one context holds. One foreground session, the
**orchestrator**, plans and lands; every slice of work runs as a chain of cards,
**implement → blind review → fix**, each role in its own Hermes profile, and each card reports back
to the orchestrating session.

- **Pipeline:** map the destination and design forks → tickets with acceptance checks → one task
  file per card → chains on the board → gated landing. Research runs as parallel cards; anything
  needing a human (credentials, a device, taste) becomes a blocked gate card.
- **Scripts** (repo-agnostic, read the repository's `.kanban/config.env`):
  `kanban-init.sh <board-slug>` scaffolds a repository and never overwrites;
  `kanban-profiles.sh <prefix>` creates the three role profiles; `kanban-card.sh` makes one card;
  `kanban-chain.py` makes a linked implement/review/fix triple; `kanban-sync.py` finds projects
  whose role templates miss newer method rules and applies them.
- **Use it** when most of the work can run AFK with written acceptance checks; not for one-session
  fixes or design interviews.

### dz-kanban

Source: [`skills/dz-kanban/`](skills/dz-kanban/SKILL.md).

The front door to the Kanban method: everything around the board except creating cards.

| Command | Does |
|---|---|
| `dz-kanban` | where the board is and what it is doing, then the next step |
| `dz-kanban setup` | read-only plan: Hermes has the fork's Kanban, hermes-tools is current, the installed method skill matches `kanban/`, the project is scaffolded, its profiles and templates are up to date |
| `dz-kanban subscribe` / `unsubscribe` | this session hears every open card of the board, or stops |
| `dz-kanban status [7d]` | counts, open chains, who hears what, and problems: real blocks, stuck heads, silent running cards, crashes, model fallbacks, quota walls |
| `dz-kanban review [7d]` | how the orchestration went, with improvements ranked by value |
| `dz-kanban configure …` | boards (create, rename, archive, delete with a backup) and role profiles (model, provider, effort, fallbacks); every write is a dry run until you confirm |

### dz-wrapup

Source: [`skills/dz-wrapup/`](skills/dz-wrapup/SKILL.md).

Closes a session so nothing it produced is lost, nothing it started keeps running unnoticed, and
the next session starts smarter. It surveys every repository, branch, worktree and process the
session touched, writes a summary and process improvements (each with the ready text to apply),
asks once, then commits, pushes what you chose, stops processes, applies improvements, and offers
the session's worktree for removal last.

| Mode | Does |
|---|---|
| `dz-wrapup` | the run above, with one confirmation |
| `dz-wrapup close` | the work is finished: also lands it by the project's runbook (a rollout skill, `AGENTS.md`, `.kanban/config.env`, or merge → test → push), then detaches the session |
| `dz-wrapup auto` | executes nothing: writes everything as open items, safe unattended |
| `dz-wrapup review` | walks the open items of past wrap-ups and applies the ones you pick |

Every run leaves a report in `~/.hermes/wrapups/`, and `INBOX.md` there lists the reports with
open items.

### dz-clean-worktrees

Source: [`skills/dz-clean-worktrees/`](skills/dz-clean-worktrees/SKILL.md).

Worktrees pile up. Name a folder; the skill lists every worktree inside it in three groups:
**recommended to delete** (merged and lying where worktrees get forgotten, such as `.worktrees/`),
**safe but kept** (merged, in a folder ending `-wt` where you keep them on purpose), and
**blocked** (with the reason). Each row says what the work was, when, how and where it was merged,
whether that is pushed, and its size. You pick by numbers; removal re-checks each one and refuses
on any change. It fetches nothing and never runs `rm -rf`.

Scripts: `wt_scan.py ROOT [--json]` (read-only survey), `wt_remove.py [--yes] PATH…` (dry run
without `--yes`).

### dz-agent-memory

Source: [`skills/dz-agent-memory/`](skills/dz-agent-memory/SKILL.md).

A one-way mirror of what your coding agents learned: Hermes memory, `SOUL.md` and skills, Claude
Code `CLAUDE.md`, skills and auto-memory, Codex `AGENTS.md`, skills and memories go into a separate
git repository on a schedule. Each sync scans for secrets with gitleaks, commits and pushes. Chat
history, `config.yaml`, `.env`, auth files and `state.db` are never copied, and the agents never
read from the mirror. Open it in Obsidian to browse memory and its history.

| Command | Does |
|---|---|
| `dz-agent-memory setup` | path, remote (keep it **private**: memory holds host names and ids), interval; a secret scan first, then the first sync and the schedule (launchd) |
| `dz-agent-memory status` | is the schedule running, last commit, unpushed commits, a sync blocked by gitleaks |
| `dz-agent-memory review [7d]` | what the agents learned: memory fill against its limits, entries added and removed, skills changed |
| `dz-agent-memory sync` | sync now |

### dz-hermes-rollout

Source: [`skills/dz-hermes-rollout/`](skills/dz-hermes-rollout/SKILL.md).

It starts with `state.sh`: which agent harness runs it (Hermes Desktop or CLI, Claude Code,
Codex), what Hermes is installed and from where, whether a fork checkout exists. That picks the mode.

**Initial setup** (nothing installed; run it from Claude Code or Codex) asks you to choose one of:

1. install the DZamataev fork, the way to use these tools without changing Hermes yourself;
2. install vanilla Hermes and put the plugins and skills on top, with what will not work spelled out
   (the TeamClaude route, Kanban, rollout);
3. install your own fork, best forked from DZamataev, and prepare it for rollout: a checkout with
   upstream remotes, its environment, a worktree folder and the rollout config.

Then it offers the plugins, the skills, TeamClaude and codex-lb backends (how many of each), and a
compression threshold for ChatGPT models: through a proxy they otherwise compress at the global
threshold, well before the window is full. When everything installed on a fork, it ends by offering
[`dz-kanban`](#dz-kanban) setup for Kanban development.

**Rollout** is for running Hermes from your own fork: features live in git worktrees, the fork's
integration branch collects them and upstream, and the live install plus Hermes Desktop run that
branch. Every change reaches the live app through three gated steps:

1. `merge.sh [--upstream]` merges every worktree branch ahead of the integration branch (and
   upstream first); a conflict stops it for a hand resolution that keeps both sides.
2. `verify.sh` runs the changed tests, counts only failures upstream does not have, builds the
   desktop app and starts a **test instance** next to the live one, with an isolated home. You
   check it by hand.
3. `deploy.sh [--push]` fast-forwards the install, rebuilds the desktop app, runs your post-deploy
   hook, restarts the gateway and reopens the app. It refuses an unverified commit, and refuses
   while a Hermes window it would close has a turn running.

Paths live in `~/.config/dz-hermes-rollout/config.sh`. Inside Hermes Desktop deploy runs detached,
since the app closes with the session in it. The skill also covers porting an open upstream PR as a
hotfix with a test pinned to your failure.

## Desktop plugins

Each plugin has two halves: a Python backend (FastAPI routes) and a desktop UI.

| Plugin | Shows |
|---|---|
| [`provider-limits`](plugins/provider-limits/README.md) | status-bar chip with each custom provider's remaining 5-hour quota, in `config.yaml` order; the popover adds weekly windows, per-account rows and upstream status. Reads providers and their `key_env` from `config.yaml` and sends each key only to its own provider |
| `comp-count` | the 🧳 chip: the focused session's timeline (models, providers, segments, compactions), tokens and estimated API cost. The chip and the panel share one query, so their counts agree |

### Install and update

```bash
./setup_hermes_tools.sh          # or: bun run setup
```

It installs both plugins into `~/.hermes/plugins/` and enables their backend halves. Then:

1. **Quit and reopen Hermes Desktop** when the script asks (it says so only when a backend half
   changed). Plugin routes are mounted by the `hermes serve` child the app spawns for itself, not
   by the launchd gateway, so `hermes gateway restart` would end your live sessions and still leave
   the routes answering 404. The script restarts nothing on purpose.
2. **Turn on each desktop half** once in **Capabilities → Plugins**. Both halves default to off:
   that is the plugin security boundary (GHSA-mcfc-hp25-cjv7), and the script cannot flip it.

A desktop-only change needs no restart: Electron reconciles its copy of the desktop half (it alone
writes `desktop-plugins/<name>/`), at once when the app is open, at next launch otherwise.

The script installs plugins and nothing else. It once patched Hermes source and repaired provider
settings; the TeamClaude OAuth proxy now ships in the fork, and the "repair" had drifted from the
working configuration.

### Cost estimates in comp-count

The cost line is **list price for the tokens actually used**, at rates from
`openrouter.ai/api/v1/models` (cached on disk, refreshed daily, served stale offline). It answers
"what would this session have billed through a paid API", not what a subscription charges:
TeamClaude and codex-lb bill through Claude and ChatGPT plans.

- Hermes' own rate table (`agent/usage_pricing.py`) is ignored: it is a hand-copied snapshot that
  misprices some models and lacks others.
- All four rates apply (input, output, cache read, cache write). Cache reads dominate long sessions
  (108M against 1.1k input tokens on a real one), so ignoring them would be wrong by orders of
  magnitude.
- A model with no published rate shows "no rate published" and the total is marked `partial`.
- Provider names come from `billing_base_url` matched against the endpoints in
  `~/.hermes/config.yaml` (under both `base_url` and `api`); otherwise several gateways would all
  read `custom`. A row with neither shows no provider rather than a made-up one.
- Model names are normalised for the lookup (`claude-opus-5-5` → `claude-opus-5.5`, a dated
  snapshot → its base model). Spelling only: an unpublished version stays unpriced instead of
  borrowing a neighbour's rate.

## Hermes WebUI runner

[`hermes-webui/`](hermes-webui) is a pinned submodule of
[DZamataev/hermes-webui](https://github.com/DZamataev/hermes-webui); `runner/` starts it in one of
two launch modes. Both bind `0.0.0.0:8787`: **configure Hermes WebUI authentication before
exposing the port** to a LAN or the internet. The two modes exclude each other on the same host
and port; never run `hermes-webui/start.sh` or `ctl.sh start` alongside either.

**Dock app** — started by a click, stopped by quitting it:

```bash
bun run app                      # builds "Hermes WebUI.app" in the repository root
runner/hermes-webui-app.sh status   # also: start, stop
```

Opening the app starts the WebUI and opens `http://127.0.0.1:8787` in the default browser.
The app records the checkout that built it: rebuild after moving the repository; the bundle itself
can move to `/Applications`. Set `HERMES_WEBUI_ICON` when Hermes is not at `~/.hermes`. Quitting
stops only the WebUI it started. After a force quit or a crash, reopening reconnects to the saved
process, and `status`/`stop` recover it from a terminal.

**LaunchAgent** — always on, starts at login, restarts after failures:

```bash
bun run webui:enable             # also: webui:status, webui:restart, webui:disable
```

Override `HERMES_WEBUI_DIR`, `HERMES_WEBUI_HOST`, `HERMES_WEBUI_PORT` or `HERMES_WEBUI_PYTHON` when
the checkout or runtime lives elsewhere. The Dock app never touches the LaunchAgent: disable it
before using the app on the same port.

## Working on this repository

```text
GUIDE.md                  the whole stack from scratch
setup_hermes_tools.sh     installs the desktop plugins
plugins/<name>/           plugin.yaml, dashboard/ (backend), desktop/ (UI), tests/
skills/<name>/            SKILL.md, scripts/, references/, templates/, tests/run.sh
skills/install.sh         copies skills into a Hermes home
kanban/                   the Kanban method skill, its scripts and install.sh
hermes-webui/             WebUI submodule;  runner/  its Dock app and LaunchAgent
scripts/check.mjs         runs every test suite;  scripts/help.mjs  lists the bun scripts
tests/                    repository-level setup and layout checks
docs/superpowers/         design specs and implementation plans
```

```bash
bun run bootstrap          # git submodule update --init --recursive
bun run help               # every script, with examples
bun run check              # every suite, concurrently
bun run check comp-count   # only the suites whose name matches
```

**Not `bun test`.** Every suite here is a shell or Python bench, and Bun's runner collects only
`*.test.*`/`*.spec.*` files, so it would find nothing and report success. Suites are safe to run at
once: each builds its own `mktemp` sandbox and fakes `launchctl`, `curl` and `hermes`, so no board,
profile, cron job, port or launchd state is touched. `plugins/provider-limits/tests/run.sh --e2e`
adds an opt-in pass against the real upstreams.

**Archived branches:** `archive/*` hold abandoned attempts, kept for history only. Never merge them.
