#!/bin/sh

set -eu

root_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
script="$root_dir/restore-teamclaude.sh"
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
  HERMES_SOURCE_DIR="$test_dir/source" \
  FAKE_HERMES_STATE="$test_dir/state" \
  FAKE_HERMES_LOG="$test_dir/log" \
  FAKE_GIT_LOG="$test_dir/git-log" \
  FAKE_PATCH_STATE="$test_dir/patch-state" \
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
run_script >/dev/null
assert_equal "config get model.provider
config get model.base_url
config get providers.teamclaude.name
config get providers.teamclaude.api
config get providers.teamclaude.url
config get providers.teamclaude.base_url
config get providers.teamclaude.key_env
config get providers.teamclaude.transport
config get providers.teamclaude.capabilities.anthropic_oauth_proxy
config get model.key_env" "$(cat "$test_dir/log")" 'does nothing when settings are already correct'

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

printf 'PASS: restore-teamclaude.sh\n'
