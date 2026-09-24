---
name: hermes-kanban-development
description: "Use when running a repo's development on Hermes Kanban. Map → AFK cards → implement/review/fix chains → gated landing."
version: 1.0.0
author: Denis Zamataev
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [kanban, multi-agent, orchestration, wayfinder, tdd, review, overnight]
    related_skills: [wayfinder, grill-with-docs, to-tickets, tdd, code-review]
---

# Development on Hermes Kanban

A method for building a project with many non-interactive Hermes workers on a
Kanban board while one foreground session (the **coordinator**, i.e. you) plans,
feeds the board and lands the results. It works in any repository; the scripts
next to this file are repo-agnostic and read a per-repo `.kanban/config.env`.

Scripts: `${HERMES_SKILL_DIR}/scripts/` (Hermes substitutes the installed
location when it loads this skill). Every script prints usage with no
arguments. Requires `bash`, `python3` and `git`; nothing else.

| Script | Does |
|---|---|
| `kanban-init.sh <slug> [repo]` | scaffolds `.kanban/config.env`, role templates, the per-repo runbook, gitignores the notify env. Never overwrites. |
| `kanban-profiles.sh <prefix>` | creates `<prefix>impl/review/fix` profiles, rewrites their memory to role-only content, optional model pins |
| `kanban-card.sh <role> "<title>" <task-file\|-> <workdir> [parent…]` | one card: role template + task body, profile, retries, workspace, notify subscription (+ the calling desktop/TUI session); prints the id |
| `kanban-chain.py` | implement → review → fix triple (optionally after a parent, optionally with an operator gate), created blocked, linked, session subscription checked, head released |
| `kanban-monitor.py` | the change-detector for a supervising cron job; stable tokens only |
| `kanban-coordinator.sh up\|down\|status\|prompt\|drill` | installs the detector, renders the coordinator prompt, creates / removes the cron job |

## 1. When the board is worth it

Use it when the work spans more sessions than one context can hold and most of
it can be done **AFK** (no human turn): implementation slices with written
acceptance checks, research that writes one file. Do not use it for one-session
fixes, for design interviews, or for work whose acceptance is taste.

Work item types and where they go:

| Type | Goes to |
|---|---|
| grilling (product/design question) | the foreground session, never a card |
| task with acceptance checks | implement → review → fix chain |
| research (read, probe, write one file) | a `research` card, parallel, no worktree |
| prototype | an AFK build card on its own branch + a blocked **gate** card that chooses |
| anything needing eyes/ears/hands/credentials | a blocked **gate** card for the operator |

## 2. The pipeline

1. **Map.** Run `/wayfinder` for the destination, out-of-scope line and the
   fork-in-the-road tickets; resolve design forks with `/grill-with-docs` so
   the answers land in `CONTEXT.md` / ADRs, not in chat. Ask the operator only
   what only they can weigh (scope, priorities, taste, access). Answer
   questions about the *form of the system* yourself, with rejected
   alternatives, and offer them for veto. When the operator asks for minimal
   questions, write the destination yourself as a resolved ticket "open for
   veto". Put in the map's notes that ready-for-agent tickets are built on the
   board — wayfinder is plan-only by default.
2. **Tickets.** `/to-tickets` into the repo tracker. Each AFK ticket names its
   acceptance checks and the layer its first failing test lives at. Tickets
   blocked on research stay `needs-triage` until the research card answers.
3. **Task files.** One file per card under `<templates>/tasks/<ticket>.md`:
   the ticket path (the worker reads acceptance from the ticket, not from a
   paraphrase), the non-obvious constraint, the repo traps that apply, and any
   **definition constant** the repo already has (tolerance, threshold) by
   symbol and value — never let a worker invent a second one.
4. **Cards.** Created by script only (section 5).
5. **Landing.** The coordinator verifies and merges (section 8).

## 3. Bring-up on a new repository (one turn, no setup questions)

After the operator says "put it on the board", the setup choices are yours;
report them afterwards where they can be overridden.

```bash
K=${HERMES_SKILL_DIR}/scripts                      # a path, not a command: fine in zsh
bash $K/kanban-init.sh <board-slug> <repo>             # then edit what it scaffolded
bash $K/kanban-profiles.sh <prefix> --repo <repo>      # <prefix>impl/review/fix
hermes kanban boards create <board-slug> --name "<title>"
git -C <repo> worktree add -b <effort>/chain <wt-root>/<effort> main
```

1. **Commit first.** A repo with no commits cannot grow a worktree. Make the
   initial commit yourself, gitignore the notify env *before* `git add -A`,
   and say so in the report.
2. **Edit the scaffold for this repo.** `.kanban/config.env` (board, profiles,
   gate command, base branch); the role templates' repo sections (gate, test
   layers, defect classes for this language/runtime); the runbook's traps and
   "what agents cannot check here". Porting from another repo is mostly
   subtraction: warm-up command, test invocation *and its success criterion*,
   timeouts, ports, traps and defect lists never transfer.
3. **Profiles before cards.** A claimed card keeps the model it started with and
   `assign` refuses a running card. Pin models, then create cards.
4. **Warm the worktree** through the repo's own scripts (install, binary
   downloads, engine import, the full gate and e2e), in the background with
   completion notify. A cold cache makes the first worker debug a network
   blip; a fixture hidden by `.gitignore` makes it "fix" a green test.
   Ignored planning files do not travel with the branch: copy them in.
5. **Notify target** goes into the gitignored notify env, never into a tracked
   file (a chat id names a private group; secret scanners do not catch it).
6. Fan out cards (section 5), then verify: `list` shows heads `running`,
   `show <child>` has the right `parents:`, `notify-list` shows one target per
   card.

## 4. Roles and profiles

| Role | Profile | Sees |
|---|---|---|
| coordinator | the foreground session | everything; owns the board, the map, write-protected files, merges |
| implement / research | `<prefix>impl` | the repo, the ticket, the task file |
| adversarial review | `<prefix>review` | the diff and the repo's rules — **not** the ticket, task file, plan or author |
| fix | `<prefix>fix` | the review findings and the implementation summary |
| gate | the operator | absolute artifact paths + one copy-pasteable command + numbered questions |

- **One profile per role**: workers sharing a profile share `MEMORY.md`, and a
  reviewer that can read the implementer's memory is not blind.
- `--clone-from` copies `MEMORY.md` wholesale. Rewrite it to role-only content
  before the first dispatch (`kanban-profiles.sh` does this). `USER.md` may stay.
- **Reviewer on a different model family than the author.** When quota forces
  one family for all roles, say so: the review becomes a second pass, not an
  independent one. Per-role fallback is a top-level `fallback_providers:` list
  in the profile's `config.yaml`.
- Check routing: `HERMES_HOME=~/.hermes/profiles/<p> hermes config get model.default`
  (configured) and `grep "OpenAI client created" ~/.hermes/profiles/<p>/logs/agent.log`
  (actual, only after a real run). Per-card `set-model` overrides beat the profile.
- A config change binds at the next spawn. **Never kill a running worker to
  apply one.**

## 5. Cards

Always through `kanban-card.sh` / `kanban-chain.py`: the role preamble lives in
the repo (`<templates>/{common,research,review,fix}.md`), so a card can never
be created without its prohibitions and gate.

```bash
# one slice, after the previous slice's fix card, with an operator gate at the end
python3 $K/kanban-chain.py --title "Virtual list" --task <templates>/tasks/05-list.md \
  --workdir <wt> --after <prev-fix-id> --gate-task <templates>/tasks/05-gate.md
# a research card in the primary checkout
bash $K/kanban-card.sh research "Media formats" <templates>/tasks/03-media.md <repo>
```

What every body carries (the templates hold it; do not trim them):

- workspace boundary; local commits allowed; push / PR / merge / rebase /
  reset / amend / revert / force / branch deletion forbidden;
- read the repo's agent rules and the ticket first; write-protected files
  (`AGENTS.md`, `CLAUDE.md`) are returned as text in the summary, not written;
- the gate command and its success line; which expensive suites the card must
  run and which it must leave to the convergence card;
- TDD with the RED run quoted; a **manual** mutation check for every new
  guarantee (break the real line, see the test fail, restore);
- an objective obstacle → `kanban_block`, reason **opening with the action
  required and the path** (the notification shows ~160 chars);
- a **neutral** completion summary: first line exactly
  `START_HEAD..END_HEAD, N files, gate: green` and nothing about intent — the
  reviewer reconstructs intent from the diff.

Board mechanics the scripts already respect (know them when working by hand):

- **Race-free chains**: create every card `--initial-status blocked`, link,
  then `unblock` only the head and `dispatch`. A card whose `--parent` did not
  resolve is claimable on the next tick.
- `create --json` returns the id under **`id`**. `link` is positional
  `parent child`; read `parents:` in `show <child>` afterwards.
- Bodies via `--body-file -` (stdin), never `--body "$BODY"`.
- `--json` creation skips auto-subscribe: subscribe every card — the notify
  target **and** the orchestrating session (`--platform tui --chat-id
  "$HERMES_SESSION_KEY"`), or the session never hears of a block.
- **One writer per worktree**: cards sharing a workspace are chained. Research
  cards that each write one *new* file may share the primary checkout.
- Archiving a card releases its children; re-parent them.
- A body is frozen at creation. If a queued card's inputs move, **archive and
  recreate** it (re-link edges, re-subscribe). Comment only on a running card.
  Writing a long "ignore what the body says" comment is the recreate signal.
- Never hold a board command in a shell variable: zsh (terminal tool, cron)
  does not split it and every call silently fails.
- `unset HERMES_DELEGATED_CHILD_CONTEXT` before board writes from a session
  that inherited it.

## 6. Review and fix

- The review card reads its range from the parent's summary first line; if it
  is missing it reviews `<base>..HEAD`, and in either case **states the range
  it reviewed**.
- It runs the gate itself, never trusting the summary, and returns numbered
  findings `F1…` with `path:line`, consequence, fix and severity.
- The fix card applies each finding with a test and a mutation check, or
  rejects it **with proof** (command output or the refuting line). A finding
  that argues with an ADR or the glossary is returned as *needs decision*, not
  applied. A symptom-shaped finding is fixed on **every** path that produces it.
  Two failed attempts on one finding: stop and record.
- Findings the fixer rejected are the operator's first suspects at acceptance:
  name them in the hand-off.

## 7. While it runs

- The dispatcher reclaims crashed, stale and protocol-violating workers with
  bounded retries. Do not kill workers by hand; `block` a card you need
  stopped.
- Observe, you cannot attach: `hermes kanban runs <id>` (attempts),
  `show <id>` (comments, heartbeats, summary), the assignee profile's
  `logs/agent.log` (live), `hermes sessions export <sid>` (after).
- The operator's diagnosis of a stall ("it hit limits") is a hypothesis; read
  the board and the log before acting on it. A worker's block reason is a
  diagnosis too: re-run the check it quotes and read all of its output.
- Before a reboot: `hermes pause --reason …` (global, all boards) **and**
  comment the stop order on the running card (commit what exists, no
  merge/lock mid-flight, `kanban_block` with the resume point). Pausing does
  not tell the worker.
- Workers do not inherit your interactive shell: export toolchain paths in the
  template, not in your `.zshrc`.

## 8. Landing, gates, and the morning pass

- A chain has landed when its fix card is done. In its worktree: clean
  `git status`, the gate, and the e2e suites; then in the primary checkout
  `git merge --ff-only <chain-branch>` (else a merge plus the gate again; abort
  on conflict). **Push only on the operator's explicit word**, then confirm
  with `git ls-remote origin refs/heads/<branch>`.
- Mark the ticket resolved with the merge commit and append the decision to the
  map's "Decisions so far".
- **Gate cards** are created blocked with absolute paths, a command that opens
  the artifact, and numbered questions. When the operator asks "why is it
  blocked?" — it is theirs by design. A verdict splits three ways; sweep all
  three in one turn: accepted parts (complete the gate), a stated design rule
  (docs commit on the base branch, now), defects (a new chain + a new gate).
- **Returning to a board** ("status"): list, read `Latest summary:` of every
  card that finished, run the gate in each finished chain, land it, then answer:
  one line per slice (what landed, elapsed, findings, tests before → after),
  gate questions numbered, rejected findings named. If the next eligible card
  is a gate, launch its artifact yourself and still give the command.

## 9. Unattended runs: the coordinator job

You do not run between the operator's messages. For overnight progress, create
a monitor-gated cron job whose woken agent has a **closed list of writes**:

```bash
bash $K/kanban-coordinator.sh drill      # prove the detector tokens first
bash $K/kanban-coordinator.sh up         # installs ~/.hermes/scripts/kanban_monitor_<board>.py + cron job
bash $K/kanban-coordinator.sh down       # when the board drains — do not leave it waking on nothing
```

- The detector prints stable tokens only: `DONE:<n>`, `BLOCKED:<ids>`,
  `STALL`, `RETRYING:<id>`, `ALL-DONE`, `BOARD-UNREADABLE`. Unchanged output
  skips the model run, so a healthy tick costs nothing. No timestamps, sorted
  ids.
- Permitted: verify and merge a finished chain locally, commit research
  outputs and map/ticket updates, unblock once after a crash the evidence
  confirms, dispatch once on `STALL`. Forbidden: push, edit code, edit
  write-protected files, create or archive cards, kill workers, change
  profiles or models, touch other boards.
- The prompt is `<templates>/coordinator.md` in the repo, rendered by the
  script, so the job can be rebuilt from the repo alone.
- Its output is delivered to the notify target; first line = the action needed,
  `NOOP` when nothing needs saying. The CLI cannot restrict toolsets; when you
  want `terminal`+`file` only, create it with `cronjob_manage` using the prompt
  from `kanban-coordinator.sh prompt`.

## 10. Notifications

Two destinations per card:

- **The notify target** (`platform:chat_id[:thread_id]` from the notify env):
  the operator's chat.
- **The orchestrating session.** Run from a desktop/TUI session, the scripts
  also subscribe `tui:$HERMES_SESSION_KEY`; that session's own poller delivers
  blocks and completions into it as a turn, so the orchestrator reacts without
  being asked. Not done for gateway sessions (the notify target covers them),
  cron runs or board workers; `KANBAN_NOTIFY_SESSION=0` turns it off. The
  session must stay open: a closed tab hears nothing until it is reopened.

What reaches the operator is truncated per event and not configurable:

| Event | Delivered |
|---|---|
| completed | first **line** of the summary, ~200 chars |
| blocked | the reason, ~160 chars |
| gave_up | the error, ~200 chars |
| crashed, timed_out | no text |

So: block reasons open with the action; completion summaries stay neutral (a
mechanical first line), because the reviewer reads them.

## 11. Decompose to reachable capability

Slice by what becomes **reachable** in the product, not by module. Every
producing card needs a consumer card naming the call sites it converts, or the
board goes green while the feature is unreachable. Before saying "ready to
test", grep the production entry point and the flag default yourself.

## 12. The per-repo runbook

`kanban-init.sh` scaffolds `docs/hermes_kanban_development.md`. It must be
self-contained for that repo's readers: no paths into other checkouts, no model
ids (they change with quota — record the rule and the check commands instead).
Record standing authorizations the operator grants twice, with what they do
**not** cover. After the first chain, fill the calibration section: wall time
per card kind, findings per review, retries.

More in this skill's `references/`: `pitfalls.md` (short rules by area:
board CLI, profiles, workspaces, card bodies, review, gates, notifications),
`monitor-gated-supervision.md`, `per-repo-kanban-runbook.md`,
`upstream-merge-cards.md`, `external-baseline-audit-cards.md`,
`parameter-sweep-for-blocked-decisions.md`. Card skeleton:
`templates/card-body.md`.
