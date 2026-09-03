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
