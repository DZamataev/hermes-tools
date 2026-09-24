# Hermes Kanban: multi-agent development on this repo

Per-repo runbook. Cards run as separate non-interactive Hermes workers, one role
per profile; the foreground (coordinator) session plans, feeds the board and
lands the results. Settings: `.kanban/config.env`.

<!-- Fill every <…>. Keep this file self-contained: no paths into other
checkouts, no model ids (they change with quota). -->

## When the board is worth it

A ticket goes on the board when it is AFK and its acceptance checks are
written. Design questions are resolved in the foreground. Anything needing the
operator's eyes, ears, hands or credentials becomes a blocked gate card.

## One writer per worktree

Writing cards run in a git worktree, never in the operator's checkout. Cards
sharing a worktree are chained: implement → review → fix, the next slice's
implement parented on the previous fix. Research cards that each add one new
file may run in the primary checkout without commits.

```bash
git worktree add -b <effort>/chain <worktree-root>/<effort> <base>
```

### Warm-up before the first card

<The exact commands a fresh checkout needs: install, binary downloads, engine
import, then the gate and every e2e suite. The first card waits for success.>

Workers do not inherit the interactive shell: <toolchain paths they need>.

### Ports and shared services

<Fixed ports the suites bind; why two e2e cards cannot run at once.>

## Role profiles

| Role | Profile | Template |
|---|---|---|
| implement | `<prefix>impl` | `<templates>/common.md` |
| research | `<prefix>impl` | `<templates>/research.md` |
| adversarial review | `<prefix>review` | `<templates>/review.md` |
| remediation | `<prefix>fix` | `<templates>/fix.md` |

Rule: the reviewer runs on a different model family than the author; when
quota forces one family, the review is a second pass and the hand-off says so.
Check: `HERMES_HOME=~/.hermes/profiles/<p> hermes config get model.default`.

## Creating cards

```bash
K=${KANBAN_SCRIPTS:-${HERMES_HOME:-$HOME/.hermes}/skills/software-development/hermes-kanban-development/scripts}
python3 $K/kanban-chain.py --title "<slice>" --task <templates>/tasks/<ticket>.md --workdir <worktree> [--after <prev-fix>] [--gate-task <gate.md>]
bash $K/kanban-card.sh research "<title>" <templates>/tasks/<ticket>.md <repo>
```

## The gate

`<gate command>` — green means <success criterion>. Extra suites and when a
card runs them: <…>.

## Traps specific to this repo

- <each pitfall that already cost someone debugging time, in a worker's terms>

## What agents cannot check here

- <route each to an operator gate card>

## Standing authorizations

<Permissions the operator granted, with what they do NOT cover.>

## Calibration

<After the first chain: wall time per implement / review / fix, findings per
review, retries per card.>
