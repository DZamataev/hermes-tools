#!/bin/sh

# Checks for setup_hermes_tools.sh. A fake `hermes` records every invocation, so
# the assertions cover not just the installed files but exactly which commands
# ran.
#
# The script must restart NOTHING. Plugin routes are mounted by the `hermes
# serve` child Hermes.app spawns for itself — not by the launchd gateway — so a
# `gateway restart` ends the user's live sessions AND leaves the plugin
# unmounted. The fake below treats it as a hard error rather than an assertion
# in one scenario, so no future path can reintroduce it quietly.

set -eu

root_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
script="$root_dir/setup_hermes_tools.sh"
comp_count_source="$root_dir/plugins/comp-count"
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
    # Not "unexpected" — specifically forbidden, and worth its own message:
    # this is the regression that shipped once already.
    printf 'setup_hermes_tools restarted the gateway; it serves no plugin routes\n' >&2
    exit 65
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

# Every run's stdout is kept: the script's remedy for a backend change is the
# printed instruction (it restarts nothing), so the output IS the behaviour
# under test, not a side effect. Callers redirect to /dev/null for quiet; the
# copy in $test_dir/out is what the assertions read.
run_script() {
  HERMES_BIN="$fake_hermes" \
  HERMES_HOME="$test_dir/home" \
  FAKE_HERMES_LOG="$test_dir/log" \
  FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
  FAKE_CONFIG_WRITES="$test_dir/config-writes" \
    "$script" >"$test_dir/out" 2>"$test_dir/err" && script_status=0 || script_status=$?
  cat "$test_dir/err" >&2
  cat "$test_dir/out"
  return "$script_status"
}

# A writable copy of the repository, for cases that must dirty the source.
fixture_src="$test_dir/repo"
mkdir -p "$fixture_src"
cp "$script" "$fixture_src/setup_hermes_tools.sh"
cp -R "$root_dir/plugins" "$fixture_src/plugins"

# --- first run: nothing installed yet ---------------------------------------

run_script >/dev/null

# Both are two-half plugins: the desktop file alone is not enough, and a
# backend is dead weight unless the gate is set.
for name in comp-count provider-limits; do
  for half in desktop/plugin.js dashboard/plugin_api.py plugin.yaml tests/run.sh; do
    cmp -s "$root_dir/plugins/$name/$half" \
      "$test_dir/home/plugins/$name/$half" ||
      fail "installs $name/$half from the repository copy"
  done
  grep -qx -- "- $name" "$test_dir/plugins-state" ||
    fail "enables the $name backend gate"
done

assert_equal "config get plugins.enabled
plugins enable --no-allow-tool-override comp-count
config get plugins.enabled
plugins enable --no-allow-tool-override provider-limits" \
  "$(cat "$test_dir/log")" 'enables both gates and restarts nothing on first install'

# A first install cannot be finished by this script: the app's server imported
# its modules before the plugin existed. Saying so IS the remedy, so the printed
# instruction is part of the contract, not decoration.
grep -qi 'RESTART HERMES DESKTOP' "$test_dir/out" ||
  fail 'never tells the user to restart the app, so the plugin stays unmounted'
grep -qi 'Capabilities' "$test_dir/out" ||
  fail 'never names where to enable the desktop half'

# --- second run: everything already in place --------------------------------

: >"$test_dir/log"
run_script >/dev/null
assert_equal 'config get plugins.enabled
config get plugins.enabled' "$(cat "$test_dir/log")" \
  'does nothing when the plugins are already installed'

# --- a comp-count desktop-only change needs no restart at all ---------------

: >"$test_dir/log"
printf 'stale plugin\n' >"$test_dir/home/plugins/comp-count/desktop/plugin.js"
run_script >/dev/null
cmp -s "$comp_count_source/desktop/plugin.js" \
  "$test_dir/home/plugins/comp-count/desktop/plugin.js" ||
  fail 'restores an outdated comp-count desktop half'
grep -qi 'no restart needed' "$test_dir/out" ||
  fail 'asks for a restart when only the comp-count desktop half changed'

# --- a desktop-only change must NOT ask for a restart -----------------------

# The renderer picks up a desktop edit through Electron's reconcile, so a CSS
# fix must not send the user to quit the app. This was a real defect: every
# desktop-only edit restarted the gateway.
: >"$test_dir/log"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/desktop/plugin.js"
run_script >/dev/null
cmp -s "$root_dir/plugins/provider-limits/desktop/plugin.js" \
  "$test_dir/home/plugins/provider-limits/desktop/plugin.js" ||
  fail 'restores an outdated provider-limits desktop half'
grep -qi 'no restart needed' "$test_dir/out" ||
  fail 'asks for a restart after a desktop-only change'

# --- updated backend source must ask for an app restart ---------------------

# A file deleted upstream must disappear from the installed copy: a stale
# plugin_api.py is still imported by the gateway.
: >"$test_dir/log"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/leftover.py"
printf 'stale\n' >>"$test_dir/home/plugins/provider-limits/dashboard/plugin_api.py"
run_script >/dev/null
if test -e "$test_dir/home/plugins/provider-limits/leftover.py"; then
  fail 'leaves a file that no longer exists in the repository copy'
fi
cmp -s "$root_dir/plugins/provider-limits/dashboard/plugin_api.py" \
  "$test_dir/home/plugins/provider-limits/dashboard/plugin_api.py" ||
  fail 'restores an outdated provider-limits backend'
grep -qi 'RESTART HERMES DESKTOP' "$test_dir/out" ||
  fail 'stays silent after a backend update, so the stale module keeps serving'

# --- the renderer's copy gets nudged, and nothing is left behind ------------

# Electron materializes plugins/<name>/desktop/ into desktop-plugins/<name>/;
# updating the package alone leaves that copy stale, so a CSS fix can be
# installed and still never reach the screen. The script cannot write that copy
# (only Electron may), so it touches the root the app watches.
#
# The nudge is observed the way the app observes it: a real fswatch on the root.
#
# The root is created here on purpose. Electron owns it; no package writes into
# it any more, so a fixture that never launched the app would not have one and
# the nudge would (correctly) be skipped.
: >"$test_dir/log"
mkdir -p "$test_dir/home/desktop-plugins"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/desktop/plugin.js"
before_inode=$(stat -f '%i %m' "$test_dir/home/desktop-plugins" 2>/dev/null || echo none)
: >"$test_dir/fswatch"
if command -v fswatch >/dev/null 2>&1; then
  fswatch -1 --event Created --event Removed "$test_dir/home/desktop-plugins" \
    >"$test_dir/fswatch" 2>/dev/null &
  watcher_pid=$!
  sleep 0.3
else
  watcher_pid=''
fi
run_script >/dev/null
if test -n "$watcher_pid"; then
  sleep 0.5
  kill "$watcher_pid" 2>/dev/null || true
  wait "$watcher_pid" 2>/dev/null || true
  grep -q . "$test_dir/fswatch" ||
    fail 'never touched desktop-plugins, so the renderer keeps a stale copy'
else
  # No fswatch: fall back to the directory mtime, which a create+remove bumps.
  after_inode=$(stat -f '%i %m' "$test_dir/home/desktop-plugins" 2>/dev/null || echo none)
  test "$before_inode" != "$after_inode" ||
    fail 'never touched desktop-plugins, so the renderer keeps a stale copy'
fi
if ls -a "$test_dir/home/desktop-plugins" | grep -q nudge; then
  fail 'leaves its reconcile nudge behind in desktop-plugins'
fi

# With no desktop-plugins root the nudge must not invent one. comp-count's own
# install legitimately recreates the root, so the assertion is specifically that
# no nudge directory is left or created there.
rm -rf "$test_dir/home/desktop-plugins"
printf 'stale\n' >"$test_dir/home/plugins/provider-limits/desktop/plugin.js"
run_script >/dev/null
if ls -a "$test_dir/home/desktop-plugins" 2>/dev/null | grep -q nudge; then
  fail 'left a nudge directory behind in a freshly created root'
fi

# Restore the root for the checks that follow.
mkdir -p "$test_dir/home/desktop-plugins"

# --- the retired comp-count disk copy is removed ----------------------------

# comp-count used to install as a lone plugin.js under desktop-plugins/. Left
# behind, the app loads TWO plugins claiming id "comp-count" and the popover
# never appears. Electron's own materialized copy carries a
# .hermes-package.json marker and must SURVIVE.
: >"$test_dir/log"
mkdir -p "$test_dir/home/desktop-plugins/comp-count"
printf 'retired copy\n' >"$test_dir/home/desktop-plugins/comp-count/plugin.js"
run_script >/dev/null
if test -e "$test_dir/home/desktop-plugins/comp-count"; then
  fail 'leaves the retired comp-count disk copy in place'
fi

: >"$test_dir/log"
mkdir -p "$test_dir/home/desktop-plugins/comp-count"
printf 'materialized\n' >"$test_dir/home/desktop-plugins/comp-count/plugin.js"
printf '{}\n' >"$test_dir/home/desktop-plugins/comp-count/.hermes-package.json"
run_script >/dev/null
test -f "$test_dir/home/desktop-plugins/comp-count/.hermes-package.json" ||
  fail "removes Electron's own materialized copy of the package"
rm -rf "$test_dir/home/desktop-plugins/comp-count"

# --- a gate the user turned off is set again --------------------------------

: >"$test_dir/log"
: >"$test_dir/plugins-state"
run_script >/dev/null
for name in comp-count provider-limits; do
  grep -qx -- "- $name" "$test_dir/plugins-state" ||
    fail "re-enables the $name gate once it is no longer set"
done
grep -qi 'RESTART HERMES DESKTOP' "$test_dir/out" ||
  fail 'stays silent after setting the gate again, so the gate never takes effect'

# --- __pycache__ from the source never reaches the installed copy -----------

# Running the plugin's own bench in the repository leaves __pycache__ behind;
# that build artifact must not be installed. Cache in the TARGET is different —
# Python writes it when the server imports the plugin, and deleting it on every
# run would force a reinstall and a needless "restart the app" for nothing,
# which is why the comparison ignores it on both sides.
: >"$test_dir/log"
mkdir -p "$fixture_src/plugins/provider-limits/dashboard/__pycache__"
printf 'compiled\n' >"$fixture_src/plugins/provider-limits/dashboard/__pycache__/x.pyc"
printf 'changed\n' >>"$fixture_src/plugins/provider-limits/plugin.yaml"
# The dirty COPY of the script, not the repository one, so run_script is not
# usable here — but stdout still goes to the same file every other case reads,
# or a later assertion would silently grade this run's output.
HERMES_BIN="$fake_hermes" HERMES_HOME="$test_dir/home" \
  FAKE_HERMES_LOG="$test_dir/log" FAKE_PLUGINS_STATE="$test_dir/plugins-state" \
  FAKE_CONFIG_WRITES="$test_dir/config-writes" \
  "$fixture_src/setup_hermes_tools.sh" >"$test_dir/out"
if test -e "$test_dir/home/plugins/provider-limits/dashboard/__pycache__"; then
  fail 'copies __pycache__ out of the repository into the installed copy'
fi

# --- a failing enable must not be reported as success -----------------------

: >"$test_dir/log"
: >"$test_dir/plugins-state"
if FAKE_HERMES_FAIL_ENABLE=1 run_script >/dev/null 2>&1; then
  fail 'returns success when Hermes rejects the enable'
fi
if grep -qi 'RESTART HERMES DESKTOP' "$test_dir/out"; then
  fail 'claims the install finished after a failed enable'
fi

# --- incomplete sources are refused before anything is written --------------

fixture="$test_dir/incomplete"
mkdir -p "$fixture/plugins/provider-limits/desktop"
cp "$script" "$fixture/setup_hermes_tools.sh"
cp -R "$root_dir/plugins/comp-count" "$fixture/plugins/comp-count"
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