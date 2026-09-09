#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
SOURCE="$ROOT/runner/HermesWebUIRunner.applescript"
ICON="/Users/frenzy/.hermes/hermes-agent/apps/desktop/assets/icon.icns"
APP="$ROOT/Hermes WebUI.app"
EXPECTED_APP="/Users/frenzy/dev/hermes/hermes-tools/Hermes WebUI.app"
EXPECTED_APP="${HERMES_WEBUI_EXPECTED_APP:-$EXPECTED_APP}"

die() { print -u2 -- "$1"; exit 1; }

[[ -f "$SOURCE" ]] || die "AppleScript source not found: $SOURCE"
[[ -f "$ICON" ]] || die "App icon not found: $ICON"
[[ "$APP" == "$EXPECTED_APP" ]] || die "Unexpected app output path: $APP"

/bin/rm -rf "$APP"
/usr/bin/osacompile -s -o "$APP" "$SOURCE"
/bin/cp "$ICON" "$APP/Contents/Resources/applet.icns"

ensure_plist_string() {
  local key="$1"
  if ! /usr/libexec/PlistBuddy -c "Print :$key" "$APP/Contents/Info.plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Add :$key string" "$APP/Contents/Info.plist"
  fi
}

ensure_plist_string CFBundleDisplayName
ensure_plist_string CFBundleIdentifier
/usr/libexec/PlistBuddy -c 'Set :CFBundleDisplayName Hermes WebUI' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier local.hermes.webui-runner' "$APP/Contents/Info.plist"

set_plist_boolean() {
  local key="$1"
  local value="$2"
  if /usr/libexec/PlistBuddy -c "Print :$key" "$APP/Contents/Info.plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$APP/Contents/Info.plist"
  else
    /usr/libexec/PlistBuddy -c "Add :$key bool $value" "$APP/Contents/Info.plist"
  fi
}

set_plist_boolean LSMultipleInstancesProhibited true
set_plist_boolean LSUIElement false

/usr/bin/codesign --force --deep --sign - "$APP"
