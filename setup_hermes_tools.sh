#!/bin/sh

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
patch_file="$script_dir/teamclaude-oauth-proxy.patch"
source_dir=${HERMES_SOURCE_DIR:-"${HOME}/.hermes/hermes-agent"}
hermes_home=${HERMES_HOME:-"${HOME}/.hermes"}
comp_count_source="$script_dir/desktop-plugins/comp-count/plugin.js"
comp_count_dir="$hermes_home/desktop-plugins/comp-count"
comp_count_target="$comp_count_dir/plugin.js"
provider_limits_source="$script_dir/plugins/provider-limits"
provider_limits_target="$hermes_home/plugins/provider-limits"

relay_url='https://teamclaude.larid.dedyn.io:3443'
expected_provider='custom:teamclaude'
expected_provider_name='TeamClaude'
expected_key_env='HERMES_CUSTOM_TEAMCLAUDE_API_KEY'
expected_transport='anthropic_messages'
expected_oauth_proxy='true'

if test -n "${HERMES_GIT_BIN:-}"; then
  git_bin=$HERMES_GIT_BIN
elif git_bin=$(command -v git); then
  :
else
  printf 'setup_hermes_tools: git is not available in PATH\n' >&2
  exit 127
fi

if test ! -f "$patch_file"; then
  printf 'setup_hermes_tools: Hermes OAuth proxy patch is missing: %s\n' "$patch_file" >&2
  exit 1
fi
if test ! -f "$comp_count_source"; then
  printf 'setup_hermes_tools: comp-count source is missing: %s\n' "$comp_count_source" >&2
  exit 1
fi
if test ! -f "$provider_limits_source/desktop/plugin.js" \
  || test ! -f "$provider_limits_source/dashboard/plugin_api.py"; then
  printf 'setup_hermes_tools: provider-limits source is incomplete: %s\n' "$provider_limits_source" >&2
  exit 1
fi
if test ! -d "$source_dir/.git"; then
  printf 'setup_hermes_tools: Hermes git checkout not found: %s\n' "$source_dir" >&2
  exit 1
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
  rm -rf "$provider_limits_target.tmp/.git" "$provider_limits_target.tmp/__pycache__"
  find "$provider_limits_target.tmp" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  rm -rf "$provider_limits_target"
  mv "$provider_limits_target.tmp" "$provider_limits_target"
  provider_limits_changed=true
fi

code_changed=false
if "$git_bin" -C "$source_dir" apply --reverse --check "$patch_file" >/dev/null 2>&1; then
  :
elif "$git_bin" -C "$source_dir" apply --check "$patch_file" >/dev/null 2>&1; then
  "$git_bin" -C "$source_dir" apply "$patch_file"
  code_changed=true
else
  # --3way --check can succeed even when the apply would write conflict markers.
  # Refuse incompatible updates before touching runnable source or the index.
  printf 'setup_hermes_tools: OAuth proxy patch no longer applies cleanly after this Hermes update\n' >&2
  printf 'setup_hermes_tools: rebase %s onto %s before restarting Hermes\n' "$patch_file" "$source_dir" >&2
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

current_provider=$("$hermes_bin" config get model.provider)
if current_base_url=$("$hermes_bin" config get model.base_url 2>/dev/null); then
  model_base_url_present=true
else
  model_base_url_present=false
fi
current_provider_name=$("$hermes_bin" config get providers.teamclaude.name 2>/dev/null || true)
current_provider_url=$("$hermes_bin" config get providers.teamclaude.api 2>/dev/null || true)
if current_provider_url_alias=$("$hermes_bin" config get providers.teamclaude.url 2>/dev/null); then
  provider_url_alias_present=true
else
  provider_url_alias_present=false
fi
if current_provider_base_url=$("$hermes_bin" config get providers.teamclaude.base_url 2>/dev/null); then
  provider_base_url_present=true
else
  provider_base_url_present=false
fi
current_provider_key_env=$("$hermes_bin" config get providers.teamclaude.key_env 2>/dev/null || true)
current_provider_transport=$("$hermes_bin" config get providers.teamclaude.transport 2>/dev/null || true)
current_oauth_proxy=$("$hermes_bin" config get providers.teamclaude.capabilities.anthropic_oauth_proxy 2>/dev/null || true)
if current_model_key_env=$("$hermes_bin" config get model.key_env 2>/dev/null); then
  model_key_env_present=true
else
  model_key_env_present=false
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

changed=$code_changed

# Backend routes mount at gateway startup only, so new or updated plugin source
# needs the restart below. The desktop half stays a manual toggle in
# Capabilities → Plugins: the loader forces it off for a materialized package
# whatever the plugin declares, and this script cannot flip it.
if test "$provider_limits_changed" = true || test "$provider_limits_gate_changed" = true; then
  changed=true
fi

if test "$current_provider" != "$expected_provider"; then
  "$hermes_bin" config set model.provider "$expected_provider"
  changed=true
fi

if test "$current_provider_name" != "$expected_provider_name"; then
  "$hermes_bin" config set providers.teamclaude.name "$expected_provider_name"
  changed=true
fi

if test "$current_provider_url" != "$relay_url"; then
  "$hermes_bin" config set providers.teamclaude.api "$relay_url"
  changed=true
fi

if test "$current_provider_key_env" != "$expected_key_env"; then
  "$hermes_bin" config set providers.teamclaude.key_env "$expected_key_env"
  changed=true
fi

if test "$current_provider_transport" != "$expected_transport"; then
  "$hermes_bin" config set providers.teamclaude.transport "$expected_transport"
  changed=true
fi

if test "$current_oauth_proxy" != "$expected_oauth_proxy"; then
  "$hermes_bin" config set providers.teamclaude.capabilities.anthropic_oauth_proxy "$expected_oauth_proxy"
  changed=true
fi

if test "$model_base_url_present" = true; then
  "$hermes_bin" config unset model.base_url
  changed=true
fi

if test "$provider_url_alias_present" = true; then
  "$hermes_bin" config unset providers.teamclaude.url
  changed=true
fi

if test "$provider_base_url_present" = true; then
  "$hermes_bin" config unset providers.teamclaude.base_url
  changed=true
fi

if test "$model_key_env_present" = true; then
  "$hermes_bin" config unset model.key_env
  changed=true
fi

restored=''
if test "$comp_count_changed" = true; then
  restored="${restored:+$restored, }comp-count"
fi
if test "$provider_limits_changed" = true; then
  restored="${restored:+$restored, }provider-limits"
fi
if test "$provider_limits_gate_changed" = true; then
  restored="${restored:+$restored, }provider-limits backend gate"
fi

if test "$changed" = false; then
  if test -n "$restored"; then
    printf 'TeamClaude source patch and settings are already correct; restored: %s; gateway restart skipped.\n' "$restored"
  else
    printf 'TeamClaude source patch, settings, and plugin installations are already correct; gateway restart skipped.\n'
  fi
  exit 0
fi

"$hermes_bin" gateway restart

saved_provider=$("$hermes_bin" config get model.provider)
saved_provider_name=$("$hermes_bin" config get providers.teamclaude.name)
saved_provider_url=$("$hermes_bin" config get providers.teamclaude.api)
saved_provider_key_env=$("$hermes_bin" config get providers.teamclaude.key_env)
saved_provider_transport=$("$hermes_bin" config get providers.teamclaude.transport)
saved_oauth_proxy=$("$hermes_bin" config get providers.teamclaude.capabilities.anthropic_oauth_proxy)
if "$hermes_bin" config get model.base_url >/dev/null 2>&1; then
  printf 'setup_hermes_tools: Hermes retained stale model.base_url\n' >&2
  exit 1
fi
if "$hermes_bin" config get providers.teamclaude.url >/dev/null 2>&1; then
  printf 'setup_hermes_tools: Hermes retained stale providers.teamclaude.url\n' >&2
  exit 1
fi
if "$hermes_bin" config get providers.teamclaude.base_url >/dev/null 2>&1; then
  printf 'setup_hermes_tools: Hermes retained stale providers.teamclaude.base_url\n' >&2
  exit 1
fi
if "$hermes_bin" config get model.key_env >/dev/null 2>&1; then
  printf 'setup_hermes_tools: Hermes retained stale model.key_env\n' >&2
  exit 1
fi
if test "$saved_provider" != "$expected_provider" \
  || test "$saved_provider_name" != "$expected_provider_name" \
  || test "$saved_provider_url" != "$relay_url" \
  || test "$saved_provider_key_env" != "$expected_key_env" \
  || test "$saved_provider_transport" != "$expected_transport" \
  || test "$saved_oauth_proxy" != "$expected_oauth_proxy"; then
  printf 'setup_hermes_tools: Hermes did not persist the expected TeamClaude settings\n' >&2
  exit 1
fi

if test -n "$restored"; then
  printf 'TeamClaude settings restored (%s); Hermes gateway restarted.\n' "$restored"
else
  printf 'TeamClaude settings restored and Hermes gateway restarted.\n'
fi
if test "$provider_limits_gate_changed" = true; then
  printf 'Enable the provider-limits desktop half in Capabilities → Plugins to see the chip.\n'
fi
