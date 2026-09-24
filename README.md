# Hermes Tools

Local macOS tooling for running Hermes WebUI and keeping Hermes Desktop
plugins installed.

## Layout

- `hermes-webui/` — pinned Git submodule for
  `git@github.com:DZamataev/hermes-webui.git`.
- `runner/` — native Dock application and LaunchAgent lifecycle tools.
- `plugins/comp-count/` — unified plugin (Python backend + desktop UI) showing
  the focused session's working timeline: models, providers, segments and
  compactions, plus session-wide token use and estimated API cost, behind the
  status-bar 🧳 chip. The chip and the panel share one query, so the count on
  the chip is the count in the panel.
- `plugins/provider-limits/` — unified plugin (Python backend + desktop UI)
  showing each custom provider's 5-hour quota in the status bar.
- `setup_hermes_tools.sh` — installs both plugins as unified packages under
  `~/.hermes/plugins`.
- `kanban/` — the `hermes-kanban-development` skill: the method for building a
  repository with implement → review → fix workers on a Hermes Kanban board,
  plus repo-agnostic scripts (init, profiles, cards, chains, the change
  detector and the unattended coordinator job). See [Kanban skill](#kanban-skill).
- `scripts/check.mjs` — runs every suite in this repository, concurrently.
- `docs/` — design specifications and implementation plans.
- `tests/` — repository-level integration and lifecycle checks.

## Bootstrap and test

```bash
bun run help               # every script, with examples
bun run bootstrap          # git submodule update --init --recursive
bun run check              # every suite, concurrently
bun run check comp-count   # just the suites whose name matches
```

**Not `bun test`.** Bun's own runner only collects files with `.test`/`.spec` in
the name, so it would collect nothing at all here — every suite is a shell or
Python bench — and report success. `bun run check` is the entry point;
it runs the suites concurrently and prints the full output of whichever failed.

Suites are safe to run at once: each builds its own `mktemp` sandbox and fakes
`launchctl` and `curl`, so they share no port, file or launchd state.

## Setup

```bash
bun run setup              # or ./setup_hermes_tools.sh without bun
```

Installs `comp-count` and `provider-limits` under `~/.hermes` and sets both
backend gates. Idempotent, and it **restarts nothing** — when the backend half
changed it prints an instruction to restart Hermes Desktop instead.

That is not laziness. Plugin routes (`/api/plugins/<name>/`) are mounted by the
`hermes serve --port 0` child that **Hermes.app spawns for itself**, not by the
launchd gateway: its `Mounted plugin API routes` lines land in `gui.log` and
never in `gateway.log`. `hermes gateway restart` therefore restarts an unrelated
process — it ends the user's live sessions and leaves the new plugin unmounted,
its REST calls answering 404. This script used to do exactly that.

A desktop-only edit needs no restart at all: the renderer picks it up through
Electron's reconcile.

The desktop half the renderer loads is a third copy: Electron materializes
`plugins/<name>/desktop/` into `desktop-plugins/<name>/`. Only Electron may
write it, so the script nudges the directory the app watches, and the reconcile
runs itself. With the app closed that is a no-op — it reconciles at next launch.

It installs plugins and nothing else. Patching Hermes source lived here once;
the TeamClaude OAuth proxy now ships in the Hermes fork, so the patch and the
provider-settings repair were removed — those settings had drifted from the
working configuration, and "repairing" them would have broken a live install.

The script cannot enable either **desktop** half: the loader forces a
materialized package off whatever the plugin declares, so flip them on once in
Capabilities → Plugins.

## Cost estimates in comp-count

The panel's cost line is **list price for the tokens actually used**, at rates
fetched from `openrouter.ai/api/v1/models` (cached on disk, refreshed daily,
served stale when the network is down). It answers "what would this session
have billed through a paid API", not what a subscription charges — TeamClaude
and the codex gateways bill through Anthropic and ChatGPT plans, where tokens
never become a per-token invoice.

Hermes carries its own rate table in `agent/usage_pricing.py`, and the plugin
deliberately ignores it: that table is a hand-copied snapshot, it prices
`gpt-5.6-sol` at $5/$30 where OpenRouter currently says $2/$10, and it has no
entry at all for `claude-opus-5`. Cache reads dominate a long session — 108M
against 1.1k input tokens on one real one — so all four rates (input, output,
cache read, cache write) are applied; ignoring cache would be wrong by orders
of magnitude. A model with no published rate reads as "no rate published" and
is excluded from the total, which is then marked `partial` rather than passed
off as the whole bill.

Provider names come from `billing_base_url` matched against the endpoints in
`~/.hermes/config.yaml` — read under **both** `base_url` and `api`, since the
config uses each. Without that, 291 rows of the real store say only `custom`,
and three different gateways hide behind that one word. A row carrying neither
field (real sessions have them — a `vision` call) shows no provider at all
rather than a made-up one, and is still priced from the model's own vendor.

Model names are normalised before the rate lookup: the store writes
`claude-opus-5-5` where OpenRouter publishes `claude-opus-5.5`, and a dated
snapshot (`claude-haiku-4-5-20251001`) prices as its base model. This rewrites
spelling only — it never walks to a neighbouring version. An unpublished 5.5
stays unpriced instead of quietly billing at 5's higher rate.

## Provider limits plugin

`plugins/provider-limits/` is the source of truth for the status-bar quota chip.
Like `comp-count` it has two halves — a FastAPI backend and the desktop UI —
and installs into `~/.hermes/plugins/`.

Both halves default to OFF — that is the plugin security boundary
(GHSA-mcfc-hp25-cjv7), not an oversight. Backend routes mount only when the
app's own server starts, which is why the setup script asks for an app restart
after updating them.

Its own bench runs offline and needs no API keys; the live-upstream pass is
opt-in because an upstream outage is not this code breaking:

```bash
plugins/provider-limits/tests/run.sh          # 53 checks, offline
plugins/provider-limits/tests/run.sh --e2e    # plus real route and upstreams
```

`bun run check` runs the offline pass. See the plugin's own README for what it draws
and why.

## Kanban skill

`kanban/` is a Hermes skill (`SKILL.md`) and its scripts. It is self-contained (see `kanban/README.md`)
and installs by copy:

```bash
bash kanban/install.sh      # copies the skill into ~/.hermes/skills
```

A repository opts in with `kanban/scripts/kanban-init.sh <board-slug>`, which
scaffolds `.kanban/config.env`, the role templates and a per-repo runbook and
never overwrites. Every script prints its usage with no arguments. The tests
drive the scripts against a fake `hermes` (`KANBAN_HERMES`), so no board,
profile or cron job is touched:

```bash
bun run check kanban
```

## Dock application

Build the stay-open macOS application:

```bash
bun run app
```

This creates `Hermes WebUI.app` in the repository root. The applet records the
path of the checkout that built it, so rebuild after moving the repository;
the bundle itself can then be moved to `/Applications` freely. Set
`HERMES_WEBUI_ICON` if Hermes is not at `~/.hermes`.

Opening it binds the
native WebUI to `0.0.0.0:8787` and opens `http://127.0.0.1:8787` in the default
browser. Normally quitting the application stops only the WebUI process that
the application started. Configure Hermes WebUI authentication before exposing
port 8787 to a LAN or the internet.

The same lifecycle can be inspected or recovered from a terminal:

```bash
runner/hermes-webui-app.sh start
runner/hermes-webui-app.sh status
runner/hermes-webui-app.sh stop
```

Force Quit, `kill -9`, a system crash, or power loss can prevent the
application's quit handler from running. In that case, reopening the
application reconnects to its saved process; the `status` and `stop` commands
above are also available for recovery.

## LaunchAgent

For an always-on service that starts at login and restarts after failures, use
the existing macOS user LaunchAgent:

```bash
bun run webui:enable   # enable launch-at-login and start now
bun run webui:status
bun run webui:restart
bun run webui:disable  # stop now and disable launch-at-login
```

The LaunchAgent defaults to port 8787 and bind address `0.0.0.0`, so configure
Hermes WebUI authentication before enabling it. Override
`HERMES_WEBUI_DIR`, `HERMES_WEBUI_HOST`, `HERMES_WEBUI_PORT`, or
`HERMES_WEBUI_PYTHON` when the checkout or runtime uses another path.

The Dock application and LaunchAgent are separate, mutually exclusive launch
modes when configured for the same host and port. The application never changes
LaunchAgent configuration. Disable the LaunchAgent before using the application
on port 8787, and do not run `hermes-webui/start.sh` or
`hermes-webui/ctl.sh start` alongside either managed mode on the same endpoint.
