---
name: dz-kanban
description: "Use when starting, resuming or checking Kanban development: board setup or update, session subscriptions, board status, orchestrator review, boards and role profiles (models, providers)."
version: 1.0.0
author: Denis Zamataev
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [kanban, orchestration, board, notifications, retrospective]
    related_skills: [hermes-kanban-development, dz-wrapup]
---

# Kanban entry point

The front door to the Kanban development method. The method itself — map,
chains, roles, landing — is the `hermes-kanban-development` skill (source:
`hermes-tools/kanban`); load it before creating cards or landing chains. This
skill answers the questions around it: is it installed and current, who hears
the board, what is the board doing, how did the orchestration go.

Everything runs through `python3 ${HERMES_SKILL_DIR}/scripts/kb.py` (`KB`
below). The board comes from `--board`, else from `.kanban/config.env` of the
current repository (worktrees find the primary checkout's). The session
defaults to the calling one (`$HERMES_SESSION_ID`).

| Ask | Do |
|---|---|
| `dz-kanban help` | `KB help`, then explain to the operator, in their language, what each command does and when to use it — grouped as setup, notifications, status/review, configure. Not the raw text. |
| `dz-kanban` (no argument) | `KB where`, then `KB status`; offer the next step |
| `dz-kanban setup` | see "Setup" |
| `dz-kanban subscribe` | `KB subscribe` — this session hears every open card of the board |
| `dz-kanban unsubscribe` | `KB unsubscribe` (dry run), then `--yes` |
| `dz-kanban status [7d]` | `KB status --since 7d`, then summarise (see "Status") |
| `dz-kanban review [7d]` | see "Review" |
| `dz-kanban configure …` | see "Configure" — boards and role profiles |

## Setup

`KB setup [--repo DIR] [--tools DIR]` is **read-only**: it prints the numbered
plan. It checks, in order: Hermes has the fork's Kanban (the method refuses
cards otherwise); the `hermes-tools` checkout and whether it is behind its
upstream; the installed method skill against `hermes-tools/kanban`, file by
file; the project — scaffold, board, role profiles, rule gaps in the role
templates (`kanban-sync.py`), the runbook's Stack section, the notify env.

1. Run it and show the plan.
2. **Installed copy edited by hand** (`edited in install` / `only in
   install`): those changes are lost by `install.sh --force`. Port each into
   `hermes-tools/kanban` first (copy, run `bun run check kanban`, commit), then
   reinstall. Never `--force` over them.
3. **New project:** the plan is `kanban-init.sh` → `kanban-profiles.sh` →
   `boards create` → the method skill's section 3 (bring-up). Follow section 3
   — it is the procedure; this plan only names its entry commands.
4. **Existing project:** `install.sh --force` (scripts reach the project at
   once), then `kanban-sync.py --apply <project>` for template rule gaps.
   Before `--apply`, check the board has no chain half built on the old
   templates; show the diff; commit on the operator's word; repeat per
   worktree (templates live on each branch).
5. Re-run `KB setup` until it prints "nothing to do".

## Subscriptions

A desktop/TUI session hears a card only through a `tui:<session>`
subscription. Cards created by `kanban-card.sh`/`kanban-chain.py` from a
session subscribe it automatically; `subscribe` is for taking over a board
from another session (after a restart, a new orchestrator tab).

- `KB subscribe [--board B] [--session S]` adds the session to every **open**
  card it is not on yet (`--all` includes done ones). Idempotent.
- `KB unsubscribe [--board B] [--session S] [--yes]` removes the session's
  subscriptions **on that board only** — its whole compression lineage — and
  lists open cards nobody will report to it afterwards. It never unpins the
  session (closing a session entirely is `dz-wrapup close`).
- Taking over: subscribe the new session first, then unsubscribe the old one —
  never leave a window where nobody hears the board.

## Status

`KB status [--since 7d] [--json]` reads the board's database read-only (fast,
no `hermes` call). It reports:

- counts by state; cards done and chains finished (fix card done) in the
  period; the last completion;
- **plan** — open cards grouped by chain;
- **notifications** — each subscribed session (title, cards, open cards,
  ended or not) and the chat targets (the chat id is never printed);
- **problems** — real blocks with their reason, unreleased chain heads, running
  cards without a heartbeat for 30 min, crashes / give-ups / protocol
  violations, model fallbacks and quota walls, open cards nobody hears, ended
  sessions still subscribed to open cards, `hermes pause` engaged.

Then write the operator a short summary in their language: one line of
throughput, the plan as "next up", problems first with what to do about each
(a real block → its action; a stale running card → `hermes kanban runs <id>`
and the profile's `logs/agent.log`; an ended subscribed session → `KB
subscribe` from the live one, then `KB unsubscribe --session <old>`).

## Review

Run in the orchestrator session, near the end of an effort or on request.
The goal: what made orchestrating this board slow, noisy or error-prone, and
what to change in the method, the project's templates or the tools.

1. **Facts:** `KB signals [--since 7d]` — run outcomes, wall time per
   profile, reviews with/without findings, fix cards closed no-change,
   orchestrator comments, worker questions (text), real blocks (reason),
   crashes and give-ups, model fallbacks, cards with retries.
2. **This session's own experience** — read back through the conversation:
   - what the orchestrator did by hand more than twice (a script candidate);
   - commands or board mechanics that surprised it or failed (wrong flag,
     zsh variable, race between cards);
   - notifications that arrived late, truncated, or not at all; questions it
     had to answer that the ticket or a decision record should have answered;
   - landing friction: flaky suites, port collisions, merges by hand;
   - operator corrections.
3. **Read the signals for causes**, not counts:
   - many fix cards no-change + few findings → reviews add latency for little;
     consider review only for risky slices;
   - workers' questions clustering on one ticket or ADR → the spec gap, name it;
   - one card with many runs, give-ups, watchdog kills → the task is too big
     or the environment flakes; split or warm up;
   - fallbacks / quota walls → profile model pins and `worker_fallback`;
   - long impl wall time vs short review → slices too large.
4. **Output** — a numbered list, most valuable first. Each item: the
   evidence (card ids, counts, quotes), the change, and its destination:
   `hermes-tools/kanban` (method, templates, scripts — then `setup` rolls it
   out), the project's role templates or runbook, a Hermes fork issue, or this
   skill. Check the destination first and drop what it already says.
5. Apply only what the operator picks. Method changes go into
   `hermes-tools/kanban` with its tests, never into the installed copy.

## Configure

`python3 ${HERMES_SKILL_DIR}/scripts/kb_config.py` (`KC`). Every write is a
**dry run without `--yes`**: it prints the exact `hermes` commands. Show them,
get the operator's word, re-run with `--yes`.

| Ask | Run |
|---|---|
| list boards | `KC boards` |
| create a board | `KC board-create <slug> --name "<title>" [--workdir DIR]` |
| rename | `KC board-rename <slug> "<new name>"` — display name only; the slug is immutable, so `.kanban/config.env` keeps working |
| default workspace | `KC board-workdir <slug> <dir>` |
| archive | `KC board-archive <slug>` — to `kanban/boards/_archived/`, recoverable |
| delete | `KC board-delete <slug>` — exports to `~/.hermes/backups/kanban/<slug>-<time>.tar.gz`, then hard-deletes |
| list a board's profiles | `KC profiles --board <slug>` (or `--prefix p`) — model, provider, effort, fallbacks, `worker_fallback` |
| what providers exist | `KC providers` |
| create role profiles | `KC profile-create <prefix> --repo DIR [--model-impl P:M --model-review P:M --model-fix P:M]` — runs the method's `kanban-profiles.sh` |
| change a profile | `KC profile-set <name> [--model P:M] [--effort high\|inherit] [--fallback P:M …\|--no-fallback] [--worker-fallback wait\|allow] [--description TEXT]` |
| delete a profile | `KC profile-delete <name>` — exports to `~/.hermes/backups/profiles/`, then deletes |

Guards the script enforces: a board with a running card is not archived or
deleted; a profile with an open card assigned is not deleted; `default` is
neither. Renaming a board's **slug** is not supported by Hermes — for that,
export, import `--as <new>`, update `.kanban/config.env`, then delete the old.

Method rules to apply when changing profiles (the script warns on the first
two, the rest are yours):

- The reviewer runs on a **different model family** than the implementer;
  same family makes the review a second pass, not an independent one. Say so
  if quota forces it.
- impl and fix keep `worker_fallback: wait` (a quota wall requeues the card
  instead of finishing it on a weaker model); review stays `allow`.
- `--fallback` replaces the whole list, in order; pass it once per entry.
- A change binds at the **next spawn**. A running card keeps the model it
  started with; never kill a worker to apply a change. A per-card
  `set-model` override beats the profile.
- Verify the actual route after the next real card:
  `grep "OpenAI client created" ~/.hermes/profiles/<p>/logs/agent.log`.

## Tests

`bash ${HERMES_SKILL_DIR}/tests/run.sh` builds a sandbox board database,
sessions, a fake `hermes`, a fake `hermes-tools` and installed method, and
checks status (period, chains, waits vs real blocks, heartbeats, unheard cards,
ended sessions, pause, no chat id leak), signals, subscribe/unsubscribe
(open cards only, idempotent, failures, board-scoped, never unpins) and the
setup plan (older vs locally edited install, profiles, notify env, new repo,
upstream Hermes), and configure: dry run by default, board create/rename/
archive/delete with the running-card guard and export-before-delete, profile
listing (board filter, family and worker_fallback warnings, YAML reader equal
to the CLI path), profile-set (model, effort, ordered fallbacks, worker
fallback, description, running-card notice), profile-delete guard and
profile-create through kanban-profiles.sh.
