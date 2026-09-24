#!/usr/bin/env bash
# Unattended board coordinator: a monitor-gated cron job with a closed charter.
#
#   kanban-coordinator.sh up       install the detector, render the prompt, create the job
#   kanban-coordinator.sh down     remove the job and the installed detector
#   kanban-coordinator.sh status   the job (if any) and the detector's current output
#   kanban-coordinator.sh prompt   print the rendered prompt (for cronjob_manage)
#   kanban-coordinator.sh drill    prove the detector's tokens on synthetic boards
#
# Reads .kanban/config.env (board, gate, base branch, language, schedule) and the
# notify env for the delivery target. The prompt comes from the repo's
# <templates>/coordinator.md, so the job can be rebuilt from the repo alone.
# Job name: "<board> coordinator". Detector: ~/.hermes/scripts/kanban_monitor_<board>.py
set -euo pipefail
. "$(dirname "$0")/lib.sh"

CMD="${1:-}"
case "$CMD" in up|down|status|prompt|drill) ;; *) sed -n 2,13p "$0" >&2; exit 2 ;; esac

if [ "$CMD" = drill ]; then exec python3 "$KANBAN_SCRIPTS/kanban-monitor.py" --drill; fi

kanban_load_config
HH="${HERMES_HOME:-$HOME/.hermes}"
SLUG_SAFE="$(printf '%s' "$KANBAN_BOARD" | tr -c 'A-Za-z0-9_\n' '_')"
MONITOR_NAME="kanban_monitor_${SLUG_SAFE}.py"
MONITOR_PATH="$HH/scripts/$MONITOR_NAME"
JOB_NAME="$KANBAN_BOARD coordinator"

job_id() {
  python3 - "$HH/cron/jobs.json" "$JOB_NAME" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
jobs = d if isinstance(d, list) else d.get("jobs", d)
jobs = jobs.values() if isinstance(jobs, dict) else jobs
for j in jobs:
    if isinstance(j, dict) and j.get("name") == sys.argv[2]:
        print(j["id"]); break
PY
}

render_prompt() {
  if [ -n "${KANBAN_LAND_CHECKS:-}" ]; then
    KANBAN_LAND_CHECKS_TEXT="\`$KANBAN_LAND_CHECKS\`"
  else
    KANBAN_LAND_CHECKS_TEXT="no further suites"
  fi
  export KANBAN_LAND_CHECKS_TEXT
  KANBAN_LANGUAGE="${KANBAN_LANGUAGE:-English}"; export KANBAN_LANGUAGE
  local tpl="$KANBAN_REPO_ROOT/$KANBAN_TEMPLATES/coordinator.md"
  [ -f "$tpl" ] || kanban_die "missing $tpl (kanban-init.sh copies it)"
  kanban_render "$tpl"
}

case "$CMD" in
  prompt) render_prompt ;;

  status)
    ID="$(job_id)"
    if [ -n "$ID" ]; then echo "job: $ID ($JOB_NAME)"; else echo "job: none ($JOB_NAME)"; fi
    if [ -f "$MONITOR_PATH" ]; then echo "detector: $(python3 "$MONITOR_PATH")"; else echo "detector: not installed"; fi
    ;;

  up)
    [ -z "$(job_id)" ] || kanban_die "\"$JOB_NAME\" already exists ($(job_id)); run down first"
    TARGET="$(kanban_notify_target)"
    [ -n "$TARGET" ] || kanban_die "no notify target: fill $KANBAN_NOTIFY_FILE (KANBAN_NOTIFY_CHAT_ID)"
    python3 "$KANBAN_SCRIPTS/kanban-monitor.py" --drill >/dev/null \
      || kanban_die "detector drill failed — run: kanban-coordinator.sh drill"
    mkdir -p "$HH/scripts"
    # cron runs monitors only from ~/.hermes/scripts, so install a copy with the board baked in.
    python3 - "$KANBAN_SCRIPTS/kanban-monitor.py" "$MONITOR_PATH" "$KANBAN_BOARD" <<'PY'
import sys
src, dst, board = sys.argv[1:]
text = open(src).read().replace("BOARD = None  # set by", f"BOARD = {board!r}  # set by", 1)
open(dst, "w").write(text)
PY
    OUT="$(python3 "$MONITOR_PATH")"
    case "$OUT" in DONE:*) ;; *) kanban_die "installed detector printed \"$OUT\" — is the board readable?" ;; esac
    PROMPT="$(render_prompt)"
    SKILLS=()
    for s in ${KANBAN_COORDINATOR_SKILLS:-hermes-kanban-development}; do SKILLS+=(--skill "$s"); done
    hermes_cli cron create "${KANBAN_COORDINATOR_SCHEDULE:-every 10m}" "$PROMPT" \
      --name "$JOB_NAME" --deliver "$TARGET" --failure-deliver local \
      --monitor-script "$MONITOR_NAME" --workdir "$KANBAN_PRIMARY" --continuity \
      "${SKILLS[@]}" >/dev/null
    ID="$(job_id)"
    [ -n "$ID" ] || kanban_die "cron create reported success but no job named \"$JOB_NAME\" exists"
    echo "up: job $ID, detector $MONITOR_PATH -> $OUT"
    echo "note: the CLI cannot restrict toolsets; for terminal+file only, create it with cronjob_manage using 'kanban-coordinator.sh prompt'."
    ;;

  down)
    ID="$(job_id)"
    if [ -n "$ID" ]; then hermes_cli cron remove "$ID" >/dev/null && echo "removed job $ID"; else echo "no job named \"$JOB_NAME\""; fi
    if [ -f "$MONITOR_PATH" ]; then rm -f "$MONITOR_PATH" && echo "removed $MONITOR_PATH"; fi
    ;;
esac
