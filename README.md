# Hermes Tools

Local macOS tooling for running Hermes WebUI and keeping Hermes Desktop
plugins installed.

## Layout

- `hermes-webui/` — pinned Git submodule for
  `git@github.com:DZamataev/hermes-webui.git`.
- `runner/` — native Dock application and LaunchAgent lifecycle tools.
- `desktop-plugins/comp-count/` — canonical Hermes Desktop status-bar plugin.
- `plugins/provider-limits/` — unified plugin (Python backend + desktop UI)
  showing each custom provider's 5-hour quota in the status bar.
- `setup_hermes_tools.sh` — installs both plugins under `~/.hermes`.
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
the name, so it would run the single comp-count file, skip the five shell suites
and the Python bench, and report success. `bun run check` is the entry point;
it runs the suites concurrently and prints the full output of whichever failed.

Suites are safe to run at once: each builds its own `mktemp` sandbox and fakes
`launchctl` and `curl`, so they share no port, file or launchd state.

## Setup

```bash
bun run setup              # or ./setup_hermes_tools.sh without bun
```

Installs `comp-count` and `provider-limits` under `~/.hermes` and sets the
`provider-limits` backend gate. Idempotent, and restarts the Hermes gateway only
when the **backend** actually changed — a desktop-only edit does not, since the
renderer picks it up without one and a restart would end live sessions.

The desktop half the renderer loads is a third copy: Electron materializes
`plugins/<name>/desktop/` into `desktop-plugins/<name>/`. Only Electron may
write it, so the script nudges the directory the app watches, and the reconcile
runs itself. With the app closed that is a no-op — it reconciles at next launch.

It installs plugins and nothing else. Patching Hermes source lived here once;
the TeamClaude OAuth proxy now ships in the Hermes fork, so the patch and the
provider-settings repair were removed — those settings had drifted from the
working configuration, and "repairing" them would have broken a live install.

The script cannot enable the `provider-limits` **desktop** half: the loader
forces a materialized package off whatever the plugin declares, so flip it on
once in Capabilities → Plugins.

## Provider limits plugin

`plugins/provider-limits/` is the source of truth for the status-bar quota chip.
Unlike `comp-count` it has two halves — a FastAPI backend and the desktop UI —
and installs into `~/.hermes/plugins/`.

Both halves default to OFF — that is the plugin security boundary
(GHSA-mcfc-hp25-cjv7), not an oversight. Backend routes mount at gateway startup
only, which is why the setup script restarts the gateway after updating them.

Its own bench runs offline and needs no API keys; the live-upstream pass is
opt-in because an upstream outage is not this code breaking:

```bash
plugins/provider-limits/tests/run.sh          # 53 checks, offline
plugins/provider-limits/tests/run.sh --e2e    # plus real route and upstreams
```

`bun run check` runs the offline pass. See the plugin's own README for what it draws
and why.

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
