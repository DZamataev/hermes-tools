# Hermes Tools

Integration workspace for using live Hermes Desktop sessions from OpenWebUI.

## Layout

- `hermes-plugin/` — update-safe Hermes user plugin. It will submit turns to
  the existing in-memory Desktop session without replacing its event transport.
- `desktop-plugins/comp-count/` — canonical Hermes Desktop status-bar plugin;
  `restore-teamclaude.sh` keeps its installed copy under `~/.hermes` current.
- `bridge-service/` — OpenAI-compatible adapter, session mapping, event replay,
  and reconciliation between Hermes and OpenWebUI.
- `open-webui/` — pinned Git submodule for
  `git@github.com:DZamataev/open-webui.git`.
- `hermes-webui/` — pinned Git submodule for
  `git@github.com:DZamataev/hermes-webui.git`.
- `runner/` — macOS Dock application source and Docker Compose lifecycle tools.
- `compose.yaml` — complete local stack. The explicit Compose project name
  preserves the existing `hermes_open-webui` data volume.
- `docs/` — architecture specifications and implementation plans.
- `tests/` — repository-level integration and layout checks.

## Bootstrap

```bash
make bootstrap
make test
```

The runner reads `API_SERVER_KEY` from `/Users/frenzy/.hermes/.env`. Secrets
must not be stored in this repository.

Build the Dock application with:

```bash
make app
```

The OpenWebUI checkout uses your fork as `origin`. `make bootstrap` also adds
the public project as `upstream`; `make openwebui-fetch` refreshes both remotes
without changing the checked-out branch.

Stack lifecycle shortcuts are `make start`, `make status`, and `make stop`.

## Native Hermes WebUI LaunchAgent

The `hermes-webui/` checkout can run as a macOS user LaunchAgent.
The runner installs a generated plist in `~/Library/LaunchAgents`, starts the
WebUI on port 8787, and keeps it running after crashes. The default bind address
is `0.0.0.0`, so configure Hermes WebUI authentication before enabling it.

```bash
make webui-enable   # enable launch-at-login and start now
make webui-status
make webui-restart
make webui-disable  # stop now and disable launch-at-login
```

Override `HERMES_WEBUI_DIR`, `HERMES_WEBUI_HOST`, `HERMES_WEBUI_PORT`, or
`HERMES_WEBUI_PYTHON` when the checkout or runtime uses another path.
Do not run `hermes-webui/start.sh` or `hermes-webui/ctl.sh start` in parallel
with the enabled LaunchAgent.
