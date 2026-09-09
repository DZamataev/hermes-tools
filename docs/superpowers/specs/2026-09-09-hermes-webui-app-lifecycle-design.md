# Hermes WebUI App Lifecycle and OpenWebUI Cleanup Design

## Goal

Remove the obsolete OpenWebUI integration from `hermes-tools` and replace its
Docker-oriented Dock application with an optional native macOS application for
Hermes WebUI. The existing LaunchAgent remains available as an independent
always-on option. The Dock application owns one native Hermes WebUI process and
keeps it running only for the application's lifetime during normal operation.

## Scope

The cleanup removes the root-level OpenWebUI integration:

- the `open-webui` Git submodule and its `.gitmodules` entry;
- the root `compose.yaml` Docker stack;
- `bridge-service/` and `hermes-plugin/`;
- the Docker stack controller and its tests;
- historical specifications and plans dedicated to OpenWebUI, its bridge, and
  the old Docker runner;
- Make targets, README instructions, and repository-layout assertions that
  describe or require the removed components.

The cleanup does not remove Docker support from the `hermes-webui` submodule.
Those files belong to the upstream Hermes WebUI project and remain available to
users who choose its own Docker deployment path. The cleanup also preserves the
Hermes WebUI submodule, the desktop `comp-count` plugin, the TeamClaude restore
script, and the existing LaunchAgent integration.

## User-Facing Modes

After the change, Hermes WebUI has two explicit macOS launch modes:

1. `launchd`: the existing `make webui-enable` flow starts Hermes WebUI at
   login and restarts it after failures.
2. Dock application: `make app` builds `Hermes WebUI.app`; opening it starts a
   native Hermes WebUI process, and normally quitting it stops that same
   process.

The modes are mutually exclusive for a given host and port. The application
does not enable, disable, bootstrap, or unload the LaunchAgent. If another
Hermes WebUI instance already owns the configured endpoint, application startup
fails with a clear message and leaves the existing instance untouched.

## Components

### Application lifecycle controller

Add a Zsh controller under `runner/` with `start`, `stop`, and `status`
operations. It uses absolute repository paths by default and supports
environment overrides so lifecycle behavior can be tested without touching
real Hermes state.

`start` performs the following work:

1. Validate the Hermes WebUI checkout and selected Python executable.
2. Reject an invalid host or port. If a saved PID identifies a live process
   from this controller and checkout, treat it as the already-running
   application-owned instance and proceed to health verification without
   spawning a duplicate.
3. If there is no valid application-owned process, check the configured
   endpoint before launching. If any server already
   responds, fail without trying to replace or stop it.
4. Start `hermes-webui/start.sh` in foreground mode with browser opening
   disabled, redirecting output to `~/.hermes/webui-app.log`.
5. Store the spawned PID in `~/.hermes/webui-app.pid` using a restrictive
   state directory and file permissions.
6. During the bounded startup window, require both that the spawned PID remains
   alive and that the Hermes WebUI health endpoint responds successfully.
7. On startup failure, terminate only the spawned process, remove stale
   application state, and return a concise error.

`stop` reads the application PID, verifies that it is still a process belonging
to the configured Hermes WebUI checkout, sends `SIGTERM`, and waits for bounded
graceful shutdown. It may use `SIGKILL` only after the grace period expires. It
never stops a process based solely on a port probe and never invokes
`ctl.sh stop` or `launchctl`.

`status` reports whether the application-owned process is running and removes
stale PID state only after verifying that the recorded process no longer
exists. A server on the endpoint without a valid application-owned PID is
reported as external and is not adopted.

The default binding is `127.0.0.1:8787`, matching native Hermes WebUI. The
controller uses Hermes WebUI's health-probe behavior so TLS-related environment
configuration continues to work. Test overrides cover repository path, home
and state paths, Python, health commands, wait commands, host, port, and
timeouts.

### AppleScript application

Retain the existing single-instance, stay-open AppleScript application shape,
but point it at the native application lifecycle controller.

- `run` calls the controller's `start` operation, then opens the configured
  Hermes WebUI URL only after successful health verification.
- `reopen` opens the URL again without starting another process.
- `idle` keeps the application resident in the Dock.
- `quit` calls the controller's `stop` operation and then allows the
  application to exit. A stop failure is shown to the user but does not trap
  them in an application that cannot quit.

The compiled application remains `Hermes WebUI.app`, keeps the existing Hermes
icon, is ad-hoc signed, and prohibits multiple instances.

## Ownership and Failure Semantics

The PID file is the ownership boundary. The application may stop only a live
PID whose command identity resolves to this Hermes WebUI checkout. A missing,
stale, malformed, or mismatched PID is never signaled.

The controller does not adopt a process started by `launchd`, `ctl.sh`, a shell,
or another checkout. This avoids an application quit terminating a service the
application did not create.

Normal application Quit runs cleanup. macOS Force Quit, `SIGKILL`, a system
crash, or power loss cannot run the AppleScript quit handler. In those cases the
native process may remain alive. A later application launch validates and
reattaches to the saved application-owned process instead of spawning a
duplicate; a subsequent normal Quit stops it. This limitation is documented
rather than hidden.

## Repository Interface

The root Makefile retains:

- `make bootstrap` for submodule initialization;
- `make app` for building the Dock application;
- `make test` for repository tests;
- `make webui-enable`, `webui-disable`, `webui-restart`, and `webui-status` for
  LaunchAgent management.

It removes the old Compose lifecycle and OpenWebUI remote-fetch targets. The
test target replaces the Docker stack test with the native application
lifecycle test and continues to build and inspect the application bundle.

The root README describes Hermes Tools as native Hermes WebUI support plus the
desktop plugin/restore tooling. It clearly distinguishes the persistent
LaunchAgent from the app-owned lifecycle option and warns not to run both on
the same endpoint.

## Testing

Implementation follows a test-first lifecycle:

1. Repository-layout tests first fail while removed OpenWebUI paths and root
   Compose references remain, then pass after cleanup.
2. Isolated controller tests first fail because the native app lifecycle
   controller does not exist. Fake processes and health probes verify startup,
   health timeout cleanup, PID ownership, graceful stop, stale-state handling,
   external-server refusal, and secret-safe output.
3. Application source and bundle tests first fail against the old Docker URL
   and controller, then verify the native URL, native lifecycle calls,
   single-instance metadata, icon, signature, and successful compilation.
4. Existing LaunchAgent tests remain green, demonstrating that the optional
   persistent launch mode was not regressed.
5. The full root `make test` suite and shell syntax checks run before
   completion is claimed.

Tests use isolated temporary state and never start or stop the user's real
Hermes WebUI, LaunchAgent, Docker containers, or processes.

## Documentation Cleanup

Historical documents whose subject is the removed OpenWebUI bridge or Docker
runner are deleted rather than kept as active project guidance. The new design
and implementation plan become the authoritative root-level documentation for
the Dock application. Documentation inside the `hermes-webui` submodule remains
unchanged.
