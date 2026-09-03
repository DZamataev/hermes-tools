# Hermes Tools

Integration workspace for using live Hermes Desktop sessions from OpenWebUI.

## Layout

- `hermes-plugin/` — update-safe Hermes user plugin. It will submit turns to
  the existing in-memory Desktop session without replacing its event transport.
- `bridge-service/` — OpenAI-compatible adapter, session mapping, event replay,
  and reconciliation between Hermes and OpenWebUI.
- `open-webui/` — pinned Git submodule for
  `git@github.com:DZamataev/open-webui.git`.
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

Every runner Compose command loads `/Users/frenzy/.hermes/.env` first and the
ignored repository `.env.local` second. The Hermes file supplies
`API_SERVER_KEY`; `.env.local` supplies `OPENWEBUI_API_KEY` and a
`HERMES_BRIDGE_SECRET` of at least 32 characters. Run `make local-env` to create
or repair `.env.local`, then fill the OpenWebUI key locally. The runner validates assignments
with `awk` and never sources or prints either file.

Build the Dock application with:

```bash
make app
```

The OpenWebUI checkout uses your fork as `origin`. `make bootstrap` also adds
the public project as `upstream`; `make openwebui-fetch` refreshes both remotes
without changing the checked-out branch.

`make start` launches the forked OpenWebUI and bridge service, then waits for
OpenWebUI health and a live compatible Hermes Desktop connector. `make status`
reports container state separately from connector readiness. `make stop` uses
`docker compose stop`, preserving both `hermes_open-webui` and bridge data for
the next launch.

The isolated Docker integration check is opt-in:

```bash
make bridge-stack-test
```

It uses a unique Compose project and temporary volume; it never starts or
removes the production `hermes` project or its volumes.

The complete one-time OpenWebUI setup, manual acceptance sequence, and recovery
instructions are in [`docs/openwebui-bridge-setup.md`](docs/openwebui-bridge-setup.md).
After creating the first administrator's OpenWebUI API key, verify the pinned
server contract with `make contract-test`.
