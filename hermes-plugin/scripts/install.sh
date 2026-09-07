#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd -P)"
ENV_FILE="$ROOT/.env.local"
PLUGIN_DIR="$HOME/.hermes/desktop-plugins/openwebui-bridge"
PLUGIN_FILE="$PLUGIN_DIR/plugin.js"

fail() { print -u2 -- "ERROR: $*"; exit 1; }

[[ -f "$ENV_FILE" ]] || fail ".env.local is missing; run make local-env first"

read_exact_assignment() {
  awk -v name="$1" '
    index($0, name "=") == 1 {
      count += 1
      value = substr($0, length(name) + 2)
      if ((value ~ /^\047.*\047$/) || (value ~ /^".*"$/)) {
        value = substr(value, 2, length(value) - 2)
      }
    }
    END {
      if (count != 1 || value == "") exit 1
      print value
    }
  ' "$ENV_FILE"
}

secret="$(read_exact_assignment HERMES_BRIDGE_SECRET)" ||
  fail ".env.local must contain exactly one non-empty HERMES_BRIDGE_SECRET assignment"
port="$(read_exact_assignment BRIDGE_HOST_PORT)" ||
  fail ".env.local must contain exactly one non-empty BRIDGE_HOST_PORT assignment"

[[ ${#secret} -ge 32 ]] || fail "HERMES_BRIDGE_SECRET must contain at least 32 characters"
[[ "$port" == <-> ]] || fail "BRIDGE_HOST_PORT must be an integer between 1 and 65535"
(( port >= 1 && port <= 65535 )) || fail "BRIDGE_HOST_PORT must be an integer between 1 and 65535"

temp_dir="$(mktemp -d)"
temp_install=""
cleanup() {
  rm -rf -- "$temp_dir"
  [[ -z "$temp_install" ]] || rm -f -- "$temp_install"
}
trap cleanup EXIT

cd "$ROOT/hermes-plugin"
HERMES_BRIDGE_SECRET="$secret" \
  BRIDGE_HOST_PORT="$port" \
  HERMES_PLUGIN_OUTFILE="$temp_dir/plugin.js" \
  npm run build --silent

install -d -m 700 "$PLUGIN_DIR"
temp_install="$(mktemp "$PLUGIN_DIR/.plugin.js.XXXXXX")"
install -m 600 "$temp_dir/plugin.js" "$temp_install"
mv -f -- "$temp_install" "$PLUGIN_FILE"
chmod 600 "$PLUGIN_FILE"
print -- "Installed Hermes Desktop plugin: $PLUGIN_FILE"
