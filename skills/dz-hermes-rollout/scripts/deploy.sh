#!/usr/bin/env bash
# Step 3/3. Update the live install to the verified integration branch and restart what runs it:
# fast-forward, Python deps, desktop rebuilt from the install into the app bundle, the optional
# post-deploy hook, gateway restart, a restart of backends other machines' Desktops started here
# over SSH, app relaunch.
#
#   deploy.sh --dry-run     plan only
#   deploy.sh               deploy (refuses unless verify.sh recorded the current branch sha)
#   deploy.sh --push        also push the branch from the fork first — ONLY on the operator's word
#   (refuses inside Hermes, but --dry-run: it prints the command for the operator to run from a terminal)
#   --no-relaunch           leave the app closed        --unverified   skip the verified-sha check
#   --ignore-live-turns     deploy although a Hermes window it closes has a turn running
. "$(dirname "$0")/lib.sh"

dry_run=false push=false relaunch=true unverified=false ignore_live=false
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=true ;;
    --push) push=true ;;
    --no-relaunch) relaunch=false ;;
    --unverified) unverified=true ;;
    --ignore-live-turns) ignore_live=true ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "deploy: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

step "preconditions"
# An agent hands the operator the command instead; the operator closes Hermes once its chats and
# subagents are done and runs it from a terminal.
cmd="$(cd "$(dirname "$0")" && pwd)/deploy.sh"
$push && cmd+=" --push"; $relaunch || cmd+=" --no-relaunch"; $unverified && cmd+=" --unverified"
if inside_hermes && ! $dry_run; then
  echo "Run from a terminal outside Hermes after closing Hermes (Cmd+Q):"
  echo "  $cmd"
  fail "deploy is run by the operator, not from inside Hermes (an agent session or its terminal pane)"
fi
require_config
[ -e "$INSTALL/.git" ] || fail "ROLLOUT_INSTALL is not a git checkout: $INSTALL"
[ -x "$HB" ] || fail "install launcher missing: $HB"
require_branch_clean "$FORK"
require_branch_clean "$INSTALL"
new=$(git -C "$FORK" rev-parse "$BRANCH")
old=$(git -C "$INSTALL" rev-parse HEAD)
if ! $unverified; then
  if [ -f "$VERIFIED" ] && [ "$(cut -d' ' -f1 "$VERIFIED")" = "$new" ]; then
    echo "verified: $(cat "$VERIFIED")"
  else
    msg="$BRANCH $(git -C "$FORK" rev-parse --short "$new") is not verified (run verify.sh; last: $(cat "$VERIFIED" 2>/dev/null || echo none))"
    if $dry_run; then echo "WARNING: $msg"; else fail "$msg"; fi
  fi
fi
# Deploy quits every Hermes window, and an app quit kills its running turns and background
# subagents (the gateway restart drains its own), and so does stopping an SSH backend. Check every home with a window or an SSH
# backend open: the live one and the test instance's.
live=""
owners=""
live_app_running && owners="live app"
nssh=$(ssh_backends | wc -l | tr -d ' ')
[ "$nssh" -gt 0 ] && owners="${owners:+$owners, }ssh backend"
# Background delegate_task children outlive their parent's turn, so live_turns alone misses them.
in_home() { { live_turns "$1"; live_subagents "$1"; } | sed "s/^/$2: /"; }
# The live app and the SSH backends share the live home and its logs: check it once.
[ -n "$owners" ] && live+=$(in_home "$LIVE_HOME" "$owners")$'\n'
test_instance_running && live+=$(in_home "$TEST_HOME/home" "test instance")
live=$(echo "$live" | sed '/^$/d')
if [ -n "$live" ]; then
  echo "running turns:"; echo "$live" | sed 's/^/  /'
  msg="$(echo "$live" | wc -l | tr -d ' ') turn(s)/subagent(s) still running in a Hermes window deploy would close"
  if $dry_run || $ignore_live; then echo "WARNING: $msg"; else fail "$msg (wait for them, or --ignore-live-turns)"; fi
fi
git -C "$INSTALL" fetch -q "$FORK" "$BRANCH" || fail "fetch of $FORK/$BRANCH into $INSTALL failed"
git -C "$INSTALL" merge-base --is-ancestor HEAD FETCH_HEAD \
  || fail "$INSTALL has commits that $FORK/$BRANCH lacks — merge them into the fork first"
echo "install $(git -C "$INSTALL" rev-parse --short "$old") → $(git -C "$FORK" rev-parse --short "$new"), $(git -C "$FORK" rev-list --count "$old..$new") commit(s)"

if $dry_run; then
  $push && echo "would run: git -C $FORK push $PUSH_REMOTE $BRANCH"
  echo "would: quit Hermes, fast-forward $INSTALL, sync deps (launcher), rebuild desktop into $APP$([ -n "$POST_DEPLOY" ] && echo ", run the post-deploy hook")$([ "$RESTART_GATEWAY" = 1 ] && echo ", restart gateway")$([ "$nssh" -gt 0 ] && echo ", restart $nssh SSH backend(s)")$($relaunch && echo ", open $APP")"
  inside_hermes && echo "the operator runs, from a terminal outside Hermes after closing it: $cmd"
  ok "dry run"
  exit 0
fi

if $push; then
  step "push fork"
  git -C "$FORK" push "$PUSH_REMOTE" "$BRANCH" || fail "git push $PUSH_REMOTE $BRANCH failed (nothing deployed)"
fi

# Quit first: the source-update completion below skips replacing a running app, and any hermes
# launch after the fast-forward triggers it. Every Hermes window closes.
step "quit Hermes Desktop"
quit_app "$APP"
"$(dirname "$0")/verify.sh" --stop | tail -1   # the test instance runs with an extra argv; quit_app misses it
quit_app "$(release_app "$FORK")"
quit_app "$(release_app "$INSTALL")"

step "sync $INSTALL"
if [ "$old" != "$new" ]; then
  git -C "$INSTALL" merge --ff-only FETCH_HEAD || fail "fast-forward of $INSTALL failed"
fi
# The first launch after a fast-forward re-syncs the environment the gateway and the app run in.
"$HB" --version || fail "$HB --version failed after the fast-forward"

step "desktop build ($INSTALL → $APP)"
# The same completion `hermes update` runs: TUI + web UI + packaged desktop, then copies the
# bundle over $APP when its app.asar differs. No-op when the content stamp did not change.
(cd "$INSTALL" && "$HB" --run-module hermes_cli.source_completion --source "$INSTALL" --finish-update --desktop) \
  || fail "source completion (desktop build) failed"
built_app=$(release_app "$INSTALL")
built=$built_app/Contents/Resources/app.asar
[ -n "$built_app" ] && [ -f "$built" ] || fail "no desktop build under $INSTALL/apps/desktop/release"
if ! cmp -s "$built" "$APP/Contents/Resources/app.asar"; then
  echo "$APP differs from the build — copying"
  rm -rf "$APP.new"
  ditto "$built_app" "$APP.new" || fail "could not copy the build to $APP.new"
  rm -rf "$APP" && mv "$APP.new" "$APP" || fail "could not install the build into $APP"
fi
echo "$APP matches the build of $(git -C "$INSTALL" rev-parse --short HEAD)"
git -C "$INSTALL" checkout -- package-lock.json 2>/dev/null || true

if [ -n "$POST_DEPLOY" ]; then
  step "post-deploy hook"
  HERMES_BIN=$HB HERMES_INSTALL=$INSTALL bash -c "$POST_DEPLOY" || fail "post-deploy hook failed: $POST_DEPLOY"
fi

if [ "$RESTART_GATEWAY" = 1 ]; then
  step "gateway restart"
  (cd "$INSTALL" && "$HB" gateway restart) || fail "gateway restart failed"
fi

# After the gateway, before the app reopens: SSH-started backends serve the old code until they
# stop; the Desktop that owns each respawns it on the next request.
if [ -n "$(ssh_backends)" ]; then
  step "restart SSH backends"
  stop_ssh_backends
fi

if $relaunch; then
  step "open $APP"
  open "$APP"
fi

ok "install $(git -C "$INSTALL" rev-parse --short HEAD)$($push && echo ", $BRANCH pushed")"
