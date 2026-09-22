#!/bin/sh

set -eu

root_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
script="$root_dir/setup_hermes_tools.sh"
plugin_source="$root_dir/desktop-plugins/comp-count/plugin.js"
relay_url='https://teamclaude.larid.dedyn.io:3443'
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT HUP INT TERM

fake_hermes="$test_dir/hermes"
cat >"$fake_hermes" <<'EOF'
#!/bin/sh
set -eu

printf '%s\n' "$*" >>"$FAKE_HERMES_LOG"

line_for_key() {
  case "$1" in
    model.provider) printf '1\n' ;;
    model.base_url) printf '2\n' ;;
    model.key_env) printf '3\n' ;;
    providers.teamclaude.name) printf '4\n' ;;
    providers.teamclaude.api) printf '5\n' ;;
    providers.teamclaude.url) printf '6\n' ;;
    providers.teamclaude.base_url) printf '7\n' ;;
    providers.teamclaude.key_env) printf '8\n' ;;
    providers.teamclaude.transport) printf '9\n' ;;
    providers.teamclaude.capabilities.anthropic_oauth_proxy) printf '10\n' ;;
    *) return 1 ;;
  esac
}

read_key() {
  line=$(line_for_key "$1")
  value=$(sed -n "${line}p" "$FAKE_HERMES_STATE")
  test "$value" != '__missing__' || return 1
  printf '%s\n' "$value"
}

write_key() {
  line=$(line_for_key "$1")
  value=$2
  awk -v target="$line" -v replacement="$value" '
    NR == target { print replacement; next }
    { print }
  ' "$FAKE_HERMES_STATE" >"$FAKE_HERMES_STATE.tmp"
  mv "$FAKE_HERMES_STATE.tmp" "$FAKE_HERMES_STATE"
}

case "$1 $2" in
  'config get')
    # The real CLI prints a list as "- item" lines; the script greps that shape.
    if test "$3" = plugins.enabled; then
      test -s "$FAKE_PLUGINS_STATE" || exit 1
      cat "$FAKE_PLUGINS_STATE"
      exit 0
    fi
    read_key "$3"
    ;;
  'config set')
    test "${FAKE_HERMES_FAIL_SET:-0}" != 1 || exit 9
    write_key "$3" "$4"
    ;;
  'config unset')
    test "${FAKE_HERMES_FAIL_UNSET:-0}" != 1 || exit 9
    write_key "$3" '__missing__'
    ;;
  'plugins enable')
    test "${FAKE_HERMES_FAIL_ENABLE:-0}" != 1 || exit 9
    # The plugin name is the last argument, after any flags.
    for name in "$@"; do :; done
    printf -- '- %s\n' "$name" >>"$FAKE_PLUGINS_STATE"
    ;;
  'gateway restart')
    ;;
  *)
    printf 'unexpected hermes invocation: %s\n' "$*" >&2
    exit 64
    ;;
esac
EOF
chmod +x "$fake_hermes"

fake_git="$test_dir/git"
cat >"$fake_git" <<'EOF'
#!/bin/sh
set -eu

printf '%s\n' "$*" >>"$FAKE_GIT_LOG"

case "$*" in
  *'apply --reverse --check '*)
    test "$(cat "$FAKE_PATCH_STATE")" = applied
    ;;
  *'apply --check '*)
    test "$(cat "$FAKE_PATCH_STATE")" = missing
    ;;
  *'apply '*)
    test "$(cat "$FAKE_PATCH_STATE")" = missing
    printf 'applied\n' >"$FAKE_PATCH_STATE"
    ;;
  *)
    printf 'unexpected git invocation: %s\n' "$*" >&2
    exit 64
    ;;
esac
EOF
chmod +x "$fake_git"
mkdir -p "$test_dir/source/.git"
printf 'applied\n' >"$test_dir/patch-state"
: >"$test_dir/plugins-state"
: >"$test_dir/git-log"

assert_equal() {
  expected=$1
  actual=$2
  label=$3
  if test "$actual" != "$expected"; then
    printf 'FAIL: %s\nexpected:\n%s\nactual:\n%s\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
}

write_state() {
  printf '%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n' "$@" >"$test_dir/state"
}

run_script() {
  HERMES_BIN="$fake_hermes" \
  HERMES_GIT_BIN="$fake_git" \
  HERMES_HOME="$test_dir/home" \
  HERMES_SOURCE_DIR="$test_dir/source" \
  FAKE_HERMES_STATE="$test_dir/state" \
  FAKE_HERMES_LOG="$test_dir/log" \
  FAKE_GIT_LOG="$test_dir/git-log" \
  FAKE_PATCH_STATE="$test_dir/patch-state" \
  FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
    "$script"
}

write_state \
  anthropic \
  'https://api.anthropic.com' \
  CODEX_LB_API_KEY \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__'
: >"$test_dir/log"
run_script >/dev/null
if ! cmp -s "$plugin_source" "$test_dir/home/desktop-plugins/comp-count/plugin.js"; then
  printf 'FAIL: installs comp-count from the repository copy\n' >&2
  exit 1
fi
# provider-limits is a two-half plugin: the desktop file alone is not enough,
# and its backend is dead weight unless the gate is set.
for half in desktop/plugin.js dashboard/plugin_api.py plugin.yaml tests/run.sh; do
  if ! cmp -s "$root_dir/plugins/provider-limits/$half" \
    "$test_dir/home/plugins/provider-limits/$half"; then
    printf 'FAIL: installs provider-limits/%s from the repository copy\n' "$half" >&2
    exit 1
  fi
done
if ! grep -qx -- '- provider-limits' "$test_dir/plugins-state"; then
  printf 'FAIL: enables the provider-limits backend gate\n' >&2
  exit 1
fi
assert_equal "custom:teamclaude
__missing__
__missing__
TeamClaude
$relay_url
__missing__
__missing__
HERMES_CUSTOM_TEAMCLAUDE_API_KEY
anthropic_messages
true" "$(cat "$test_dir/state")" 'registers and selects TeamClaude as an Anthropic OAuth proxy'
assert_equal "config get model.provider
config get model.base_url
config get providers.teamclaude.name
config get providers.teamclaude.api
config get providers.teamclaude.url
config get providers.teamclaude.base_url
config get providers.teamclaude.key_env
config get providers.teamclaude.transport
config get providers.teamclaude.capabilities.anthropic_oauth_proxy
config get model.key_env
config get plugins.enabled
plugins enable --no-allow-tool-override provider-limits
config set model.provider custom:teamclaude
config set providers.teamclaude.name TeamClaude
config set providers.teamclaude.api $relay_url
config set providers.teamclaude.key_env HERMES_CUSTOM_TEAMCLAUDE_API_KEY
config set providers.teamclaude.transport anthropic_messages
config set providers.teamclaude.capabilities.anthropic_oauth_proxy true
config unset model.base_url
config unset model.key_env
gateway restart
config get model.provider
config get providers.teamclaude.name
config get providers.teamclaude.api
config get providers.teamclaude.key_env
config get providers.teamclaude.transport
config get providers.teamclaude.capabilities.anthropic_oauth_proxy
config get model.base_url
config get providers.teamclaude.url
config get providers.teamclaude.base_url
config get model.key_env" "$(cat "$test_dir/log")" 'restarts and verifies after repair'

write_state \
  custom:teamclaude \
  '__missing__' \
  '__missing__' \
  TeamClaude \
  "$relay_url" \
  '__missing__' \
  '__missing__' \
  HERMES_CUSTOM_TEAMCLAUDE_API_KEY \
  anthropic_messages \
  true
: >"$test_dir/log"
printf 'stale plugin\n' >"$test_dir/home/desktop-plugins/comp-count/plugin.js"
run_script >/dev/null
if ! cmp -s "$plugin_source" "$test_dir/home/desktop-plugins/comp-count/plugin.js"; then
  printf 'FAIL: restores an outdated comp-count installation\n' >&2
  exit 1
fi
assert_equal "config get model.provider
config get model.base_url
config get providers.teamclaude.name
config get providers.teamclaude.api
config get providers.teamclaude.url
config get providers.teamclaude.base_url
config get providers.teamclaude.key_env
config get providers.teamclaude.transport
config get providers.teamclaude.capabilities.anthropic_oauth_proxy
config get model.key_env
config get plugins.enabled" "$(cat "$test_dir/log")" 'does nothing when settings are already correct'
if grep -qx 'gateway restart' "$test_dir/log"; then
  printf 'FAIL: restarts the gateway when only comp-count changed\n' >&2
  exit 1
fi
if grep -q '^plugins enable' "$test_dir/log"; then
  printf 'FAIL: re-enables an already enabled plugin\n' >&2
  exit 1
fi

# A file deleted upstream must disappear from the installed copy: a stale
# plugin_api.py is still imported by the gateway, and a stale desktop half is
# still loaded by the renderer.
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/leftover.py"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/desktop/plugin.js"
: >"$test_dir/log"
run_script >/dev/null
if test -e "$test_dir/home/plugins/provider-limits/leftover.py"; then
  printf 'FAIL: leaves a file that no longer exists in the repository copy\n' >&2
  exit 1
fi
if ! cmp -s "$root_dir/plugins/provider-limits/desktop/plugin.js" \
  "$test_dir/home/plugins/provider-limits/desktop/plugin.js"; then
  printf 'FAIL: restores an outdated provider-limits installation\n' >&2
  exit 1
fi
# Updated backend source only takes effect at gateway startup.
if ! grep -qx 'gateway restart' "$test_dir/log"; then
  printf 'FAIL: does not restart after updating the provider-limits backend\n' >&2
  exit 1
fi

write_state \
  custom:teamclaude \
  "$relay_url" \
  '__missing__' \
  TeamClaude \
  "$relay_url" \
  '__missing__' \
  "$relay_url/v1/messages" \
  HERMES_CUSTOM_TEAMCLAUDE_API_KEY \
  anthropic_messages \
  true
: >"$test_dir/log"
run_script >/dev/null
assert_equal "custom:teamclaude
__missing__
__missing__
TeamClaude
$relay_url
__missing__
__missing__
HERMES_CUSTOM_TEAMCLAUDE_API_KEY
anthropic_messages
true" "$(cat "$test_dir/state")" 'removes a stale full-endpoint base_url that wins over api'
if ! grep -qx 'config unset model.base_url' "$test_dir/log"; then
  printf 'FAIL: does not remove the redundant model base_url\n' >&2
  exit 1
fi
if ! grep -qx 'config unset providers.teamclaude.base_url' "$test_dir/log"; then
  printf 'FAIL: does not remove the stale provider base_url alias\n' >&2
  exit 1
fi
if ! grep -qx 'gateway restart' "$test_dir/log"; then
  printf 'FAIL: does not restart after removing the stale provider base_url alias\n' >&2
  exit 1
fi

write_state \
  anthropic \
  'https://api.anthropic.com' \
  CODEX_LB_API_KEY \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__' \
  '__missing__'
: >"$test_dir/log"
if FAKE_HERMES_FAIL_SET=1 run_script >/dev/null 2>&1; then
  printf 'FAIL: returns success when Hermes rejects a config write\n' >&2
  exit 1
fi
if grep -qx 'gateway restart' "$test_dir/log"; then
  printf 'FAIL: restarts gateway after a failed config write\n' >&2
  exit 1
fi

write_state \
  custom:teamclaude \
  '__missing__' \
  '__missing__' \
  TeamClaude \
  "$relay_url" \
  '__missing__' \
  '__missing__' \
  HERMES_CUSTOM_TEAMCLAUDE_API_KEY \
  anthropic_messages \
  true
: >"$test_dir/log"
: >"$test_dir/git-log"
printf 'missing\n' >"$test_dir/patch-state"
run_script >/dev/null
assert_equal applied "$(cat "$test_dir/patch-state")" 'applies the Hermes OAuth proxy patch after an update'
if ! grep -qx 'gateway restart' "$test_dir/log"; then
  printf 'FAIL: does not restart after applying the Hermes OAuth proxy patch\n' >&2
  exit 1
fi

printf 'PASS: setup_hermes_tools.sh\n'

# A three-way --check can succeed even when applying writes conflict markers.
# Exercise real git and assert an incompatible patch leaves the checkout intact.
fixture="$test_dir/conflict"
mkdir -p "$fixture/tools/desktop-plugins/comp-count" "$fixture/source"
cp "$script" "$fixture/tools/setup_hermes_tools.sh"
cp "$plugin_source" "$fixture/tools/desktop-plugins/comp-count/plugin.js"
cp -R "$root_dir/plugins" "$fixture/tools/plugins"
git -C "$fixture/source" init -q
git -C "$fixture/source" config user.name Test
git -C "$fixture/source" config user.email test@example.invalid
printf 'value = "base"\n' >"$fixture/source/example.py"
git -C "$fixture/source" add example.py
git -C "$fixture/source" commit -qm base
printf 'value = "patched"\n' >"$fixture/source/example.py"
git -C "$fixture/source" diff >"$fixture/tools/teamclaude-oauth-proxy.patch"
printf 'value = "upstream"\n' >"$fixture/source/example.py"
git -C "$fixture/source" add example.py
git -C "$fixture/source" commit -qm upstream
: >"$test_dir/log"
if HERMES_SOURCE_DIR="$fixture/source" HERMES_HOME="$fixture/home" \
  HERMES_GIT_BIN="$(command -v git)" HERMES_BIN="$fake_hermes" \
  FAKE_HERMES_LOG="$test_dir/log" FAKE_HERMES_STATE="$test_dir/state" \
  FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
  "$fixture/tools/setup_hermes_tools.sh" >"$fixture/output" 2>&1; then
  printf 'FAIL: accepts a conflicting patch\n' >&2
  exit 1
fi
assert_equal 'value = "upstream"' "$(cat "$fixture/source/example.py")" 'preserves source on patch conflict'
assert_equal '' "$(git -C "$fixture/source" status --porcelain)" 'preserves index on patch conflict'
assert_equal '' "$(cat "$test_dir/log")" 'does not configure or restart after patch conflict'
printf 'PASS: incompatible patch leaves checkout untouched\n'
