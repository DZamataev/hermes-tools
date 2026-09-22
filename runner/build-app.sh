#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
SOURCE="$ROOT/runner/HermesWebUIRunner.applescript"
APP="$ROOT/Hermes WebUI.app"
LIFECYCLE="$ROOT/runner/hermes-webui-app.sh"
USER_HOME="${HERMES_WEBUI_USER_HOME:-${HOME:?HOME is not set}}"
ICON="${HERMES_WEBUI_ICON:-$USER_HOME/.hermes/hermes-agent/apps/desktop/assets/icon.icns}"

die() { print -u2 -- "$1"; exit 1; }

[[ -f "$SOURCE" ]] || die "AppleScript source not found: $SOURCE"
[[ -x "$LIFECYCLE" ]] || die "Lifecycle script not found or not executable: $LIFECYCLE"
[[ -f "$ICON" ]] || die "App icon not found: $ICON
Set HERMES_WEBUI_ICON to an .icns file if Hermes lives elsewhere."

# A caller that knows where the bundle should land can still pin it; the build
# refuses to write anywhere else. Unset, the path follows this checkout.
if [[ -n "${HERMES_WEBUI_EXPECTED_APP:-}" ]]; then
  [[ "$APP" == "$HERMES_WEBUI_EXPECTED_APP" ]] || die "Unexpected app output path: $APP"
fi

# `rm -rf` below is derived from ROOT, so it can only reach a sibling of this
# script — but refuse a path that is not a bundle anyway: a surprising ROOT
# must not silently delete whatever happens to sit there.
if [[ -e "$APP" && ! -d "$APP/Contents" ]]; then
  die "Refusing to replace a path that is not an app bundle: $APP"
fi

# The applet stores the controller path as a compile-time property: launched
# from the Dock it has no working directory to resolve a relative path against.
# Substituting at build time records the checkout that built it, so the bundle
# keeps working after it is moved to /Applications.
case "$LIFECYCLE" in
  (*'"'*|*'\'*)
    die "Checkout path contains a quote or backslash, which cannot be embedded in AppleScript: $LIFECYCLE" ;;
esac

grep -Fq '@LIFECYCLE_SCRIPT@' "$SOURCE" ||
  die "AppleScript source has no @LIFECYCLE_SCRIPT@ placeholder: $SOURCE"

BUILD_TMP="$(mktemp -d)"
trap 'rm -rf "$BUILD_TMP"' EXIT HUP INT TERM
GENERATED="$BUILD_TMP/HermesWebUIRunner.applescript"
source_text="$(<"$SOURCE")"
print -r -- "${source_text//@LIFECYCLE_SCRIPT@/$LIFECYCLE}" >"$GENERATED"

/bin/rm -rf "$APP"
/usr/bin/osacompile -s -o "$APP" "$GENERATED"
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
