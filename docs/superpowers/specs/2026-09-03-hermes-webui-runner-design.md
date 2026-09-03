# Hermes WebUI Runner Design

## Goal

Provide a native macOS Dock application in `/Users/frenzy/dev/hermes/hermes-tools` that owns the lifecycle of the complete Hermes WebUI Docker Compose stack. Opening the application starts Docker Desktop when necessary, starts the stack, waits until OpenWebUI is healthy, and opens it in the default browser. Quitting the application normally stops the Compose stack without removing containers or persistent data.

## Scope

The first version manages the existing OpenWebUI service and is intentionally structured to manage every service later added to the same Compose project, including the planned Hermes session bridge.

The runner does not:

- quit Docker Desktop;
- delete containers, images, networks, or volumes;
- manage unrelated Docker Compose projects;
- guarantee cleanup after macOS `Force Quit`, process termination, or a system crash;
- install itself as a Login Item.

## Components

### Shell lifecycle controller

`/Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh` is the single command-line entry point for stack lifecycle operations:

- `stack.sh start` validates prerequisites, starts Docker Desktop when required, starts the Compose project, waits for health, and returns success only when OpenWebUI is reachable.
- `stack.sh stop` stops every service in the Compose project with `docker compose stop`. It never runs `down` or passes `--volumes`.
- `stack.sh status` reports whether Docker is reachable and whether every declared Compose service is running.

All Docker Compose commands run against `/Users/frenzy/dev/hermes/hermes-tools/compose.yaml` and load `API_SERVER_KEY` from `/Users/frenzy/.hermes/.env`. The script never prints the key. It uses explicit absolute paths so launching from Finder does not depend on the process working directory.

### AppleScript application

`/Users/frenzy/dev/hermes/hermes-tools/runner/HermesWebUIRunner.applescript` is compiled as a stay-open application at `/Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app`.

Its handlers have narrow responsibilities:

- `run`: call `stack.sh start`; after success open `http://localhost:11001`; after failure show a macOS error dialog, call `stack.sh stop` to clean up a partially started stack, and quit.
- `reopen`: open `http://localhost:11001` again without restarting a healthy stack.
- `idle`: keep the application resident so its icon remains in the Dock. It does not continuously poll Docker.
- `quit`: call `stack.sh stop`, show an error dialog if stopping fails, then allow the application to quit.

The compiled application uses the existing Hermes icon from `/Users/frenzy/.hermes/hermes-agent/apps/desktop/assets/icon.icns`.

## Startup Flow

1. Verify that `/usr/local/bin/docker` or `/opt/homebrew/bin/docker` is available, falling back to the executable found through a controlled PATH.
2. Verify that `/Users/frenzy/.hermes/.env` exists and contains a non-empty `API_SERVER_KEY` assignment without evaluating the file as shell code.
3. Run `docker info`.
4. If the Docker daemon is unavailable, run `open -a Docker` and poll `docker info` for up to 120 seconds.
5. Run `docker compose --env-file /Users/frenzy/.hermes/.env -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml up -d --remove-orphans`.
6. Poll `http://localhost:11001/health` for up to 120 seconds. A successful 2xx response marks startup complete.
7. Open `http://localhost:11001` in the user's default browser.

Timeouts and command failures produce a non-zero exit status and a concise message suitable for an AppleScript dialog. Diagnostic command output goes to `/Users/frenzy/dev/hermes/hermes-tools/runner/runner.log`, with secret values redacted.

## Shutdown Flow

Normal application Quit calls:

```text
docker compose --env-file /Users/frenzy/.hermes/.env \
  -f /Users/frenzy/dev/hermes/hermes-tools/compose.yaml stop
```

This stops every service in the project and preserves the named `open-webui` volume. Docker Desktop remains running because it may host unrelated containers.

`Force Quit`, `kill -9`, and machine crashes cannot invoke AppleScript's `quit` handler. This is an accepted limitation of the selected minimal implementation.

## Secret Handling

`compose.yaml` must replace the literal OpenAI API key with Compose interpolation:

```yaml
OPENAI_API_KEY: ${API_SERVER_KEY:?API_SERVER_KEY is required}
```

The runner supplies `/Users/frenzy/.hermes/.env` through Compose's `--env-file` option. No copy of the Hermes API key is created under `/Users/frenzy/dev/hermes/hermes-tools`, and neither logs nor dialogs include its value.

OpenWebUI may retain an older connection credential in its internal database because connection environment variables only seed initial configuration. The implementation must verify the effective OpenWebUI-to-Hermes connection after recreating the container and, if necessary, update that persisted connection through OpenWebUI's supported configuration API or Admin UI without deleting the volume.

## Error Handling

- Missing Docker CLI: show an instruction to install or restore Docker Desktop.
- Docker Desktop launch timeout: report that the daemon did not become ready within 120 seconds.
- Missing or empty `API_SERVER_KEY`: report the exact configuration file path; do not start Compose.
- Compose startup failure: record sanitized diagnostics, stop any services started by this attempt, and quit the runner.
- OpenWebUI health timeout: record sanitized diagnostics, stop the stack, and quit the runner.
- Shutdown failure: show the error and allow the application to quit so the user is not trapped in an unresponsive Dock app.

## Verification

The implementation is complete when all of the following have been demonstrated:

1. Shell syntax validation passes for `runner/stack.sh`.
2. The source AppleScript compiles successfully with `/usr/bin/osacompile`.
3. `compose.yaml` contains no literal `OPENAI_API_KEY` secret.
4. Starting the compiled app with Docker already running starts the complete stack and opens `http://localhost:11001`.
5. Starting the app with Docker Desktop stopped launches Docker Desktop, waits for readiness, then starts the stack.
6. Clicking the running Dock icon opens OpenWebUI again without creating a second runner instance.
7. Normal Quit stops all services returned by `docker compose config --services`.
8. The OpenWebUI named volume still exists and existing chats remain available after restart.
9. Docker Desktop remains running after the runner quits.
10. OpenWebUI can list the Hermes model after the Compose configuration change.

## Future Bridge Compatibility

The future Hermes session bridge will be another service in the same `compose.yaml`. Because the runner always operates on the complete project rather than naming the `open-webui` service, no Runner App change will be required when the bridge is added.
