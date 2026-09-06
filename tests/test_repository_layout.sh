#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

fail() { print -u2 -- "FAIL: $*"; exit 1; }

[[ -f "$ROOT/README.md" ]] || fail "README.md is missing"
[[ -f "$ROOT/Makefile" ]] || fail "Makefile is missing"
[[ -f "$ROOT/compose.yaml" ]] || fail "compose.yaml is missing"
[[ -f "$ROOT/.env.example" ]] || fail ".env.example is missing"
[[ -x "$ROOT/runner/stack.sh" ]] || fail "runner/stack.sh is missing or not executable"
[[ -x "$ROOT/runner/bootstrap-local-env.sh" ]] || fail "runner/bootstrap-local-env.sh is missing or not executable"
[[ -x "$ROOT/tests/test_bridge_stack.sh" ]] || fail "tests/test_bridge_stack.sh is missing or not executable"
[[ -f "$ROOT/tests/contract/test_openwebui_live.py" ]] || fail "OpenWebUI contract test is missing"
[[ -x "$ROOT/tests/fakes/fake_desktop_connector.py" ]] || fail "fake Desktop connector is missing or not executable"
[[ -f "$ROOT/docs/openwebui-bridge-setup.md" ]] || fail "bridge setup guide is missing"
[[ -d "$ROOT/hermes-plugin" ]] || fail "hermes-plugin directory is missing"
[[ -d "$ROOT/bridge-service" ]] || fail "bridge-service directory is missing"
[[ -e "$ROOT/open-webui/.git" ]] || fail "open-webui submodule is missing"
[[ -f "$ROOT/hermes-plugin/package-lock.json" ]] || fail "Hermes plugin lockfile is missing"
[[ -f "$ROOT/hermes-plugin/src/connector-core.js" ]] || fail "Hermes connector core is missing"
[[ -f "$ROOT/hermes-plugin/src/plugin.js" ]] || fail "Hermes Desktop plugin entry is missing"
[[ -x "$ROOT/hermes-plugin/scripts/install.sh" ]] || fail "plugin installer is missing or not executable"

grep -Eq '^name:[[:space:]]+hermes$' "$ROOT/compose.yaml" ||
  fail "compose project name must remain hermes to preserve the existing volume"

grep -Eq 'OPENAI_API_KEY=[[:alnum:]]{16,}' "$ROOT/compose.yaml" &&
  fail "compose.yaml contains a literal API key"

git -C "$ROOT" check-ignore -q .env.local ||
  fail ".env.local must be ignored"

git -C "$ROOT" check-ignore -q hermes-plugin/dist/plugin.js ||
  fail "Hermes plugin build output must be ignored"

grep -Eq '^OPENWEBUI_API_KEY=$' "$ROOT/.env.example" ||
  fail ".env.example must leave OPENWEBUI_API_KEY empty"

grep -Eq '^HERMES_BRIDGE_SECRET=$' "$ROOT/.env.example" ||
  fail ".env.example must leave HERMES_BRIDGE_SECRET empty"

grep -Fq 'context: ./open-webui' "$ROOT/compose.yaml" ||
  fail "OpenWebUI must build from the checked-out fork"

grep -Eq '^[[:space:]]+bridge-service:$' "$ROOT/compose.yaml" ||
  fail "compose.yaml must declare bridge-service"

grep -Fq 'http://bridge-service:8787/v1' "$ROOT/compose.yaml" ||
  fail "OpenWebUI must use the internal bridge endpoint"

grep -Fq 'OPENAI_API_KEY: ${HERMES_BRIDGE_SECRET:' "$ROOT/compose.yaml" ||
  fail "OpenWebUI must authenticate to the bridge with HERMES_BRIDGE_SECRET"

grep -Fq '127.0.0.1:${BRIDGE_HOST_PORT:-8787}:8787' "$ROOT/compose.yaml" ||
  fail "bridge port must bind to loopback only"

grep -Eq '^[[:space:]]+bridge-data:$' "$ROOT/compose.yaml" ||
  fail "compose.yaml must declare bridge-data"

grep -Fq '/bin/zsh tests/test_bridge_stack.sh' "$ROOT/Makefile" ||
  fail "Makefile does not expose the isolated bridge stack test"

grep -Fq 'tests/contract/test_openwebui_live.py' "$ROOT/Makefile" ||
  fail "Makefile does not expose the OpenWebUI contract test"

grep -Eq '^ENV NODE_OPTIONS="--max-old-space-size=4096"$' \
  "$ROOT/open-webui/Dockerfile" ||
  fail "OpenWebUI Docker build must raise the Node heap limit to 4096 MB"

[[ "$(git -C "$ROOT/open-webui" remote get-url origin)" == "git@github.com:DZamataev/open-webui.git" ]] ||
  fail "open-webui origin does not point to the requested fork"

grep -Fq '/Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh' \
  "$ROOT/runner/HermesWebUIRunner.applescript" ||
  fail "AppleScript still points outside the integration repository"

grep -Fq 'hermes-plugin/scripts/install.sh' "$ROOT/Makefile" ||
  fail "Makefile does not expose the safe plugin installer"

grep -Fq '.hermes/desktop-plugins/openwebui-bridge' \
  "$ROOT/hermes-plugin/scripts/install.sh" ||
  fail "plugin installer does not target the standalone Desktop plugin door"

if [[ -f "$ROOT/.env.local" ]]; then
  bridge_secret="$(awk '
    /^HERMES_BRIDGE_SECRET=/ {
      count += 1
      value = substr($0, length("HERMES_BRIDGE_SECRET=") + 1)
    }
    END { if (count == 1 && value != "") print value }
  ' "$ROOT/.env.local")"
  if [[ -n "$bridge_secret" ]] && git -C "$ROOT" grep -Fq -- "$bridge_secret"; then
    fail "a tracked file contains the local Hermes bridge secret"
  fi
fi
