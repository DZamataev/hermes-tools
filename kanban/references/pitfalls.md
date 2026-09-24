# Pitfalls

Short rules that come from real board runs. One rule per item: the trap, then
what to do. `SKILL.md` has the method; this file is for looking up one problem.

## Board CLI

- **`create --json` returns the id under `id`, not `task_id`.** Reading the
  wrong key gives `None`, `--parent None` quietly creates a card with no parent,
  and it gets dispatched straight away. Print every parsed id and stop if one is
  empty.
- **A card whose parent did not resolve runs at once.** A card is never held
  as "unlinked". Create every card `--initial-status blocked`, link all edges,
  read `parents:` back, then `unblock` the head only (`kanban-chain.py` does this).
- **`link` takes `parent_id child_id` positionally.** If you swap them, the
  parent waits on its own child. Read `show <child>` after each link.
- **Archiving a card releases its children.** An archived parent no longer
  counts as unmet. After any `archive`, re-parent the orphans.
- **Do not keep the board command in a shell variable.** zsh (the terminal
  tool, cron) does not word-split `$B`, so each call fails while the script
  exits 0. Use a function or write the command out in full.
- **Under `set -u`, pass a list that may be empty as `${a[@]+"${a[@]}"}`.**
- **Send card bodies on stdin (`--body-file -`).** Backticks and leading dashes
  get mangled when the body is an argument.
- **`--workspace` needs its kind prefix:** `dir:<abs>`, `worktree:<abs>`,
  `scratch`.
- **`comment <id> "<text>"` is positional.** `--body` is rejected with the
  top-level usage dump.
- **`HERMES_DELEGATED_CHILD_CONTEXT=1` blocks board writes.** A gateway-spawned
  coordinator can inherit it. `unset` it before you mutate the board.
- **Guessed flag names fail with an empty id.** Take the settings from a
  finished sibling instead (`show <id> --json`: `assignee`, `workspace_*`,
  `max_retries`, `completion_contract`).

## Profiles and models

- **`profile create --clone-from` copies `MEMORY.md` wholesale.** A cloned
  reviewer is not blind until you rewrite its memory down to its role
  (`kanban-profiles.sh`). `wc -c` on the memory file shows what was inherited.
- **The dispatcher silently fails on an unknown assignee name.** Create the
  profiles before the cards, and fix a description with
  `hermes profile describe <p> --text "…"`.
- **A claimed card keeps the model it started with, and `assign` refuses a
  running card.** Pin models before the first `dispatch`.
- **A config change applies at the next spawn.** Never kill a worker to make a
  setting take effect.
- **`profile show` prints the inherited `base_url`.** Use
  `config get model.default` for what is configured and the
  `OpenAI client created` line in `agent.log` for where calls actually went.
  That log line only appears after a real run.
- **Per-card `set-model` overrides and the global fallback chain both beat the
  profile.** Check `model_override` in `list --json` and look for
  `Fallback activated` in the logs.
- **Two sizes of one vendor's model are not independent reviewers.** When
  quota forces a single family, say that the review is a second pass.
- **The one-shot flag is top-level:** `hermes -p <p> -z "<prompt>"`.

## Workspaces

- **One writer per tree.** Chain the cards that share a workspace. A read-only
  review may run in parallel with unrelated work.
- **A card created by a worker gets a `scratch` workspace.** Check the
  `workspace:` of cards created by workers before they are claimed.
- **A new worktree is not a working environment.** Install dependencies, run
  one full build and the full suite there before the first dispatch. Network
  fetches at build time and tracked-looking files hidden by ignore rules
  surface only there.
- **Gitignored plans do not travel with the branch.** Copy them into the
  worktree explicitly.
- **Build outputs have one writer too.** Before you build in a tree, check that
  no worker is compiling (`pgrep -fl 'xcodebuild|gradle|tsc|vite'`).
- **To measure while a worker holds the tree,** use
  `git worktree add --detach <path> <sha>`.
- **A dead worker leaves `index.lock` behind.** Delete it only after `lsof` on
  the lock shows nothing and its mtime matches a run that has ended.
- **Long-lived build daemons serve the module graph they started with.** After
  a merge or reinstall, restart them before you diagnose a missing module.
- **An unresolved merge in a tree blocks your commits too.** Edit
  orchestration docs in the primary checkout.
- **Re-read shared docs right before you edit them.** Workers change the plan
  and the map as they go.
- **Workers do not inherit your shell.** Export toolchain roots in the card
  body.

## Card bodies

- **A body is frozen at creation.** If a queued card's inputs moved, archive it
  and recreate it with edges and a subscription. Comment only on a card that is
  already running. If a correcting comment would run longer than the section it
  corrects, recreate the card.
- **Long-lived cards collect stale claims.** Before convergence and final-gate
  cards become eligible, re-read their bodies against reality. An obsolete
  "X is unavailable" is an active instruction.
- **A root cause stated in a body is a hypothesis.** Ask the worker to prove
  which step introduces the behaviour before changing it, and say that
  falsifying the hypothesis is a valid outcome.
- **A card blocked on hardware can still do the host-side half.** Say so in the
  body.
- **Give definition constants; let the worker measure thresholds.** Name the
  existing epsilon or tolerance by symbol and value, checked in the source.
- **Do deterministic input prep in the foreground session.** Leave the
  expensive, judgement-heavy runs to cards.
- **Research subagents must not start other agent CLIs.** Say
  "do the work yourself" in their context.

## Review and fix

- **The reviewer cannot know its commit range at creation.** Have it read
  `START_HEAD..END_HEAD` from its parent's summary and state the range it
  reviewed.
- **A finding about what behaviour *should* be goes to the operator.** Do not
  apply it as a fix. A finding that contradicts a written decision always goes
  there.
- **A dismissal citing a decision record is a claim.** Read what the record
  actually promises. A silent record authorizes nothing.
- **A closed finding is one instance.** For a symptom an operator would report,
  fix every producer of it.
- **Check an "out of scope" finding against the base branch.** A defect older
  than the branch is cheapest to fix now.
- **Reversing a decision touches source, tests, changelog, spec and any guard
  test.** Grep for the decision's distinguishing value.
- **A harness the gate relies on needs its own detection proof.** Sabotage the
  code it measures, confirm each scenario places its load, and have the operator
  watch the scenarios once.
- **A measurement taken before a fix no longer describes the code.**
  Re-measure, prove a rebuild happened, and re-parent the gate.

## Operator, gates, blocks

- **A blocked gate is the operator's by design.** Unblocking it hands it to a
  worker that will "complete" it alone.
- **Gate cards need absolute paths, one copy-pasteable command and numbered
  questions.** The operator reads them from a notification.
- **A verdict splits into three parts:** accepted parts, a new design rule
  (docs commit now), and defects (a new chain plus a new gate). Then archive
  the old gate.
- **Approval lifts a prohibition; it does not mean now.** Check the tree is
  clean before you tag or push.
- **Record the operator's version scheme verbatim** in the publish gate.
- **Verify external side effects from the far side** (`git ls-remote`, the
  consumer manifest).
- **A block reason and an operator's stall diagnosis are hypotheses.** Re-run
  the quoted check, read its whole output, and read the board before any
  remedy.
- **Tell quota errors from transport errors.** 429 is not `APITimeoutError`.
- **An informational unblock comment must say "verify independently".**
- **A worker cannot edit write-protected files** (`AGENTS.md` and peers). Make
  the edit yourself, then unblock with "do not re-edit".
- **A shared device is a workspace.** Block the card while the operator holds
  it, and parent cards across trees that deploy to the same device. Pass device
  IDs to use *and* to ignore.
- **A standing authorization is recorded with what it does not cover.**
- **Once the operator answers every few minutes, stop carding iterations.**
  Tune in the foreground and card only the final verdict.

## Running, stopping, observing

- **`hermes pause` is global and stops dispatch only.** Also comment the stop
  on the running card: commit what exists, then `kanban_block` with the resume
  point. After a restart, the dispatcher reruns the card from its body.
- **You cannot attach to a running worker.** Use `kanban tail`, `runs`, the
  profile's `agent.log`, and `sessions export` once it ends.
- **Let the dispatcher reclaim dead workers.** A manual `kill` races it.
- **Answer "is there a UI?" from the docs.** Do not start servers on the
  operator's machine.
- **Check `created:` before blaming a worker for a card.** The operator
  creates cards too.
- **Evidence directories and per-checkout build trees fill the disk.** Retire
  proof artifacts when a card closes.

## Notifications

- **`create --json` skips auto-subscribe.** Subscribe explicitly and check
  `notify-list`.
- **Truncation depends on the event:** completed gives the first line of the
  summary (~200 chars), blocked the reason (~160), and crashed/timed_out no
  text. Front-load block reasons. Keep completion summaries neutral with a
  mechanical first line.
- **Subscribing a card to a new chat does not remove the old one.**
  Unsubscribe the old one too and count targets.
- **Keep chat ids out of the repo.** Secret scanners do not catch bare numeric
  ids.

## Reachability

- **Green cards do not mean the feature is reachable.** Grep the entry point,
  check the flag default, and trace eligibility guards to whatever produces
  their inputs.
- **A kill-switch is not an A/B tool.** Card a diagnostics panel that reads the
  same selection result the pipeline executes, and log it.
- **Before you apply a config edit for operator testing, expect a red test
  that asserts the shipped default.** Do not let a worker "fix" that test.
