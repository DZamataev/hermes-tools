#!/usr/bin/env bash
# Create one board card with the role's standing constraints prepended.
#
#   kanban-card.sh <impl|research|review|fix|gate> "<title>" <task-file|-> <workdir> [parent-id...]
#
# Reads .kanban/config.env of the repo you run it from (or KANBAN_REPO).
# Env: KANBAN_BLOCKED=1 creates the card blocked (gate cards always are).
#      KANBAN_SKILLS="a b" force-loads skills into the worker.
#      KANBAN_NOTIFY_SESSION=0 skips subscribing the calling session.
# Body = <templates>/<role template> with {{…}} filled + the task file.
# Subscribes the card to the notify target when one is configured, and to the
# calling desktop/TUI session (HERMES_SESSION_KEY) so an orchestrator hears back.
# Prints the new card id and nothing else on stdout.
set -euo pipefail
. "$(dirname "$0")/lib.sh"

if [ $# -lt 4 ]; then sed -n 2,14p "$0" >&2; exit 2; fi
ROLE="$1"; TITLE="$2"; TASK="$3"; WORKDIR="$4"; shift 4
kanban_load_config
export WORKDIR

P="$KANBAN_PROFILE_PREFIX"
BLOCKED="${KANBAN_BLOCKED:-0}"
case "$ROLE" in
  impl)     TPL=common.md;   WHO="${P}impl";   RETRIES="${KANBAN_RETRIES_IMPL:-2}" ;;
  research) TPL=research.md; WHO="${P}impl";   RETRIES="${KANBAN_RETRIES_RESEARCH:-1}" ;;
  review)   TPL=review.md;   WHO="${P}review"; RETRIES="${KANBAN_RETRIES_REVIEW:-1}" ;;
  fix)      TPL=fix.md;      WHO="${P}fix";    RETRIES="${KANBAN_RETRIES_FIX:-2}" ;;
  gate)     TPL=gate.md;     WHO="";           RETRIES=1; BLOCKED=1 ;;
  *) kanban_die "role must be impl|research|review|fix|gate" 2 ;;
esac

[ -d "$WORKDIR" ] || kanban_die "workdir does not exist: $WORKDIR" 2
case "$WORKDIR" in /*) ;; *) kanban_die "workdir must be absolute: $WORKDIR" 2 ;; esac
TEMPLATE="$KANBAN_REPO_ROOT/$KANBAN_TEMPLATES/$TPL"
[ -f "$TEMPLATE" ] || kanban_die "role template missing: $TEMPLATE (kanban-init.sh copies them)" 2
if [ "$TASK" != "-" ] && [ ! -f "$TASK" ]; then kanban_die "task file does not exist: $TASK" 2; fi
if [ "$ROLE" = gate ] && [ "$TASK" = "-" ]; then kanban_die "a gate needs a task file with its questions" 2; fi

BODY="$(kanban_render "$TEMPLATE")"
if [ "$TASK" != "-" ]; then
  BODY="$BODY
$(cat "$TASK")"
fi

ARGS=(--workspace "dir:$WORKDIR" --max-retries "$RETRIES")
[ -n "$WHO" ] && ARGS+=(--assignee "$WHO")
[ -n "${KANBAN_MAX_RUNTIME:-}" ] && [ "$ROLE" != gate ] && ARGS+=(--max-runtime "$KANBAN_MAX_RUNTIME")
for s in ${KANBAN_SKILLS:-}; do ARGS+=(--skill "$s"); done
for p in "$@"; do
  [ -n "$p" ] || kanban_die "empty parent id — a card with an unresolved parent is dispatched at once" 2
  ARGS+=(--parent "$p")
done
[ "$BLOCKED" = 1 ] && ARGS+=(--initial-status blocked)

OUT="$(printf '%s\n' "$BODY" | hermes_cli kanban --board "$KANBAN_BOARD" create "$TITLE" --body-file - "${ARGS[@]}" --json)" \
  || kanban_die "hermes kanban create failed for \"$TITLE\""
# The id is under `id` (not task_id); a missing one means nothing usable was created.
ID="$(printf '%s' "$OUT" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("id") or "")
except Exception: print("")')"
[ -n "$ID" ] || kanban_die "create returned no id for \"$TITLE\": $OUT"

# --json skips auto-subscribe, so subscribe explicitly.
if [ -n "${KANBAN_NOTIFY_CHAT_ID:-}" ]; then
  SUB=(--platform "${KANBAN_NOTIFY_PLATFORM:-telegram}" --chat-id "$KANBAN_NOTIFY_CHAT_ID"
       --user-id "${KANBAN_NOTIFY_USER_ID:-$KANBAN_NOTIFY_CHAT_ID}" --delivery-mode notify)
  if [ -n "${KANBAN_NOTIFY_THREAD_ID:-}" ]; then
    SUB+=(--thread-id "$KANBAN_NOTIFY_THREAD_ID" --chat-type thread)
  else
    SUB+=(--chat-type dm)
  fi
  hermes_cli kanban --board "$KANBAN_BOARD" notify-subscribe "$ID" "${SUB[@]}" >/dev/null \
    || printf 'kanban-card: %s created but notify-subscribe failed\n' "$ID" >&2
fi
# ...and the orchestrating session itself, so its blocks/completions reach it.
SESSION="$(kanban_session_key)"
if [ -n "$SESSION" ]; then
  hermes_cli kanban --board "$KANBAN_BOARD" notify-subscribe "$ID" --platform tui --chat-id "$SESSION" \
    --delivery-mode notify >/dev/null \
    || printf 'kanban-card: %s created but the session subscription failed\n' "$ID" >&2
fi
echo "$ID"
