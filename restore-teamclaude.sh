#!/bin/sh

set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
patch_file="$script_dir/teamclaude-oauth-proxy.patch"
source_dir=${HERMES_SOURCE_DIR:-"${HOME}/.hermes/hermes-agent"}
hermes_home=${HERMES_HOME:-"${HOME}/.hermes"}
comp_count_source="$script_dir/desktop-plugins/comp-count/plugin.js"
comp_count_dir="$hermes_home/desktop-plugins/comp-count"
comp_count_target="$comp_count_dir/plugin.js"

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
  printf 'restore-teamclaude: git is not available in PATH\n' >&2
  exit 127
fi

if test ! -f "$patch_file"; then
  printf 'restore-teamclaude: Hermes OAuth proxy patch is missing: %s\n' "$patch_file" >&2
  exit 1
fi
if test ! -f "$comp_count_source"; then
  printf 'restore-teamclaude: comp-count source is missing: %s\n' "$comp_count_source" >&2
  exit 1
fi
if test ! -d "$source_dir/.git"; then
  printf 'restore-teamclaude: Hermes git checkout not found: %s\n' "$source_dir" >&2
  exit 1
fi

comp_count_changed=false
if ! cmp -s "$comp_count_source" "$comp_count_target"; then
  install -d -m 0755 "$comp_count_dir"
  install -m 0644 "$comp_count_source" "$comp_count_target"
  comp_count_changed=true
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
  printf 'restore-teamclaude: OAuth proxy patch no longer applies cleanly after this Hermes update\n' >&2
  printf 'restore-teamclaude: rebase %s onto %s before restarting Hermes\n' "$patch_file" "$source_dir" >&2
  exit 1
fi

if test -n "${HERMES_BIN:-}"; then
  hermes_bin=$HERMES_BIN
elif hermes_bin=$(command -v hermes); then
  :
else
  printf 'restore-teamclaude: hermes is not available in PATH\n' >&2
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
changed=$code_changed

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

if test "$changed" = false; then
  if test "$comp_count_changed" = true; then
    printf 'TeamClaude source patch and settings are already correct; comp-count restored; gateway restart skipped.\n'
  else
    printf 'TeamClaude source patch, settings, and comp-count installation are already correct; gateway restart skipped.\n'
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
  printf 'restore-teamclaude: Hermes retained stale model.base_url\n' >&2
  exit 1
fi
if "$hermes_bin" config get providers.teamclaude.url >/dev/null 2>&1; then
  printf 'restore-teamclaude: Hermes retained stale providers.teamclaude.url\n' >&2
  exit 1
fi
if "$hermes_bin" config get providers.teamclaude.base_url >/dev/null 2>&1; then
  printf 'restore-teamclaude: Hermes retained stale providers.teamclaude.base_url\n' >&2
  exit 1
fi
if "$hermes_bin" config get model.key_env >/dev/null 2>&1; then
  printf 'restore-teamclaude: Hermes retained stale model.key_env\n' >&2
  exit 1
fi
if test "$saved_provider" != "$expected_provider" \
  || test "$saved_provider_name" != "$expected_provider_name" \
  || test "$saved_provider_url" != "$relay_url" \
  || test "$saved_provider_key_env" != "$expected_key_env" \
  || test "$saved_provider_transport" != "$expected_transport" \
  || test "$saved_oauth_proxy" != "$expected_oauth_proxy"; then
  printf 'restore-teamclaude: Hermes did not persist the expected TeamClaude settings\n' >&2
  exit 1
fi

if test "$comp_count_changed" = true; then
  printf 'TeamClaude settings and comp-count restored; Hermes gateway restarted.\n'
else
  printf 'TeamClaude settings restored and Hermes gateway restarted.\n'
fi
