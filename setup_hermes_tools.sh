#!/bin/sh

# Install the Hermes plugins kept in this repository into ~/.hermes.
#
# Deliberately does nothing else — in particular it restarts NOTHING. Patching
# Hermes source lived here once; the OAuth proxy now ships in the Hermes fork,
# and the TeamClaude provider settings this script used to enforce had drifted
# from the working configuration, so "repairing" them would have broken a live
# installation. A `hermes gateway restart` lived here too and was worse than
# useless: it ended live sessions while restarting a process that does not serve
# plugin routes at all (see the note above the final block).

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
hermes_home=${HERMES_HOME:-"${HOME}/.hermes"}

# Both plugins are unified packages: a desktop half the renderer loads and a
# Python half the gateway imports. They install identically, so the work lives
# in one function rather than in two copies that drift apart.
packages='comp-count provider-limits'

# Refuse EVERY incomplete source before writing anything: a half-installed
# plugin whose backend is missing still gets imported and fails at runtime.
for name in $packages; do
  source_dir="$script_dir/plugins/$name"
  if test ! -f "$source_dir/desktop/plugin.js" \
    || test ! -f "$source_dir/dashboard/plugin_api.py"; then
    printf 'setup_hermes_tools: %s source is incomplete: %s\n' "$name" "$source_dir" >&2
    exit 1
  fi
done

if test -n "${HERMES_BIN:-}"; then
  hermes_bin=$HERMES_BIN
elif hermes_bin=$(command -v hermes); then
  :
else
  printf 'setup_hermes_tools: hermes is not available in PATH\n' >&2
  exit 127
fi

installed=''
any_changed=false
backend_changed=false
gate_set=''

# Install one package, updating the four globals above. A POSIX function cannot
# return a tuple, so the state it reports is shared rather than passed back.
install_package() {
  name=$1
  source_dir="$script_dir/plugins/$name"
  target_dir="$hermes_home/plugins/$name"

  # A package is a directory with two halves, so compare the whole tree and
  # replace it wholesale: copying file-by-file would leave a file deleted
  # upstream behind in the installed copy, and a stale plugin_api.py still gets
  # imported.
  if ! diff -r -q \
    -x '.git' -x '__pycache__' -x '*.pyc' \
    "$source_dir" "$target_dir" >/dev/null 2>&1; then
    # Only a BACKEND change needs the app's own `hermes serve` process
    # restarted. A desktop-only edit (CSS, the chip) reaches the screen through
    # Electron's reconcile, so it costs the user nothing.
    if ! diff -r -q \
      -x '__pycache__' -x '*.pyc' \
      "$source_dir/dashboard" "$target_dir/dashboard" >/dev/null 2>&1; then
      backend_changed=true
    fi
    install -d -m 0755 "$hermes_home/plugins"
    rm -rf "$target_dir.tmp"
    cp -R "$source_dir" "$target_dir.tmp"
    rm -rf "$target_dir.tmp/.git"
    find "$target_dir.tmp" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    rm -rf "$target_dir"
    mv "$target_dir.tmp" "$target_dir"
    any_changed=true
    installed="${installed:+$installed, }$name"
  fi

  # The backend half must appear in plugins.enabled or its routes are never
  # imported (GHSA-mcfc-hp25-cjv7). Check before enabling: `plugins enable` is
  # idempotent but reports success either way, and treating that as a change
  # would tell the user to restart the app on every run.
  if ! "$hermes_bin" config get plugins.enabled 2>/dev/null | grep -qx -- "- $name"; then
    "$hermes_bin" plugins enable --no-allow-tool-override "$name"
    backend_changed=true
    gate_set="${gate_set:+$gate_set, }$name"
    installed="${installed:+$installed, }$name backend gate"
  fi
}

for name in $packages; do
  install_package "$name"
done

# The renderer-only disk copy from comp-count's previous layout must go, or the
# app loads TWO plugins claiming id "comp-count": the stale one wins or they
# collide, and either way the popover never appears. Electron re-materializes
# the package's desktop half into this same directory, beside a
# `.hermes-package.json` marker — a directory WITHOUT that marker is the retired
# hand-installed copy, and only that one is removed.
legacy_comp_count="$hermes_home/desktop-plugins/comp-count"
if test -d "$legacy_comp_count" && test ! -f "$legacy_comp_count/.hermes-package.json"; then
  rm -rf "$legacy_comp_count"
  any_changed=true
  installed="${installed:+$installed, }retired the old comp-count disk copy"
fi

# The desktop half the RENDERER loads is a third copy: Electron materializes
# `plugins/<name>/desktop/` into `desktop-plugins/<name>/` with a
# `.hermes-package.json` marker. Updating the package alone leaves that copy
# stale, so a CSS fix can be installed and still not reach the screen.
#
# Only Electron may write it — the marker records the source mtime it copied,
# and a shell script forging that would drift the moment the format changes.
# Instead nudge the watcher the app already keeps on the root directory: a
# directory appearing and vanishing makes it re-resolve the root, which runs
# the reconcile. With the app closed this is a no-op and the reconcile happens
# at next launch anyway.
desktop_root="$hermes_home/desktop-plugins"
if test "$any_changed" = true && test -d "$desktop_root"; then
  nudge="$desktop_root/.setup-hermes-tools-nudge"
  rm -rf "$nudge"
  if mkdir "$nudge" 2>/dev/null; then
    sleep 1
    rmdir "$nudge" 2>/dev/null || true
  fi
fi

# A plugin's Python half is imported ONCE, at the startup of the FastAPI server
# that mounts /api/plugins/<name>/ — and that server is NOT the launchd gateway.
# Hermes.app spawns its own `hermes serve --port 0` child and that child is what
# mounts plugin routes (its "Mounted plugin API routes" lines land in gui.log,
# never in gateway.log). `hermes gateway restart` restarts a different process
# entirely: it ends the user's live sessions and leaves the plugin unmounted, so
# this script does not run it.
#
# Nothing here may restart the app's server either. Killing the child assumes a
# respawn that has not been proven, and no documented IPC asks Electron to
# recycle its backend. Restarting the app is the user's call, so say so plainly
# and let them pick the moment.
if test "$backend_changed" = true; then
  printf 'Installed: %s.\n' "$installed"
  printf '\n'
  printf 'RESTART HERMES DESKTOP to finish: the backend half is imported only\n'
  printf 'when the app starts its own server, so the plugin stays unmounted (its\n'
  printf 'REST calls 404) until Hermes.app is quit and reopened.\n'
  if test -n "$gate_set"; then
    printf '\n'
    printf 'Then enable the desktop half of %s in Capabilities → Plugins.\n' "$gate_set"
  fi
  exit 0
fi

if test -n "$installed"; then
  printf 'Installed: %s; desktop-only change, no restart needed.\n' "$installed"
else
  printf 'Plugins are already installed and enabled; nothing to do.\n'
fi
