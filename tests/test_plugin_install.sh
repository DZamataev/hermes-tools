#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
INSTALLER="$ROOT/hermes-plugin/scripts/install.sh"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

fail() { print -u2 -- "FAIL: $*"; exit 1; }

mkdir -p "$TEST_TMP/project/hermes-plugin/scripts" "$TEST_TMP/bin" "$TEST_TMP/home"
cp "$INSTALLER" "$TEST_TMP/project/hermes-plugin/scripts/install.sh"

EXPECTED_SECRET="test-bridge-secret-that-is-at-least-32-chars"
{
  print -r -- "HERMES_BRIDGE_SECRET='$EXPECTED_SECRET'"
  print -r -- "BRIDGE_HOST_PORT=8787"
} > "$TEST_TMP/project/.env.local"

cat > "$TEST_TMP/bin/npm" <<'FAKE_NPM'
#!/bin/zsh
set -euo pipefail
[[ "$*" == "run build --silent" ]]
print -rn -- "$HERMES_BRIDGE_SECRET" > "$HERMES_PLUGIN_OUTFILE"
FAKE_NPM
chmod +x "$TEST_TMP/bin/npm"

HOME="$TEST_TMP/home" PATH="$TEST_TMP/bin:/usr/bin:/bin" \
  "$TEST_TMP/project/hermes-plugin/scripts/install.sh" >/dev/null

installed="$TEST_TMP/home/.hermes/desktop-plugins/openwebui-bridge/plugin.js"
[[ -f "$installed" ]] || fail "plugin was not installed"
[[ "$(<"$installed")" == "$EXPECTED_SECRET" ]] ||
  fail "installer must pass the dotenv value without its single quotes"

