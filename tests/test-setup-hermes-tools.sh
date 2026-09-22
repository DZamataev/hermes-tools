#!/bin/sh

# Checks for setup_hermes_tools.sh. A fake `hermes` records every invocation, so
# the assertions cover not just the installed files but exactly which commands
# ran — a needless `gateway restart` ends the user's live sessions.

set -eu

root_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
script="$root_dir/setup_hermes_tools.sh"
plugin_source="$root_dir/desktop-plugins/comp-count/plugin.js"
test_dir=$(mktemp -d)
trap 'rm -rf "$test_dir"' EXIT HUP INT TERM

fake_hermes="$test_dir/hermes"
cat >"$fake_hermes" <<'EOF'
#!/bin/sh
set -eu

printf '%s\n' "$*" >>"$FAKE_HERMES_LOG"

case "$1 $2" in
  'config get')
    # The real CLI prints a list as "- item" lines; the script greps that shape.
    test "$3" = plugins.enabled || exit 1
    test -s "$FAKE_PLUGINS_STATE" || exit 1
    cat "$FAKE_PLUGINS_STATE"
    ;;
  'plugins enable')
    test "${FAKE_HERMES_FAIL_ENABLE:-0}" != 1 || exit 9
    # The plugin name is the last argument, after any flags.
    for name in "$@"; do :; done
    printf -- '- %s\n' "$name" >>"$FAKE_PLUGINS_STATE"
    ;;
  'gateway restart')
    ;;
  'config set'|'config unset')
    # Accepted on purpose, and recorded separately: a check at the end of this
    # file asserts the script never wrote configuration in ANY scenario.
    # Refusing here would abort with a cryptic error instead of that named
    # failure, and only the strict log comparisons would notice.
    printf '%s\n' "$*" >>"$FAKE_CONFIG_WRITES"
    ;;
  *)
    printf 'unexpected hermes invocation: %s\n' "$*" >&2
    exit 64
    ;;
esac
EOF
chmod +x "$fake_hermes"

: >"$test_dir/log"
: >"$test_dir/plugins-state"
: >"$test_dir/config-writes"

assert_equal() {
  expected=$1
  actual=$2
  label=$3
  if test "$actual" != "$expected"; then
    printf 'FAIL: %s\nexpected:\n%s\nactual:\n%s\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

run_script() {
  HERMES_BIN="$fake_hermes" \
  HERMES_HOME="$test_dir/home" \
  FAKE_HERMES_LOG="$test_dir/log" \
  FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
  FAKE_CONFIG_WRITES="$test_dir/config-writes" \
    "$script"
}

# A writable copy of the repository, for cases that must dirty the source.
fixture_src="$test_dir/repo"
mkdir -p "$fixture_src/desktop-plugins/comp-count"
cp "$script" "$fixture_src/setup_hermes_tools.sh"
cp "$plugin_source" "$fixture_src/desktop-plugins/comp-count/plugin.js"
cp -R "$root_dir/plugins" "$fixture_src/plugins"

# --- first run: nothing installed yet ---------------------------------------

run_script >/dev/null

cmp -s "$plugin_source" "$test_dir/home/desktop-plugins/comp-count/plugin.js" ||
  fail 'installs comp-count from the repository copy'

# provider-limits is a two-half plugin: the desktop file alone is not enough,
# and its backend is dead weight unless the gate is set.
for half in desktop/plugin.js dashboard/plugin_api.py plugin.yaml tests/run.sh; do
  cmp -s "$root_dir/plugins/provider-limits/$half" \
    "$test_dir/home/plugins/provider-limits/$half" ||
    fail "installs provider-limits/$half from the repository copy"
done
grep -qx -- '- provider-limits' "$test_dir/plugins-state" ||
  fail 'enables the provider-limits backend gate'

assert_equal "config get plugins.enabled
plugins enable --no-allow-tool-override provider-limits
gateway restart" "$(cat "$test_dir/log")" 'enables the gate and restarts on first install'

# --- second run: everything already in place --------------------------------

: >"$test_dir/log"
run_script >/dev/null
assert_equal 'config get plugins.enabled' "$(cat "$test_dir/log")" \
  'does nothing when the plugins are already installed'

# --- comp-count alone must not restart the gateway --------------------------

: >"$test_dir/log"
printf 'stale plugin\n' >"$test_dir/home/desktop-plugins/comp-count/plugin.js"
run_script >/dev/null
cmp -s "$plugin_source" "$test_dir/home/desktop-plugins/comp-count/plugin.js" ||
  fail 'restores an outdated comp-count installation'
if grep -qx 'gateway restart' "$test_dir/log"; then
  fail 'restarts the gateway when only comp-count changed'
fi

# --- updated backend source must restart ------------------------------------

# A file deleted upstream must disappear from the installed copy: a stale
# plugin_api.py is still imported by the gateway, and a stale desktop half is
# still loaded by the renderer.
: >"$test_dir/log"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/leftover.py"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/desktop/plugin.js"
run_script >/dev/null
if test -e "$test_dir/home/plugins/provider-limits/leftover.py"; then
  fail 'leaves a file that no longer exists in the repository copy'
fi
cmp -s "$root_dir/plugins/provider-limits/desktop/plugin.js" \
  "$test_dir/home/plugins/provider-limits/desktop/plugin.js" ||
  fail 'restores an outdated provider-limits installation'
grep -qx 'gateway restart' "$test_dir/log" ||
  fail 'does not restart after updating the provider-limits backend'

# --- a gate the user turned off is set again --------------------------------

: >"$test_dir/log"
: >"$test_dir/plugins-state"
run_script >/dev/null
grep -qx -- '- provider-limits' "$test_dir/plugins-state" ||
  fail 're-enables a gate that is no longer set'
grep -qx 'gateway restart' "$test_dir/log" ||
  fail 'does not restart after setting the gate again'

# --- __pycache__ from the source never reaches the installed copy -----------

# Running the plugin's own bench in the repository leaves __pycache__ behind;
# that build artifact must not be installed. Cache in the TARGET is different —
# Python writes it when the gateway imports the plugin, and deleting it on every
# run would force a reinstall and a gateway restart for nothing, which is why
# the comparison ignores it on both sides.
: >"$test_dir/log"
mkdir -p "$fixture_src/plugins/provider-limits/dashboard/__pycache__"
printf 'compiled\n' >"$fixture_src/plugins/provider-limits/dashboard/__pycache__/x.pyc"
printf 'changed\n' >>"$fixture_src/plugins/provider-limits/plugin.yaml"
HERMES_BIN="$fake_hermes" HERMES_HOME="$test_dir/home" \
  FAKE_HERMES_LOG="$test_dir/log" FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
  FAKE_CONFIG_WRITES="$test_dir/config-writes" \
  "$fixture_src/setup_hermes_tools.sh" >/dev/null
if test -e "$test_dir/home/plugins/provider-limits/dashboard/__pycache__"; then
  fail 'copies __pycache__ out of the repository into the installed copy'
fi

# --- a failing enable must not be reported as success -----------------------

: >"$test_dir/log"
: >"$test_dir/plugins-state"
if FAKE_HERMES_FAIL_ENABLE=1 run_script >/dev/null 2>&1; then
  fail 'returns success when Hermes rejects the enable'
fi
if grep -qx 'gateway restart' "$test_dir/log"; then
  fail 'restarts the gateway after a failed enable'
fi

# --- incomplete sources are refused before anything is written --------------

fixture="$test_dir/incomplete"
mkdir -p "$fixture/desktop-plugins/comp-count" "$fixture/plugins/provider-limits/desktop"
cp "$script" "$fixture/setup_hermes_tools.sh"
cp "$plugin_source" "$fixture/desktop-plugins/comp-count/plugin.js"
cp "$root_dir/plugins/provider-limits/desktop/plugin.js" \
  "$fixture/plugins/provider-limits/desktop/plugin.js"
: >"$test_dir/log"
if HERMES_BIN="$fake_hermes" HERMES_HOME="$fixture/home" \
  FAKE_HERMES_LOG="$test_dir/log" FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
  FAKE_CONFIG_WRITES="$test_dir/config-writes" \
  "$fixture/setup_hermes_tools.sh" >"$fixture/output" 2>&1; then
  fail 'accepts a provider-limits source with no backend half'
fi
grep -q 'provider-limits source is incomplete' "$fixture/output" ||
  fail 'does not explain why an incomplete source was refused'
if test -e "$fixture/home"; then
  fail 'installs from an incomplete source before checking it'
fi
assert_equal '' "$(cat "$test_dir/log")" 'runs hermes despite an incomplete source'

# --- across every scenario above: no configuration was written --------------

# This tool installs plugins. Provider and model configuration belongs to the
# user's own setup; the version that "repaired" it had drifted from the working
# configuration and would have rewritten a live installation.
assert_equal '' "$(cat "$test_dir/config-writes")" \
  'writes configuration it has no business touching'

printf 'PASS: setup_hermes_tools.sh\n'
