#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
RUNNER="$ROOT/runner/hermes-webui-launchd.sh"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

fail() { print -u2 -- "FAIL: $*"; exit 1; }
assert_contains() { [[ "$1" == *"$2"* ]] || fail "expected <$2> in <$1>"; }

mkdir -p "$TEST_TMP/bin" "$TEST_TMP/home" "$TEST_TMP/hermes-webui"
print -r -- '#!/bin/zsh' > "$TEST_TMP/hermes-webui/start.sh"
chmod +x "$TEST_TMP/hermes-webui/start.sh"

cat > "$TEST_TMP/bin/launchctl" <<'FAKE_LAUNCHCTL'
#!/bin/zsh
set -euo pipefail
print -r -- "$*" >> "$FAKE_CALLS"
case "$1" in
  enable)
    rm -f "$FAKE_DISABLED"
    ;;
  disable)
    : > "$FAKE_DISABLED"
    ;;
  bootstrap)
    : > "$FAKE_LOADED"
    ;;
  bootout)
    [[ -f "$FAKE_LOADED" ]] || exit 3
    rm -f "$FAKE_LOADED"
    ;;
  kickstart)
    [[ -f "$FAKE_LOADED" ]] || exit 3
    ;;
  print)
    [[ -f "$FAKE_LOADED" ]] || exit 3
    print -r -- 'state = running'
    ;;
  print-disabled)
    if [[ -f "$FAKE_DISABLED" ]]; then
      print -r -- '"com.parantoux.hermes-webui" => disabled'
    else
      print -r -- '"com.parantoux.hermes-webui" => enabled'
    fi
    ;;
  *)
    exit 64
    ;;
esac
FAKE_LAUNCHCTL

cat > "$TEST_TMP/bin/curl" <<'FAKE_CURL'
#!/bin/zsh
set -euo pipefail
print -r -- "$*" >> "$FAKE_CURL_CALLS"
exit 0
FAKE_CURL
chmod +x "$TEST_TMP/bin/"*

export HERMES_WEBUI_DIR="$TEST_TMP/hermes-webui"
export HERMES_WEBUI_USER_HOME="$TEST_TMP/home"
export HERMES_WEBUI_PYTHON=/usr/bin/true
export HERMES_WEBUI_LAUNCHCTL_BIN="$TEST_TMP/bin/launchctl"
export HERMES_WEBUI_CURL_BIN="$TEST_TMP/bin/curl"
export HERMES_WEBUI_SLEEP_BIN=/usr/bin/true
export HERMES_WEBUI_DOMAIN=gui/999
export HERMES_WEBUI_HEALTH_TIMEOUT=2
export FAKE_CALLS="$TEST_TMP/launchctl.calls"
export FAKE_CURL_CALLS="$TEST_TMP/curl.calls"
export FAKE_LOADED="$TEST_TMP/loaded"
export FAKE_DISABLED="$TEST_TMP/disabled"
: > "$FAKE_CALLS"
: > "$FAKE_CURL_CALLS"

set +e
"$RUNNER" >"$TEST_TMP/missing.stdout" 2>"$TEST_TMP/missing.stderr"
code=$?
set -e
[[ $code -eq 64 ]] || fail "missing command must exit 64"
assert_contains "$(<"$TEST_TMP/missing.stderr")" "Usage:"

"$RUNNER" enable

PLIST="$TEST_TMP/home/Library/LaunchAgents/com.parantoux.hermes-webui.plist"
[[ -f "$PLIST" ]] || fail "enable must install the LaunchAgent plist"
/usr/bin/plutil -lint "$PLIST" >/dev/null || fail "installed plist must be valid"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:1' "$PLIST")" == "$TEST_TMP/hermes-webui/start.sh" ]] ||
  fail "installed plist must point to the selected Hermes WebUI checkout"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments' "$PLIST" | /usr/bin/sed -n '/^[[:space:]]/p' | /usr/bin/wc -l | /usr/bin/tr -d ' ')" == 7 ]] ||
  fail "installed plist must contain exactly seven program arguments"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:4' "$PLIST")" == --host ]] ||
  fail "installed plist must preserve the host flag position"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:5' "$PLIST")" == 0.0.0.0 ]] ||
  fail "installed plist must bind the configured host"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:6' "$PLIST")" == 8787 ]] ||
  fail "installed plist must use the configured port"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :WorkingDirectory' "$PLIST")" == "$TEST_TMP/hermes-webui" ]] ||
  fail "installed plist must use the selected working directory"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :RunAtLoad' "$PLIST")" == true ]] ||
  fail "installed plist must start at login"
[[ -f "$FAKE_LOADED" ]] || fail "enable must load the LaunchAgent"
[[ ! -f "$FAKE_DISABLED" ]] || fail "enable must clear the persistent disabled state"
assert_contains "$(<"$FAKE_CURL_CALLS")" "http://127.0.0.1:8787/health?deep=1"

status_output=$("$RUNNER" status)
assert_contains "$status_output" "enabled"
assert_contains "$status_output" "running"

"$RUNNER" restart

"$RUNNER" disable
[[ ! -f "$FAKE_LOADED" ]] || fail "disable must unload the LaunchAgent"
[[ -f "$FAKE_DISABLED" ]] || fail "disable must persist the disabled state"
status_output=$("$RUNNER" status)
assert_contains "$status_output" "disabled"
assert_contains "$status_output" "stopped"

set +e
"$RUNNER" unexpected >"$TEST_TMP/unknown.stdout" 2>"$TEST_TMP/unknown.stderr"
code=$?
set -e
[[ $code -eq 64 ]] || fail "unknown command must exit 64"
assert_contains "$(<"$TEST_TMP/unknown.stderr")" "Usage:"
