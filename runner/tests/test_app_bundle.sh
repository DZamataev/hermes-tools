#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SOURCE="$ROOT/runner/HermesWebUIRunner.applescript"
BUILD="$ROOT/runner/build-app.sh"
APP="$ROOT/Hermes WebUI.app"

fail() { print -u2 -- "FAIL: $*"; exit 1; }

[[ -f "$SOURCE" ]] || { print -u2 -- "missing AppleScript source"; exit 1; }
[[ -x "$BUILD" ]] || { print -u2 -- "missing executable build script"; exit 1; }

for handler in 'on run' 'on reopen' 'on idle' 'on quit'; do
  grep -Fq "$handler" "$SOURCE" || { print -u2 -- "missing $handler"; exit 1; }
done

grep -Fq '/Users/frenzy/dev/hermes/hermes-tools/runner/hermes-webui-app.sh' "$SOURCE" ||
  fail "app must call the native Hermes WebUI controller"
grep -Fq 'http://127.0.0.1:8787' "$SOURCE" || fail "app must open native Hermes WebUI"
! grep -Fq 'runner/stack.sh' "$SOURCE" || fail "app still calls the Docker stack"
! grep -Fq 'localhost:11001' "$SOURCE" || fail "app still opens OpenWebUI"

HERMES_WEBUI_EXPECTED_APP="$APP" "$BUILD"
[[ -f "$APP/Contents/Resources/applet.icns" ]]
[[ "$(/usr/libexec/PlistBuddy -c 'Print :LSMultipleInstancesProhibited' "$APP/Contents/Info.plist")" == "true" ]]
[[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")" == "local.hermes.webui-runner" ]]
/usr/bin/codesign --verify --deep --strict "$APP"
decompiled="$(/usr/bin/osadecompile "$APP")"
[[ "$decompiled" == *'/Users/frenzy/dev/hermes/hermes-tools/runner/hermes-webui-app.sh'* ]] ||
  fail "compiled app must call native controller"
[[ "$decompiled" == *'http://127.0.0.1:8787'* ]] || fail "compiled app must open native WebUI"
[[ "$decompiled" != *'runner/stack.sh'* && "$decompiled" != *'localhost:11001'* ]] ||
  fail "compiled app still contains OpenWebUI lifecycle"
