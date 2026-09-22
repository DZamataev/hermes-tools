#!/bin/sh

# Install the Hermes plugins kept in this repository into ~/.hermes.
#
# Deliberately does nothing else. Patching Hermes source lived here once; the
# OAuth proxy now ships in the Hermes fork, and the TeamClaude provider settings
# this script used to enforce had drifted from the working configuration, so
# "repairing" them would have broken a live installation and restarted the
# gateway underneath running sessions.

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
hermes_home=${HERMES_HOME:-"${HOME}/.hermes"}
comp_count_source="$script_dir/desktop-plugins/comp-count/plugin.js"
comp_count_dir="$hermes_home/desktop-plugins/comp-count"
comp_count_target="$comp_count_dir/plugin.js"
provider_limits_source="$script_dir/plugins/provider-limits"
provider_limits_target="$hermes_home/plugins/provider-limits"

if test ! -f "$comp_count_source"; then
  printf 'setup_hermes_tools: comp-count source is missing: %s\n' "$comp_count_source" >&2
  exit 1
fi
if test ! -f "$provider_limits_source/desktop/plugin.js" \
  || test ! -f "$provider_limits_source/dashboard/plugin_api.py"; then
  printf 'setup_hermes_tools: provider-limits source is incomplete: %s\n' "$provider_limits_source" >&2
  exit 1
fi

if test -n "${HERMES_BIN:-}"; then
  hermes_bin=$HERMES_BIN
elif hermes_bin=$(command -v hermes); then
  :
else
  printf 'setup_hermes_tools: hermes is not available in PATH\n' >&2
  exit 127
fi

comp_count_changed=false
if ! cmp -s "$comp_count_source" "$comp_count_target"; then
  install -d -m 0755 "$comp_count_dir"
  install -m 0644 "$comp_count_source" "$comp_count_target"
  comp_count_changed=true
fi

# provider-limits is a directory with two halves, so compare the whole tree and
# replace it wholesale: copying file-by-file would leave a file deleted upstream
# behind in the installed copy, and a stale plugin_api.py still gets imported.
provider_limits_changed=false
if ! diff -r -q \
  -x '.git' -x '__pycache__' -x '*.pyc' \
  "$provider_limits_source" "$provider_limits_target" >/dev/null 2>&1; then
  install -d -m 0755 "$hermes_home/plugins"
  rm -rf "$provider_limits_target.tmp"
  cp -R "$provider_limits_source" "$provider_limits_target.tmp"
  rm -rf "$provider_limits_target.tmp/.git"
  find "$provider_limits_target.tmp" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$provider_limits_target"
  mv "$provider_limits_target.tmp" "$provider_limits_target"
  provider_limits_changed=true
fi

# The backend half must appear in plugins.enabled or its routes are never
# imported (GHSA-mcfc-hp25-cjv7). Check before enabling: `plugins enable` is
# idempotent but reports success either way, and treating that as a change
# would restart the gateway on every run.
provider_limits_enabled=false
if "$hermes_bin" config get plugins.enabled 2>/dev/null | grep -qx -- '- provider-limits'; then
  provider_limits_enabled=true
fi
provider_limits_gate_changed=false
if test "$provider_limits_enabled" = false; then
  "$hermes_bin" plugins enable --no-allow-tool-override provider-limits
  provider_limits_gate_changed=true
fi

installed=''
if test "$comp_count_changed" = true; then
  installed="${installed:+$installed, }comp-count"
fi
if test "$provider_limits_changed" = true; then
  installed="${installed:+$installed, }provider-limits"
fi
if test "$provider_limits_gate_changed" = true; then
  installed="${installed:+$installed, }provider-limits backend gate"
fi

# Backend routes mount at gateway startup only, so new or updated backend source
# needs a restart. comp-count is desktop-only: the renderer reloads it by itself,
# and restarting for it would end live sessions for nothing.
if test "$provider_limits_changed" = true || test "$provider_limits_gate_changed" = true; then
  "$hermes_bin" gateway restart
  printf 'Installed: %s; Hermes gateway restarted.\n' "$installed"
  if test "$provider_limits_gate_changed" = true; then
    printf 'Enable the provider-limits desktop half in Capabilities → Plugins to see the chip.\n'
  fi
  exit 0
fi

if test -n "$installed"; then
  printf 'Installed: %s; gateway restart not needed.\n' "$installed"
else
  printf 'Plugins are already installed and enabled; nothing to do.\n'
fi
