#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

fail() { print -u2 -- "FAIL: $*"; exit 1; }

[[ -f "$ROOT/README.md" ]] || fail "README.md is missing"
[[ -f "$ROOT/package.json" ]] || fail "package.json is missing"
[[ -f "$ROOT/scripts/check.mjs" ]] || fail "scripts/check.mjs is missing"
[[ ! -e "$ROOT/Makefile" ]] || fail "the Makefile was replaced by bun scripts"
[[ -x "$ROOT/setup_hermes_tools.sh" ]] || fail "setup_hermes_tools.sh is missing or not executable"
[[ ! -e "$ROOT/restore-teamclaude.sh" ]] ||
  fail "restore-teamclaude.sh was renamed to setup_hermes_tools.sh"
[[ ! -e "$ROOT/tests/test-restore-teamclaude.sh" ]] ||
  fail "test-restore-teamclaude.sh was renamed to test-setup-hermes-tools.sh"
[[ ! -e "$ROOT/teamclaude-oauth-proxy.patch" ]] ||
  fail "the OAuth proxy patch was retired: it now ships in the Hermes fork"
[[ -x "$ROOT/runner/hermes-webui-launchd.sh" ]] ||
  fail "runner/hermes-webui-launchd.sh is missing or not executable"
[[ -f "$ROOT/runner/com.parantoux.hermes-webui.plist.in" ]] ||
  fail "Hermes WebUI LaunchAgent template is missing"
[[ -f "$ROOT/desktop-plugins/comp-count/plugin.js" ]] ||
  fail "comp-count desktop plugin is missing"
[[ -f "$ROOT/plugins/provider-limits/desktop/plugin.js" ]] ||
  fail "provider-limits desktop half is missing"
[[ -f "$ROOT/plugins/provider-limits/dashboard/plugin_api.py" ]] ||
  fail "provider-limits backend half is missing"
[[ -x "$ROOT/plugins/provider-limits/tests/run.sh" ]] ||
  fail "provider-limits test bench is missing or not executable"

for obsolete in \
  "$ROOT/open-webui" \
  "$ROOT/bridge-service" \
  "$ROOT/hermes-plugin" \
  "$ROOT/compose.yaml" \
  "$ROOT/runner/stack.sh" \
  "$ROOT/runner/tests/test_stack.sh"; do
  [[ ! -e "$obsolete" ]] || fail "obsolete OpenWebUI path remains: $obsolete"
done

for obsolete in \
  "$ROOT/docs/superpowers/specs/2026-09-03-hermes-openwebui-bridge-design.md" \
  "$ROOT/docs/superpowers/specs/2026-09-03-hermes-webui-runner-design.md" \
  "$ROOT/docs/superpowers/plans/2026-09-03-hermes-openwebui-bridge.md" \
  "$ROOT/docs/superpowers/plans/2026-09-03-hermes-webui-runner.md" \
  "$ROOT/docs/hermes-webui-assessment.md"; do
  [[ ! -e "$obsolete" ]] || fail "obsolete OpenWebUI document remains: $obsolete"
done

[[ -e "$ROOT/hermes-webui/.git" ]] || fail "hermes-webui submodule is missing"
[[ "$(git config -f "$ROOT/.gitmodules" --get-regexp '^submodule\..*\.path$' | wc -l | tr -d ' ')" == "1" ]] ||
  fail "hermes-webui must be the only registered submodule"
[[ "$(git config -f "$ROOT/.gitmodules" --get submodule.hermes-webui.path)" == "hermes-webui" ]] ||
  fail "hermes-webui submodule path is incorrect"
[[ "$(git config -f "$ROOT/.gitmodules" --get submodule.hermes-webui.url)" == "git@github.com:DZamataev/hermes-webui.git" ]] ||
  fail "hermes-webui submodule URL is incorrect"

grep -Fq 'bun run app' "$ROOT/README.md" || fail "README must document the Dock app"
grep -Fq 'bun run webui:enable' "$ROOT/README.md" || fail "README must document launchd"
grep -Fq 'bun run check' "$ROOT/README.md" || fail "README must document the test entry point"
# `bun test` runs bun's own file-name-matching runner and would silently skip
# every shell suite, so the README must never tell anyone to use it.
! grep -Eq '^\s*bun test\b' "$ROOT/README.md" ||
  fail "README suggests 'bun test', which runs only the bun test files"
grep -Fq '127.0.0.1:8787' "$ROOT/README.md" || fail "README must document the native endpoint"
grep -Fq 'runner/hermes-webui-app.sh' "$ROOT/README.md" || fail "README must document app lifecycle commands"
! rg -n 'OpenWebUI|open-webui|localhost:11001|Docker Compose lifecycle' "$ROOT/README.md" >/dev/null ||
  fail "README still describes the removed OpenWebUI stack"
