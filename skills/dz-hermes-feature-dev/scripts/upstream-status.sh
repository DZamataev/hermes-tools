#!/usr/bin/env bash
# Where the fork and each feature worktree stand against upstream, and what upstream has shipped
# since. Read-only apart from `git fetch upstream` (skip it with --no-fetch).
#
#   upstream-status.sh [--fork DIR] [--worktrees DIR] [--branch NAME] [--upstream REF]
#                      [--log N|all] [--no-fetch]
#
# The sync point is never stored: it is the merge-base of the branch and the upstream ref, which
# the last `merge upstream/main` moved. A stored copy would only drift from it.
set -euo pipefail
FORK=${HERMES_FORK:-$HOME/dev/hermes/hermes-agent}
WT=${HERMES_WORKTREES:-$HOME/dev/hermes-wt}
BRANCH=develop
UP=upstream/main
LOG=40
FETCH=1
while [ $# -gt 0 ]; do
  case "$1" in
    --fork) FORK=$2; shift 2 ;;
    --worktrees) WT=$2; shift 2 ;;
    --branch) BRANCH=$2; shift 2 ;;
    --upstream) UP=$2; shift 2 ;;
    --log) LOG=$2; shift 2 ;;
    --no-fetch) FETCH=0; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "upstream-status.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
g() { git -C "$FORK" "$@"; }
[ -d "$FORK/.git" ] || [ -f "$FORK/.git" ] || { echo "not a git checkout: $FORK" >&2; exit 1; }
remote=${UP%%/*}
[ "$FETCH" = 1 ] && g fetch -q "$remote"
g rev-parse -q --verify "$UP" >/dev/null || { echo "no ref $UP in $FORK" >&2; exit 1; }

line() { g log -1 --format='%h  %cs  %s' "$1"; }
sync=$(g merge-base "$BRANCH" "$UP")
new_fp=$(g rev-list --first-parent --count "$sync..$UP")
new_all=$(g rev-list --no-merges --count "$sync..$UP")
echo "== $BRANCH vs $UP"
echo "synced to : $(line "$sync")"
echo "upstream  : $(line "$UP")"
echo "since     : $new_fp landed on $UP's first-parent line ($new_all non-merge commits)"

echo
echo "== feature worktrees under $WT (each syncs separately)"
g worktree list --porcelain | awk '/^worktree /{p=substr($0,10)} /^branch /{print p "\t" substr($0,19)}' |
while IFS=$'\t' read -r path br; do
  case "$path" in "$WT"/*) ;; *) continue ;; esac
  s=$(g merge-base "$br" "$UP")
  printf '%-28s %-44s synced %s, %s behind\n' "$(basename "$path")" "$br" \
    "$(g log -1 --format='%h %cs' "$s")" "$(g rev-list --first-parent --count "$s..$UP")"
done

[ "$new_fp" -gt 0 ] || exit 0
echo
echo "== what changed upstream since $(g log -1 --format=%h "$sync") (by area, % of files)"
g diff --dirstat=files,2 "$sync" "$UP" | sed 's/^/  /'
echo
if [ "$LOG" = all ]; then
  echo "== $UP first-parent log since the sync point"
  g log --first-parent --format='%h  %cs  %s' "$sync..$UP"
else
  echo "== $UP first-parent log since the sync point (newest $LOG; --log all for everything)"
  g log --first-parent --format='%h  %cs  %s' -n "$LOG" "$sync..$UP"
fi
