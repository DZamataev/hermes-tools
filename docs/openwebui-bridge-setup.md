# Hermes Desktop ↔ OpenWebUI bridge setup

Hermes remains authoritative. Existing normal Desktop sessions are mirrored as
native OpenWebUI chats in the `Hermes` folder, and a message sent from a mapped
chat continues the same Hermes lineage. Hermes Desktop must be running for a
new turn; stored history remains readable while it is offline.

## One-time local setup

From `/Users/frenzy/dev/hermes/hermes-tools` run:

```sh
make bootstrap
```

This creates the ignored `.env.local` with mode `0600`, generates
`HERMES_BRIDGE_SECRET` when needed, and leaves `OPENWEBUI_API_KEY` empty. Do not
source or commit this file.

1. Start/sign in to OpenWebUI as the first administrator. If the existing UI
   is stopped and no owner key exists yet, start only that service once with a
   non-secret interpolation placeholder:

   ```sh
   OPENWEBUI_API_KEY=bootstrap-pending docker compose \
     --env-file /Users/frenzy/.hermes/.env --env-file .env.local \
     -f compose.yaml up -d open-webui
   ```

   This does not start the bridge and does not store the placeholder.
2. Open **Admin Panel → Settings → Authentication**, enable **API Keys**, and
   save.
3. Open the user menu → **Settings → Account → API Keys**, create or rotate the
   administrator key, and copy it once.
4. Put it in the ignored file as `OPENWEBUI_API_KEY=<copied value>`. A shortened
   example is `sk-…`; never paste a real key into tracked files or logs.
5. Open **Admin Panel → Settings → Connections → OpenAI API → Manage**. Edit the
   bridge connection (or add it if absent):
   - URL: `http://bridge-service:8787/v1`
   - authentication: Bearer
   - API key: the value of `HERMES_BRIDGE_SECRET` from `.env.local`
   - Advanced → Headers:

```json
{
  "X-Hermes-Chat-Id": "{{CHAT_ID}}",
  "X-Hermes-User-Message-Id": "{{USER_MESSAGE_ID}}",
  "X-Hermes-Assistant-Message-Id": "{{MESSAGE_ID}}"
}
```

Save the connection and keep the `hermes-live` model enabled. These template
names are provided by the pinned OpenWebUI fork; do not replace them with fixed
IDs. Before testing, confirm `hermes-live` appears in the model selector. If it
is absent, the bridge connection is not active; submitting with `hermes-agent`
uses the direct Hermes connection and does not test the bridge.

Install the update-safe Desktop plugin:

```sh
make install-plugin
```

Hermes normally hot-loads it within five seconds. Otherwise use **Reload
desktop plugins** and confirm **OpenWebUI Bridge** under **Settings → Plugins**.

## Start, inspect, and stop

```sh
make contract-test
make start
make status
make stop
```

`make contract-test` creates and deletes one uniquely named temporary folder
and chat through the pinned supported API. `make start` waits for OpenWebUI and
the authenticated Desktop connector. `make stop` uses `docker compose stop`,
so both `hermes_open-webui` and `hermes_bridge-data` survive. The Dock runner
uses the same start/stop behavior: closing the app stops containers without
deleting data.

New chats created manually in OpenWebUI are intentionally not mapped in this
MVP. Start in an automatically mirrored chat inside the `Hermes` folder.

## Acceptance sequence

After the one-time setup:

Use Peekaboo as the computer-use driver for automated macOS UI actions in this
workflow. Do not substitute Orca or another UI driver.

1. Run `make install-plugin`, `make start`, and `make status`.
2. Confirm `hermes-live` is present in the OpenWebUI model selector. Stop the
   acceptance run if it is absent.
3. Confirm `http://localhost:11001/health` and
   `http://127.0.0.1:8787/health/ready` succeed.
4. Confirm volumes `hermes_open-webui` and `hermes_bridge-data` exist.
5. From `/health/status`, record one lineage/session identifier without
   recording prompt content; confirm it appears exactly once in `Hermes`.
6. Open the same chat in Desktop and OpenWebUI, select `hermes-live`, send a
   unique idle message from OpenWebUI, and confirm the same lineage receives it.
7. Start a Desktop turn, then send another unique OpenWebUI message while busy;
   confirm FIFO order.
8. Send a Desktop-originated turn and confirm the already-open OpenWebUI chat
   reloads with the stored result.
9. Restart only `bridge-service`; confirm there is no duplicate chat, message,
   or Hermes turn.
10. Quit Hermes Desktop. History must remain readable and submit must fail with
   `hermes_desktop_offline` rather than starting a shadow runtime.
11. Reopen Desktop and confirm `/health/ready` recovers without manually
    reopening the Hermes session.

## Rotation and recovery

- OpenWebUI key rotation: update only `OPENWEBUI_API_KEY` in `.env.local`, then
  restart the stack.
- Connector-secret rotation: update `HERMES_BRIDGE_SECRET`, run
  `make install-plugin`, then restart/reload the plugin and stack. Mismatched or
  legacy connectors fail readiness closed.
- `delivery_uncertain`: inspect `/health/status` and the authoritative Hermes
  transcript. The bridge never retries such a submit automatically. If Hermes
  history has no explicit bridge operation identity, resolve the turn manually
  before allowing more messages on that lineage.

Never paste `.env.local`, `/Users/frenzy/.hermes/.env`, or runner logs into an
issue without redacting credentials.
