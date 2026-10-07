#!/usr/bin/env bash
# Step 2/3. Verify the fork's integration branch before it reaches the live install:
#   1. import smoke with the fork's environment;
#   2. tests: every tests/**.py changed between the install's commit and the branch (what would
#      ship); failures are re-run alone, then on the upstream commit the branch last merged — only
#      failures upstream does not have count as ours;
#   3. test instance: builds the fork's desktop app and starts it with a backend from the fork and
#      an isolated home (seeded from the live home without messaging platforms, cron, the API
#      server or auth.json) next to the live app. The operator then checks it by hand.
#
#   verify.sh               all of the above
#   verify.sh --full        whole test suite instead of the changed files
#   verify.sh --since REF   test files changed since REF instead of since the install's commit
#   verify.sh --no-tests    skip 2 (app only)       verify.sh --no-app   skip 3
#   verify.sh --reseed      re-copy config.yaml/.env/SOUL.md into the test home before starting
#   verify.sh --stop        stop the test instance and exit
#
# On success writes "<sha> ..." to the verified stamp — deploy.sh refuses any other sha.
. "$(dirname "$0")/lib.sh"

tests=changed app=true reseed=false since=""
prev=""
for arg in "$@"; do
  if [ "$prev" = --since ]; then since=$arg; prev=""; continue; fi
  prev=$arg
  case $arg in
    --since) ;;
    --full) tests=full ;;
    --no-tests) tests=none ;;
    --no-app) app=false ;;
    --reseed) reseed=true ;;
    --stop) ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "verify: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

stop_instance() {
  test_instance_running || return 0
  pkill -TERM -f -- "/Contents/MacOS/Hermes $MARK" || true
  for _ in $(seq 1 30); do test_instance_running || return 0; sleep 1; done
  fail "test instance did not quit within 30 s"
}
for arg in "$@"; do [ "$arg" = --stop ] && { stop_instance; ok "test instance stopped"; exit 0; }; done

step "preconditions"
require_config
require_branch_clean "$FORK"
[ -e "$INSTALL/.git" ] || fail "ROLLOUT_INSTALL is not a git checkout: $INSTALL"
SHA=$(git -C "$FORK" rev-parse HEAD)
BASE=${since:-$(git -C "$INSTALL" rev-parse HEAD)}
git -C "$FORK" cat-file -e "$BASE^{commit}" 2>/dev/null || fail "base commit $BASE is not in $FORK (fetch the install's commits into the fork, or pass --since)"
echo "$BRANCH $(git -C "$FORK" rev-parse --short "$SHA"); install $(git -C "$INSTALL" rev-parse --short HEAD); tests changed since $(git -C "$FORK" rev-parse --short "$BASE") ($(git -C "$FORK" rev-list --count "$BASE..$SHA") commit(s))"

step "import smoke"
smoke "$FORK" fork_env python

# ── tests ─────────────────────────────────────────────────────────────────────────────────────
failed_ids() { grep -E '^FAILED tests/' "$1" | sed -E 's/^FAILED ([^ ]+).*/\1/' | sort -u; }
failed_files() { grep -oE '^  tests/[^ ]+\.py' "$1" | sed 's/^  //' | sort -u; }
ours=""
if [ "$tests" != none ]; then
  step "tests ($tests)"
  LOG=$STATE_DIR/verify-tests-fork.log
  files=()
  if [ "$tests" != full ]; then
    while IFS= read -r f; do
      [ -f "$FORK/$f" ] && case $(basename "$f") in test_*.py|*_test.py) files+=("$f") ;; esac
    done < <(git -C "$FORK" diff --name-only "$BASE" "$SHA" -- 'tests/*.py')
    echo "${#files[@]} changed test file(s)"
  fi
  if [ "$tests" = full ] || [ ${#files[@]} -gt 0 ]; then
    rc=0; fork_env scripts/run_tests.sh ${files[@]+"${files[@]}"} >"$LOG" 2>&1 || rc=$?
    grep -E '=== Summary' "$LOG" | tail -1 || true
    if [ $rc -ne 0 ]; then
      ffiles=$(failed_files "$LOG")
      [ -n "$ffiles" ] || fail "test runner exited $rc without a failure list — see $LOG"
      # The parallel run has load-dependent flakes: re-run each failing file alone first.
      step "re-run $(echo "$ffiles" | wc -l | tr -d ' ') failing file(s) one by one"
      RLOG=$STATE_DIR/verify-tests-fork-rerun.log
      : >"$RLOG"
      for f in $ffiles; do fork_env scripts/run_tests.sh "$f" >>"$RLOG" 2>&1 || true; done
      LOG=$RLOG
      ffiles=$(failed_files "$LOG")
      if [ -z "$ffiles" ]; then echo "all pass alone: parallel-run flakes"; fi
    fi
    if [ $rc -ne 0 ] && [ -n "$ffiles" ]; then
      ULOG=$STATE_DIR/verify-tests-upstream.log
      : >"$ULOG"
      if UP=$(git -C "$FORK" merge-base "$SHA" "$UPSTREAM" 2>/dev/null); then
        step "re-run $(echo "$ffiles" | wc -l | tr -d ' ') failing file(s) on upstream $(git -C "$FORK" rev-parse --short "$UP")"
        WT=$TEST_HOME/baseline-wt
        git -C "$FORK" worktree remove --force "$WT" 2>/dev/null || true
        git -C "$FORK" worktree prune
        git -C "$FORK" worktree add -q --detach "$WT" "$UP"
        upfiles=()
        for f in $ffiles; do [ -f "$WT/$f" ] && upfiles+=("$f"); done
        if [ ${#upfiles[@]} -gt 0 ]; then
          (cd "$WT" && bash -c '. ./activate -- >/dev/null 2>&1; scripts/run_tests.sh "$@"' _ "${upfiles[@]}") >"$ULOG" 2>&1 || true
        fi
        git -C "$FORK" worktree remove --force "$WT"
      else
        echo "no $UPSTREAM ref: every failure counts as ours"
      fi
      ours=$(comm -23 <(failed_ids "$LOG") <(failed_ids "$ULOG"))
      # a failing file that fails without FAILED lines (collection error) and is not failing upstream
      for f in $ffiles; do
        grep -q "^FAILED $f" "$LOG" || failed_files "$ULOG" | grep -qx "$f" || ours="$ours"$'\n'"$f (collection/error)"
      done
      ours=$(echo "$ours" | sed '/^$/d')
      echo "failing on $BRANCH: $(failed_ids "$LOG" | wc -l | tr -d ' '), also on upstream: $(comm -12 <(failed_ids "$LOG") <(failed_ids "$ULOG") | wc -l | tr -d ' ')"
      if [ -n "$ours" ]; then
        echo "NOT on upstream (ours):"; echo "$ours" | sed 's/^/  /'
        fail "$(echo "$ours" | wc -l | tr -d ' ') test failure(s) introduced by $BRANCH — logs: $LOG, $ULOG"
      fi
      echo "every failure also fails on upstream — not ours"
    fi
  fi
fi

# ── test instance ─────────────────────────────────────────────────────────────────────────────
if $app; then
  step "desktop build ($FORK)"
  # Build-only never touches the live app. package-lock.json churn from npm install is reverted.
  fork_env python hermes desktop --build-only || fail "desktop build failed"
  git -C "$FORK" checkout -- package-lock.json 2>/dev/null || true
  TEST_APP=$(release_app "$FORK")
  [ -n "$TEST_APP" ] && [ -x "$TEST_APP/Contents/MacOS/Hermes" ] || fail "no built app under $FORK/apps/desktop/release"

  step "seed $TEST_HOME"
  H=$TEST_HOME/home
  mkdir -p "$H" "$TEST_HOME/desktop"
  if $reseed || [ ! -f "$H/config.yaml" ]; then
    # No messaging platforms / cron / API server: the live gateway owns the bot tokens and the port.
    # No auth.json: OAuth refresh tokens rotate; a second copy would log the live install out.
    fork_env python - "$LIVE_HOME/config.yaml" "$H/config.yaml" <<'PY'
import sys
import hermes_yaml as yaml  # the repo's YAML wrapper; PyYAML is not a dependency
cfg = yaml.safe_load(open(sys.argv[1])) or {}
for k in ("platforms", "cron"):
    cfg.pop(k, None)
yaml.safe_dump(cfg, open(sys.argv[2], "w"), sort_keys=False, allow_unicode=True)
PY
    grep -vE '^\s*(export\s+)?(TELEGRAM|DISCORD|SLACK|WHATSAPP|SIGNAL|MATRIX|MATTERMOST|WECOM|FEISHU|LARK|DINGTALK|WEIXIN|YUANBAO|TWILIO|SMS|EMAIL|HOMEASSISTANT|API_SERVER)_' \
      "$LIVE_HOME/.env" >"$H/.env" 2>/dev/null || true
    chmod 600 "$H/.env"
    [ -f "$LIVE_HOME/SOUL.md" ] && cp "$LIVE_HOME/SOUL.md" "$H/SOUL.md"
    echo "seeded config.yaml (no platforms/cron), .env (no messaging/API server keys), SOUL.md"
  else
    echo "kept existing seed (--reseed to refresh)"
  fi

  step "start test instance"
  stop_instance
  FORK_VENV=$(fork_env bash -c 'echo "${PYTHONPATH#*:}"'); FORK_VENV=${FORK_VENV%/lib/python*}
  [ -x "$FORK_VENV/bin/python" ] || fail "fork environment python not found: $FORK_VENV"
  ILOG=$TEST_HOME/desktop.log
  cd "$FORK" || fail "cannot enter $FORK"
  # No subshell around the `&`: a lingering subshell keeps the caller's stdout pipe open.
  env -u PYTHONPATH -u PYTHONHOME \
    HERMES_HOME="$H" HERMES_DESKTOP_USER_DATA_DIR="$TEST_HOME/desktop" \
    HERMES_DESKTOP_HERMES_ROOT="$FORK" HERMES_DESKTOP_PYTHON="$FORK_VENV/bin/python" \
    nohup "$TEST_APP/Contents/MacOS/Hermes" "$MARK" >"$ILOG" 2>&1 </dev/null &
  disown
  up=false
  for _ in $(seq 1 120); do
    test_instance_running || fail "test instance exited — see $ILOG"
    if pgrep -f "$FORK_VENV/bin/python -m hermes_cli.main serve" >/dev/null; then up=true; break; fi
    sleep 1
  done
  $up || fail "test instance started but no backend from $FORK within 120 s — see $ILOG"
  echo "test instance up: app $TEST_APP, backend $FORK ($FORK_VENV), home $H"
  echo "one-shot check: HERMES_HOME=$H $FORK_VENV/bin/python $FORK/hermes chat -Q -q ok"
fi

echo "$SHA $(date '+%F %T') tests=$tests app=$app" >"$VERIFIED"
ok "$BRANCH $(git -C "$FORK" rev-parse --short "$SHA") verified (tests=$tests, app=$app). Check the test instance by hand, then: deploy.sh [--push]; stop it with verify.sh --stop"
