# Hermes Tools

Local macOS tooling for running Hermes WebUI and keeping Hermes Desktop
plugins installed.

## Layout

- `hermes-webui/` — pinned Git submodule for
  `git@github.com:DZamataev/hermes-webui.git`.
- `runner/` — native Dock application and LaunchAgent lifecycle tools.
- `desktop-plugins/comp-count/` — canonical Hermes Desktop status-bar plugin;
  `restore-teamclaude.sh` keeps its installed copy under `~/.hermes` current.
- `docs/` — design specifications and implementation plans.
- `tests/` — repository-level integration and lifecycle checks.

## Bootstrap and test

```bash
make bootstrap
make test
```

## Dock application

Build the stay-open macOS application:

```bash
make app
```

This creates `Hermes WebUI.app` in the repository root. Opening it starts the
native WebUI at `http://127.0.0.1:8787` and opens that address in the default
browser. Normally quitting the application stops only the WebUI process that
the application started.

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
make webui-enable   # enable launch-at-login and start now
make webui-status
make webui-restart
make webui-disable  # stop now and disable launch-at-login
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
