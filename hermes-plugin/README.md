# Hermes OpenWebUI bridge plugin

This update-safe Desktop plugin connects the running Hermes app to the local
bridge service. It uses the supported Desktop plugin SDK to resume an exact
stored session on its credential-free profile route, submit with Hermes queue
semantics, and forward replayable live events. It does not patch Hermes or open
a second raw connection to the Hermes gateway.

Install it from the repository root:

```sh
make local-env
make install-plugin
```

The installer reads only the exact `HERMES_BRIDGE_SECRET=` and
`BRIDGE_HOST_PORT=` assignments from the ignored `.env.local`; it never sources
that file. It injects the connector secret into a temporary build and installs
the resulting single ESM file at:

```text
~/.hermes/desktop-plugins/openwebui-bridge/plugin.js
```

The installed file is mode `0600`. The tracked source and ignored `dist/`
output contain no local credential. After rotating `HERMES_BRIDGE_SECRET`, run
`make install-plugin` again.

Hermes Desktop watches the standalone plugin folder and normally hot-loads the
file within five seconds. If it does not, run **Reload desktop plugins** from
the Hermes command palette and confirm **OpenWebUI Bridge** is loaded under
Settings → Plugins.

Connector 1.1.0 advertises the required `replay_complete` capability. The
bridge rejects older installed connectors instead of attempting ambiguous
recovery. If `/health/ready` reports the Desktop connector unavailable after an
upgrade, run `make install-plugin`, then reload Desktop plugins or restart
Hermes Desktop.
