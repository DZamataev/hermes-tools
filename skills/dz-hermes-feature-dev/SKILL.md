---
name: dz-hermes-feature-dev
description: "Use when starting a new feature or polishing an existing one in a Hermes fork's feature worktrees, or when checking what upstream shipped since the fork's last sync."
version: 1.0.0
author: Denis Zamataev
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [hermes, fork, worktree, feature, upstream, tdd, review, subagents]
    related_skills: [dz-hermes-rollout, dz-clean-worktrees, hermes-adv-review, tdd]
---

# Develop a feature of a Hermes fork

The operator runs Hermes from a fork. Every fork feature lives on its own branch in its own worktree,
kept apart from the integration branch so it stays mergeable with upstream, syncs with upstream on
its own, and can be offered upstream as a PR. This skill is the approach for one such feature, from
picking the worktree to the operator's deploy. It links the rules it relies on; read them, they are
not repeated here.

Defaults (ask when the operator's setup differs): fork checkout `~/dev/hermes/hermes-agent`
(integration branch `develop`, remotes `origin` = fork, `upstream` = NousResearch), feature
worktrees `~/dev/hermes-wt/<name>`.

`dz-hermes-feature-dev help`: explain to the operator, in their language, the steps below and
`upstream-status.sh`; run nothing else.

## Read first

- `~/dev/hermes-wt/AGENTS.md` — what the feature worktrees are and why none is ever deleted.
- `~/dev/hermes/README.md` — the fork's layout: checkout, worktrees, live install, app.
- The repository's `AGENTS.md` and the area `AGENTS.md` of every directory you touch (its routing
  table names them).

## 1. Where things stand

Run `scripts/upstream-status.sh` (`${HERMES_SKILL_DIR}/scripts/`). It fetches upstream and prints:
the commit of `upstream/main` the integration branch is synced to, what upstream shipped since (by
area and as a log), and every feature worktree's own sync point. The sync point is not recorded
anywhere on purpose: it is `git merge-base develop upstream/main`, moved by the last upstream merge.

When the operator wants upstream: the integration branch takes it through `dz-hermes-rollout`
(`merge.sh --upstream`); a feature branch takes it by merging `upstream/main` into itself, never
by rebasing (the branch may already be merged into the integration branch or offered upstream).

## 2. The worktree

- **Polishing a feature:** work in its existing worktree and branch.
- **New feature:** propose a name and path, create it only on the operator's yes
  (`using-git-worktrees`). Ask what to base it on: `upstream/main` keeps it PR-ready; a feature that
  needs other fork features merges their branches in.
- Unclear what to build → `brainstorming` or `grilling` before code.

## 3. Build: orchestrator plus subagents

The session that runs this skill plans, writes briefs, rules on findings and verifies. Implementing,
reviewing and fixing go to `delegate_task` subagents, so the session's own quota lasts the whole
feature.

- **Briefs are files.** Write each brief into `~/.hermes/reports/adv-review/<topic>/` (task, files,
  rules, gates, exact commit message, where to write the report) and pass its path. A child knows
  nothing of this conversation.
- **One mutating child per checkout at a time.** Read-only reviewers may run in parallel; none of
  them runs mutation experiments (they would contaminate each other).
- **Tests first** (`tdd`): a failing test, then the code. Real wiring over mocks: a real `SessionDB`
  in `tmp_path`, `server.handle_request(...)`. Python tests through `scripts/run_tests.sh` only;
  Desktop through `npx vitest run` from `apps/desktop`, plus `npx tsc --noEmit -p .` and `npx eslint`.
- **Mutation-prove each wiring point by hand:** break the line, the test fails, restore. Backups go
  to the scratch dir, never `/tmp`. A hanging test is not a caught mutation.
- **A per-chat setting** follows `hermes-per-session-settings`.
- **Prove it live** before calling it done: drive the real gateway as in
  `hermes-per-session-settings` → `references/live-gateway-e2e.md`, with an isolated `HERMES_HOME`
  and a script kept in the scratch dir so it can be rerun after every fix.
- Commit by topic on the feature branch; never rewrite its history unless the operator asks.

## 4. Review rounds

1. Read-only reviewer subagents (backend and Desktop separately when both changed) write findings
   in Russian with severity, `file:line` and a failing scenario. When the operator asks for an
   adversarial review with pinned reviewer routes, use `hermes-adv-review` instead.
2. Reproduce the headline findings yourself, then rule on each: fix / do not fix (why) / ask the
   operator. Anything that changes a product property (behaviour the user sees, data format, wire
   protocol, trust boundary) goes to the operator first.
3. One fixer subagent, one commit for the round, a brief listing every accepted item.
4. Verify it yourself: exactly one new commit, clean tree, focused tests, one of its mutations
   repeated by hand, the live e2e rerun.
5. Next round reviews the fix commit plus the status of earlier findings. Stop when a round finds
   only nits; tell the operator what stays open.

## 5. Ship

`dz-hermes-rollout`: merge into the integration branch, verify, the operator checks the test
instance, then deploy. **Deploy is the operator's:** give them the exact `deploy.sh` command to run
from a terminal after they close Hermes themselves (deploy quits the app, which would kill this
session and other chats' background subagents). Push only on their word. Afterwards check the
result (install commit, app bundle, gateway status). If the checkout has `graphify-out/graph.json`,
run `graphify update <root>`.

## Pitfalls

- **`tui_gateway` helper modules run with `server.py`'s globals** (`method_ctx.bind_module`):
  bare names like `_session_db`, `logger` resolve there, so tests call helpers through
  `tui_gateway.server`, and pyright's "undefined name" there is noise.
- **A new `session.create` parameter breaks an older backend** (`extra="forbid"` rejects the whole
  create): add it to `DROPPABLE_CREATE_FIELDS` in
  `apps/desktop/src/app/session/hooks/use-session-actions/session-create-request.ts`.
- **A contract change** needs `scripts/run-in-hermes-env python -P scripts/gen_gateway_contracts.py`;
  **a new Desktop i18n key** needs `npm run -s i18n:keys` in `apps/desktop` and an entry in every
  overlay locale.
- **Scripts that import Hermes** (e2e drivers, probes) run under `scripts/run-in-hermes-env python`
  from the worktree; the live install's venv may not import the worktree's code.
- **Tool results longer than ~200 chars are pruned by compression**: a Desktop card that renders
  from a tool's receipt needs a summarizer in `agent/context_compressor.py`
  (`_TOOL_RESULT_SUMMARIZERS`) that keeps the fields it reads.
- Failures that are not yours: check the rollout skill's known-red list, then confirm on the base
  (`git stash`, rerun) before blaming the branch.
