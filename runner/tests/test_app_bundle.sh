#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
SOURCE="$ROOT/runner/HermesWebUIRunner.applescript"
BUILD="$ROOT/runner/build-app.sh"
APP="$ROOT/Hermes WebUI.app"

[[ -f "$SOURCE" ]] || { print -u2 -- "missing AppleScript source"; exit 1; }
[[ -x "$BUILD" ]] || { print -u2 -- "missing executable build script"; exit 1; }

for handler in 'on run' 'on reopen' 'on idle' 'on quit'; do
  grep -Fq "$handler" "$SOURCE" || { print -u2 -- "missing $handler"; exit 1; }
done

"$BUILD"
[[ -f "$APP/Contents/Resources/applet.icns" ]]
[[ "$(/usr/libexec/PlistBuddy -c 'Print :LSMultipleInstancesProhibited' "$APP/Contents/Info.plist")" == "true" ]]
/usr/bin/codesign --verify --deep --strict "$APP"
/usr/bin/osadecompile "$APP" | grep -Fq 'http://localhost:11001'
