#!/usr/bin/env bash
# Step 1/3. Merge into the fork's integration branch: optionally the upstream branch first, then
# every feature-worktree branch with commits ahead of it. Touches nothing but the fork checkout.
#
#   merge.sh --dry-run      list what would be merged
#   merge.sh                merge the worktree branches
#   merge.sh --upstream     fetch the upstream remote first and merge its branch, then the worktrees
#
# A conflict aborts that merge (`git merge --abort`), keeps the merges before it and stops:
# resolve it by hand in the fork (git merge <branch>), commit, rerun.
. "$(dirname "$0")/lib.sh"

dry_run=false upstream=false
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=true ;;
    --upstream) upstream=true ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "merge: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

step "preconditions"
require_config
require_branch_clean "$FORK"

if $upstream; then
  step "$UPSTREAM → $BRANCH"
  git -C "$FORK" fetch -q "${UPSTREAM%%/*}" || fail "git fetch ${UPSTREAM%%/*} failed"
  n=$(git -C "$FORK" rev-list --count "$BRANCH..$UPSTREAM")
  echo "$UPSTREAM is $n commit(s) ahead of $BRANCH"
  if [ "$n" -gt 0 ] && ! $dry_run; then
    if ! git -C "$FORK" merge --no-ff --no-edit -m "Merge $UPSTREAM into $BRANCH" "$UPSTREAM"; then
      git -C "$FORK" merge --abort || true
      fail "merge conflict: $UPSTREAM into $BRANCH (aborted; resolve by hand in $FORK)"
    fi
  fi
fi

step "worktree branches → $BRANCH"
main_wt=$(git -C "$FORK" rev-parse --show-toplevel)
to_merge=()
while IFS=$'\t' read -r path branch; do
  [ "$path" = "$main_wt" ] && continue
  if [ -n "$WT_ROOT" ]; then case $path in "$WT_ROOT"/*) ;; *) continue ;; esac; fi
  [ -n "$branch" ] || { echo "skip $path: detached HEAD"; continue; }
  [ "$branch" = "$BRANCH" ] && continue
  [ -d "$path" ] || { echo "skip $branch: worktree dir $path is gone (prunable)"; continue; }
  ahead=$(git -C "$FORK" rev-list --count "$BRANCH..$branch")
  if [ "$ahead" -eq 0 ]; then echo "up to date: $branch"; continue; fi
  if [ -n "$(git -C "$path" status --porcelain --untracked-files=no)" ]; then
    echo "WARNING: $branch has uncommitted changes in $path — only its commits are merged"
  fi
  echo "to merge: $branch (+$ahead)"
  to_merge+=("$branch")
done < <(git -C "$FORK" worktree list --porcelain | awk '
  /^worktree /{p=substr($0,10); b=""} /^branch /{b=substr($0,19)} /^$/{if(p!="") print p "\t" b; p=""}
  END{if(p!="") print p "\t" b}')

if $dry_run; then
  ok "dry run, nothing merged; ${#to_merge[@]} branch(es) to merge: ${to_merge[*]:-none}"
  exit 0
fi
for branch in ${to_merge[@]+"${to_merge[@]}"}; do
  if ! git -C "$FORK" merge --no-ff --no-edit -m "merge: $branch into $BRANCH" "$branch"; then
    git -C "$FORK" merge --abort || true
    fail "merge conflict: $branch into $BRANCH (aborted; earlier merges kept)"
  fi
  echo "merged: $branch"
done

if [ "${ROLLOUT_SKIP_SMOKE:-0}" != 1 ]; then
  step "import smoke ($FORK)"
  smoke "$FORK" fork_env python
fi

ok "$BRANCH at $(git -C "$FORK" rev-parse --short HEAD), merged ${#to_merge[@]} branch(es): ${to_merge[*]:-none}; next: verify.sh"
