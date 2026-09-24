# Supervising a long run with a change-detector, not a poll

The orchestrator session does not wake between operator messages, and the board
already pushes its own terminal events. So the supervision worth adding is the
one nobody reports: **silent stalls**. A cron job that merely re-reads the board
every N minutes duplicates notifications the operator already gets and burns a
model run per tick to say "still fine".

Use `cronjob_manage` with a `monitor` script instead. The scheduler runs the
script first, hashes its **exact bytes**, and skips the agent entirely when the
output is unchanged. A healthy board therefore costs one cheap script execution
per tick and zero tokens; the model wakes only when the health signature moves.

```
cronjob_manage(action="create",
  schedule="10m",
  monitor="<detector>.py",           # resolves under ~/.hermes/scripts/
  prompt="<self-contained investigation brief>",
  deliver="<platform>:<chat_id>",
  failure_deliver="local",           # keep the job's own errors out of the channel
  enabled_toolsets=["terminal"],     # the woken agent only needs to read the board
  workdir="<repo root>",
  attach_to_session=True)            # operator can reply to the alert in-thread
```

## Writing the detector

**Emit one stable token per condition, nothing else.** Any value that drifts on
its own — a timestamp, an elapsed time, a monotonic counter, an unsorted set —
makes every tick look changed and restores the per-tick model run you were
avoiding. Sort collections before joining them. Print a single `OK` for the
healthy case so the hash is constant across healthy ticks.

**Detect only what the board does not already push.** Completion and block events
already reach the operator. The conditions worth a detector are the ones with no
event behind them:

| Signal | Condition |
|---|---|
| `STALL` | work queued while nothing is running — the dispatcher is not advancing |
| `RETRIES:<id>` | a card's attempt count is climbing, i.e. one failure repeating |
| `DEVICE-GONE:<names>` | a bound device vanished while device cards are still open |
| `API-UNREACHABLE` | the backend the cards depend on stopped answering |
| `BOARD-UNREADABLE` | the board CLI itself failed |
| `ALL-DONE` | the queue drained |

`BLOCKED:<ids>` is worth including even though the board notifies once, because
the detector keeps it visible until the block is actually cleared.

**Probe a dependency with the same request shape the product uses.** A bare
health `GET` is not evidence the real path works: selective network interference
and misbehaving proxies pass a small anonymous request while holding a larger
authorized one open indefinitely. Send the method, headers and body shape the app
sends, or the detector will report a reachable backend while every worker hangs
against it.

**Gate environment-sensitive checks on relevance.** Device and network probes
should only run while cards needing them are still open, otherwise a finished
board alerts forever about an unplugged phone.

**Never hardcode a host/port/path the workspace configures for itself.** Per-
worktree tooling assigns its own ports (a bundler on a computed port per
checkout), so a probe pinned to the default port reports the service down on
every tick forever while the service is healthy. Read the value from the
worktree's own env/config file at probe time and skip the check when the file
does not declare one.

**A false alarm repeating across ticks is a defect in the detector, fix it the
same turn.** A signal the operator has already dismissed once trains them to skim
the alerts, which costs the real one. When a probe turns out to be wrong about
reality, patch the script immediately rather than explaining the discrepancy
again — and re-verify with the mutation drill above, since a hardcoded constant
passes that drill happily while pointing at the wrong target.

## Prove the detector fails correctly before trusting it

A detector that always prints `OK` is indistinguishable from a healthy system,
and its silence reads as good news for as long as the run lasts. Break each
condition in the real script, confirm that exact signal appears, and restore:

- force the status filter to match a state that is present → expect that signal
- empty the running-card list → expect `STALL`
- point a bound device id at a nonexistent one → expect `DEVICE-GONE`
- point the dependency URL at an unresolvable host → expect `API-UNREACHABLE`

Restore from the original source text after each mutation rather than editing the
mutation back by hand, and re-run once at the end to confirm the baseline token
returned.

**Drill the tokens by swapping the input, not by breaking the board.** Keep the
board read in one function (`board()`), copy the script, replace that function
with a synthetic row list per scenario (`[{'id':'a','status':'todo'}]` → `STALL`,
a raise → `BOARD-UNREADABLE`, a running row with `last_failure_error` →
`RETRYING`), and assert each expected token plus "healthy prints no alarm". It
runs in a second, touches no live card, and is re-runnable as a script after any
edit. Read the real field names off `hermes kanban list --json` first: there is
no attempt counter on a listed card, only `last_failure_error`, so a detector
keyed on a guessed `run_count` stays silent forever.

## Overnight: an acting coordinator instead of a read-only watcher

When the operator leaves for the night expecting progress, a read-only watcher
only tells them in the morning what they could have been told was mergeable at
3 a.m. Give the woken agent a **closed list of permitted writes** instead of a
read-only charter, and forbid everything else by name:

- permitted: re-run the repo gate and e2e in a finished chain's worktree, then
  merge that branch into the base branch locally (`--ff-only`, else a merge
  plus the gate again; abort on conflict and report); commit research outputs
  and map/ticket status updates; `unblock` once after a crash or host restart
  that the evidence confirms; `dispatch` once on `STALL`;
- forbidden: push, edit code, edit write-protected agent-policy files, create
  or archive cards, kill workers, change profiles or models, touch other boards.

Include `DONE:<n>` in the signature so each finished card wakes it — that is
when merges and commits happen. Use `enabled_toolsets=["terminal","file"]`,
`continuity=True`, and deliver to the board's notification target so its
reports sit beside the card notifications.

**Persist the coordinator in the repo the same turn you create it.** A cron job
keeps its prompt only in the scheduler, and the detector lives in
`~/.hermes/scripts/`; a runbook paragraph saying *what* it does cannot rebuild
it. `scripts/kanban-coordinator.sh` in this skill already does this: the prompt
is the repo's `<templates>/coordinator.md`, the detector is this skill's
`kanban-monitor.py` installed with the board baked in, and `up` / `down`
recreate and remove the job from the repo alone.

**Tear it down when the board drains.** On `ALL-DONE` the coordinator has
nothing left to do but it keeps waking on nothing; the morning status pass
should pause or remove the job and say so, rather than leave it running against
an empty board.

## Writing the woken agent's brief

The job runs in a fresh session with no conversation history, cannot ask
questions, and its **final response is what gets delivered**. So the prompt must
carry:

- the signal vocabulary and what each token means;
- the read-only commands for investigating (`stats`, `list`, `show <id>`,
  `runs <id>`) and the instruction to investigate *before* writing;
- **an explicit charter** — for a daytime watcher, read-only (no unblock, edit,
  commit or restart; its job is to say what needs a human); for an overnight
  coordinator, the closed list of permitted writes above;
- shell hazards of the cron environment: it runs zsh, so board commands are
  written out in full, never held in a variable;
- **the known red herrings**, so it does not re-derive a wrong diagnosis the
  orchestrator already disproved. Carry the benign-looking error strings, the
  bound-vs-ignored device list, and the usual innocent cause of each signal;
- an explicit no-op token (`NOOP`) for "investigated, no human needed", so a
  changed signature that turns out harmless sends nothing;
- the delivery shape: front-load the required action in the first line, because
  notification channels truncate, and write in the operator's language.

## What it cannot catch

A detector reads state, not intent. A worker confidently doing the wrong thing
presents as a healthy board and stays silent. Adversarial review is what covers
that; say so rather than letting the monitor stand in for it.
