---
name: dz-clean-worktrees
description: "Use when git worktrees pile up on disk, or the operator asks which worktrees can be deleted under a folder."
version: 1.0.0
author: Denis Zamataev
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [git, worktree, cleanup, disk-space]
    related_skills: [dz-wrapup]
---

# Clean up finished worktrees

Finds linked git worktrees under a folder the operator names, shows which ones
hold only work that is already merged, and removes the ones the operator picks.

Scripts: `${HERMES_SKILL_DIR}/scripts/` (Hermes substitutes the installed
location; elsewhere they sit next to this file). Python 3 stdlib and `git` only.

| Script | Does |
|---|---|
| `wt_scan.py ROOT [--json] [--depth N] [--target PATTERN]` | read-only survey of every worktree whose directory is inside ROOT |
| `wt_scan.py --only PATH` | the same for the one worktree containing PATH |
| `wt_remove.py [--yes] [--keep-branch] PATH…` | re-checks each PATH, then removes it; dry run without `--yes` |

## Procedure

1. **Get the folder from the operator.** It is an input, never a guess: if the
   request did not name one, ask. Work only inside it — the scanner reports a
   worktree only when its own directory is under ROOT, whichever repository
   owns it.
2. **Scan.** `python3 ${HERMES_SKILL_DIR}/scripts/wt_scan.py <ROOT> --json > <scratch>/wt.json`.
   It fetches nothing: remote refs are as of the last fetch. A stale ref only
   errs towards *keeping* a worktree, so do not fetch unless the operator asks
   about a specific "unmerged" row.
3. **Present one numbered list in three groups**, in the order the scanner
   sorts them (`recommend` first, then size):
   - **recommended: delete** — `recommend: delete`: removable and lying where
     worktrees get forgotten (`placement: hidden` = inside its repository,
     e.g. `.claude/worktrees`, `.worktrees`, `tmp/`, or under a dot/tmp
     folder; `other` = anywhere else, including a worktree that is itself
     named `*-wt`);
   - **safe, but recommended: keep** — removable, but its parent folder ends
     with `-wt` (`placement: managed`, e.g. `~/dev/hermes-wt/<name>`): the
     operator keeps worktrees there on purpose. Listed so the operator can
     still pick them; never preselected, never in `all`;
   - **blocked** — one line each with its blocker.

   For each row of the first two groups, one block:
   - path, repository, size;
   - **what it was**: one line you write from `subjects` (the commits made in
     that worktree) and the branch name — the work, not the commit list;
   - created → last activity;
   - merged: how (`contained` / `rebased` / `squashed` / `content-in-target`),
     into which branch, when (`merged_at`);
   - pushed: `merged` = the integration branch on the remote has it;
     `merged-local` = only the local branch has it — say so plainly;
   - every entry of `warnings` verbatim (ignored `.env` files are lost with
     the directory).
   `no-commits` rows (a worktree that was created, received no commit of its
   own, and whose HEAD is already in an integration branch — typically an
   agent sandbox opened and abandoned) and `prunable` rows (directory already
   gone) can share one compact line each.
   After the list: the total size of the recommended group.
4. **Ask which to delete**, as an open question: numbers, ranges, `all` (=
   the recommended group only).
5. **Dry run, then remove**: `wt_remove.py <paths>`; if it matches the
   selection, `wt_remove.py --yes <paths>`. It re-analyzes each path first and
   refuses anything that changed since the listing.
6. **Report**: removed, refused with the reason, space freed, and the
   `restore branch` lines it printed.

## Rules

- **Removal goes through `wt_remove.py` only.** Never `rm -rf` a worktree
  directory: git keeps its admin entry and the branch stays checked out
  "elsewhere". Never `git push --delete`: remote branches are out of scope.
- **A kept row stays kept.** If the operator wants an unmerged or dirty
  worktree gone anyway, show what is lost (`git -C <path> status --short`,
  `git log <target>..<branch> --oneline`) and get an explicit yes for that
  path; then `git worktree remove --force <path>` from the main checkout and
  keep the branch.
- **`in use` names the processes.** A dev server or the agent's own shell
  sitting in the worktree blocks it. `cd` out of it first; stop servers only
  if the operator says so.
- Merge detection: HEAD reachable from an integration branch; otherwise every
  commit's patch is in it (rebase, cherry-pick); otherwise the whole diff is
  one commit there (squash); otherwise every file the branch changed is
  identical there. Integration branches default to `main master develop dev
  trunk dev/* release/* releases/* release-*` plus `origin/HEAD`; pass
  `--target` (repeatable) when a repository lands work elsewhere.
- The managed-folder suffix is `-wt`; `--managed-suffix <s>` changes it,
  `--managed-suffix ''` turns the rule off.

## Tests

`bash ${HERMES_SKILL_DIR}/tests/run.sh` builds throwaway repositories (merged,
squashed, cherry-picked, unmerged, dirty, idle, locked, busy, local-only
merge, ignored `.env`, deleted directory, outside ROOT, clean and unpushed
submodules, and each placement: managed `-wt` folder, worktree named `*-wt`,
inside the repository, under `tmp/`) and checks the classification, the
recommendation and the removal, including a change made between listing and
removal.
