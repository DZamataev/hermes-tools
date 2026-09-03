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
[[ -d "$ROOT/hermes-plugin" ]] || fail "hermes-plugin directory is missing"
[[ -d "$ROOT/bridge-service" ]] || fail "bridge-service directory is missing"
[[ -e "$ROOT/open-webui/.git" ]] || fail "open-webui submodule is missing"

grep -Eq '^name:[[:space:]]+hermes$' "$ROOT/compose.yaml" ||
  fail "compose project name must remain hermes to preserve the existing volume"

grep -Eq 'OPENAI_API_KEY=[[:alnum:]]{16,}' "$ROOT/compose.yaml" &&
  fail "compose.yaml contains a literal API key"

git -C "$ROOT" check-ignore -q .env.local ||
  fail ".env.local must be ignored"

grep -Eq '^OPENWEBUI_API_KEY=$' "$ROOT/.env.example" ||
  fail ".env.example must leave OPENWEBUI_API_KEY empty"

grep -Eq '^HERMES_BRIDGE_SECRET=$' "$ROOT/.env.example" ||
  fail ".env.example must leave HERMES_BRIDGE_SECRET empty"

grep -Fq 'context: ./open-webui' "$ROOT/compose.yaml" ||
  fail "OpenWebUI must build from the checked-out fork"

grep -Eq '^ENV NODE_OPTIONS="--max-old-space-size=4096"$' \
  "$ROOT/open-webui/Dockerfile" ||
  fail "OpenWebUI Docker build must raise the Node heap limit to 4096 MB"

[[ "$(git -C "$ROOT/open-webui" remote get-url origin)" == "git@github.com:DZamataev/open-webui.git" ]] ||
  fail "open-webui origin does not point to the requested fork"

grep -Fq '/Users/frenzy/dev/hermes/hermes-tools' "$ROOT/runner/stack.sh" ||
  fail "runner default project path does not use the integration repository"

grep -Fq '/Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh' \
  "$ROOT/runner/HermesWebUIRunner.applescript" ||
  fail "AppleScript still points outside the integration repository"
