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

# The source carries a placeholder, not a path: a checked-in absolute path only
# works on the machine it was written on.
grep -Fq '@LIFECYCLE_SCRIPT@' "$SOURCE" ||
  fail "AppleScript source must use the @LIFECYCLE_SCRIPT@ placeholder"
! grep -Eq '/Users/[^/]+/' "$SOURCE" ||
  fail "AppleScript source hardcodes a home directory"
! grep -Eq '/Users/[^/]+/' "$BUILD" ||
  fail "build script hardcodes a home directory"
grep -Fq 'http://127.0.0.1:8787' "$SOURCE" || fail "app must open native Hermes WebUI"
! grep -Fq 'runner/stack.sh' "$SOURCE" || fail "app still calls the Docker stack"
! grep -Fq 'localhost:11001' "$SOURCE" || fail "app still opens OpenWebUI"

HERMES_WEBUI_EXPECTED_APP="$APP" "$BUILD"
[[ -f "$APP/Contents/Resources/applet.icns" ]]
[[ "$(/usr/libexec/PlistBuddy -c 'Print :LSMultipleInstancesProhibited' "$APP/Contents/Info.plist")" == "true" ]]
[[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")" == "local.hermes.webui-runner" ]]
/usr/bin/codesign --verify --deep --strict "$APP"
decompiled="$(/usr/bin/osadecompile "$APP")"
# The COMPILED applet must carry a real absolute path: launched from the Dock it
# has no working directory, and an unsubstituted placeholder would only surface
# as a dialog at first launch.
[[ "$decompiled" == *"$ROOT/runner/hermes-webui-app.sh"* ]] ||
  fail "compiled app must call this checkout's native controller"
[[ "$decompiled" != *'@LIFECYCLE_SCRIPT@'* ]] ||
  fail "compiled app still contains the unsubstituted placeholder"
[[ "$decompiled" == *'http://127.0.0.1:8787'* ]] || fail "compiled app must open native WebUI"
[[ "$decompiled" != *'runner/stack.sh'* && "$decompiled" != *'localhost:11001'* ]] ||
  fail "compiled app still contains OpenWebUI lifecycle"

# Build from a copy at a different path: the bundle must follow its checkout
# rather than the one this repository happens to live at.
RELOCATED="$(mktemp -d)/relocated checkout"
trap 'rm -rf "$(dirname "$RELOCATED")"' EXIT HUP INT TERM
mkdir -p "$RELOCATED/runner"
cp "$SOURCE" "$BUILD" "$ROOT/runner/hermes-webui-app.sh" "$RELOCATED/runner/"
chmod +x "$RELOCATED/runner/build-app.sh" "$RELOCATED/runner/hermes-webui-app.sh"
"$RELOCATED/runner/build-app.sh" >"$RELOCATED/build-output" 2>&1 ||
  fail "build failed in a relocated checkout:
$(<"$RELOCATED/build-output")"
relocated_app="$RELOCATED/Hermes WebUI.app"
[[ -d "$relocated_app" ]] || fail "build did not produce a bundle in a relocated checkout"
relocated_decompiled="$(/usr/bin/osadecompile "$relocated_app")"
[[ "$relocated_decompiled" == *"$RELOCATED/runner/hermes-webui-app.sh"* ]] ||
  fail "relocated bundle does not point at its own checkout"
[[ "$relocated_decompiled" != *"$ROOT/runner/hermes-webui-app.sh"* ]] ||
  fail "relocated bundle still points at the original checkout"

# A missing icon must be reported, not silently skipped: the bundle would look
# built and ship with the generic applet face.
if HERMES_WEBUI_ICON="$RELOCATED/no-such-icon.icns" "$RELOCATED/runner/build-app.sh" \
  >"$RELOCATED/icon-output" 2>&1; then
  fail "build accepts a missing icon"
fi
grep -Fq 'App icon not found' "$RELOCATED/icon-output" ||
  fail "build does not explain a missing icon"

print -r -- "PASS: app bundle builds from its own checkout"
