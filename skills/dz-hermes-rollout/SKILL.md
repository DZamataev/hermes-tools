---
name: dz-hermes-rollout
description: "Use when installing Hermes from scratch or rolling out a Hermes Agent fork to the local install: first-time setup, merging feature worktrees or upstream into the fork, verifying it on a test instance, deploying or pushing it, or porting an open upstream PR as a hotfix."
version: 1.2.0
author: Denis Zamataev
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [hermes, rollout, deploy, desktop, git, worktree, upstream, fork]
    related_skills: [dz-clean-worktrees]
---

# Roll out a Hermes fork to the local install

For an operator who runs Hermes from their own fork: features live in git worktrees of the fork,
the fork's integration branch collects them (and upstream), and the live install plus Hermes
Desktop run that branch. Every change reaches the live app through three gated steps.

```
feature worktrees ─┐                      1. merge.sh [--upstream]
upstream branch ───┴─merge──▶ fork  <integration branch>       (merges, tests)
                                  │ 2. verify.sh: smoke, tests vs upstream, test instance
                                  │    (fork backend + isolated home) → operator checks it
                                  │ 3. deploy.sh [--push]
                                  ▼
                 live install  (git checkout; gateway + app run its launcher)
                                  └─ desktop build ──▶ the app bundle the Dock launches
```

Scripts: `${HERMES_SKILL_DIR}/scripts/` (Hermes substitutes the installed location; elsewhere they
sit next to this file). Each ends with one result line, `MERGE|VERIFY|DEPLOY OK: …` or
`… FAILED: …`, and fires a macOS notification. `--help` on any script lists its flags.

`dz-hermes-rollout help`: explain to the operator, in their language, the modes `state.sh` finds,
the three steps and their gates, what setup asks, and the scripts' flags (`--help` on each); run
nothing else.

## Start: where am I

Run `state.sh` before anything else. It reports the **harness** running this skill (Hermes Desktop
or CLI, Claude Code, Codex), the live install and which code it runs, the fork checkout, and a
`mode`:

- `rollout`: go to the Procedure below.
- `initial-setup`, `install-only`, `dev-only`: follow [references/setup.md](references/setup.md).
  With nothing installed this is initial setup: it asks the operator to confirm one of three ways
  (install the DZamataev fork; install vanilla Hermes plus the plugins and skills, with what will
  not work; or their own fork, best forked from DZamataev, prepared for rollout), then offers the
  plugins, TeamClaude and codex-lb backends, and a compression threshold for ChatGPT models. When
  everything succeeded on a fork, it ends by offering `dz-kanban setup` for Kanban development.

The harness sets who runs deploy: inside Hermes (`harness=hermes`, or `hermes_desktop=yes` for an
agent started in the app's terminal pane) the agent runs `merge.sh` and `deploy.sh --dry-run` only,
and gives the operator the commands for BOTH `verify.sh` and `deploy.sh` (see Rules): Hermes's
terminal guard refuses `verify.sh` because it stops and restarts a gateway, which would kill the
agent's own command. From any other harness the agent may run all of them itself.
Initial setup needs a harness other than Hermes, since there is no Hermes yet: the skill installs
into Claude Code or Codex with `skills/install.sh --target claude|codex`.

## Procedure

1. **Merge.** `merge.sh --dry-run` (with `--upstream` when the operator wants upstream). Show the
   operator the branch list with each branch's commits. Every branch ahead of the integration
   branch is merged, a half-finished one included: when one is not meant to ship, stop and ask.
   Then `merge.sh`. A conflict aborts that merge and stops; resolve it by hand in the fork, then
   rerun (see Conflicts). When either side touched `tui_gateway/contracts/*`, run
   `scripts/run-in-hermes-env python -P scripts/gen_gateway_contracts.py` in the fork after the
   merge: `git status` must stay clean (the generated TS/OpenRPC match the merged models).
2. **Verify.** `verify.sh`. The changed tests run; failures are re-run alone, then on the upstream
   commit the branch last merged, and only failures upstream lacks are ours: fix those in the
   owning worktree, merge again, verify again. Desktop tests are not part of it: when merged
   branches touch `apps/desktop`, also run its typecheck and the changed `*.test.ts(x)` files
   headless with vitest. Then the **fork's Playwright specs** run (every `apps/desktop/e2e/*.spec.ts`
   the fork added or changed since its upstream sync point; windows transparent and click-through,
   log `verify-e2e.log` in the rollout state dir): they are the UI proof of the fork's own features,
   so any failure is ours, and a feature without one is not proven on Desktop's real backend.
   `--no-e2e` skips them, on the operator's word. The step then starts the **test instance**, a second Hermes window on the
   fork's build and backend with an isolated home. Run the one-shot check the script prints, then
   hand the operator a checklist: a normal chat plus each shipped feature, concretely. Wait for
   their confirmation. A branch updated after verify (polish asked for in the test window):
   `merge.sh && verify.sh --stop; verify.sh` rebuilds the test instance on the new commit.
   `verify.sh --no-tests` skips straight to the build and test instance (the record says
   `tests=none`, which deploy still accepts; tell the operator which run proved the tests). Use it
   when the tests already passed for this exact sha and only the test-instance stage failed, or,
   on the operator's word, when the install is far behind: the preconditions line shows the commit
   and changed-test-file counts, and over a thousand files means an hour or more of tests. Say so
   when it shows, not after a long silent wait.
   - **"no backend from <fork> within 120 s":** read `<test home>/logs/gateway.log` before
     anything else. A startup conflict there ("Port … already in use") means a live-only key
     reached the test config: besides `platforms`, `config.yaml` can carry env-style top-level
     keys (`API_SERVER_*`, messaging tokens) that the gateway reads like `.env`.
   - **An error the operator reports from the test window:** renderer errors and toasts land in
     `~/.hermes-dev/home/logs/desktop.log`, the turn (provider errors, retries, 429s) in
     `agent.log`, inbound messages in `gateway.log`. Line them up by timestamp before guessing,
     and separate code failures from provider/quota ones.
   - Run `deploy.sh --dry-run` right after verify, not only at the end: it performs the real
     fetch into the install, so object/fetch failures surface while the operator still waits.
3. **Deploy, only after the operator confirms.** `deploy.sh` (`--push` only on the operator's
   explicit word). It refuses a sha `verify.sh` did not pass (`--unverified` only on their word)
   and refuses while a window it would close has a turn or a background subagent running (see
   Rules). Inside Hermes run `deploy.sh --dry-run [--push]`, then hand the operator the absolute
   command it prints. **Re-check the integration branch's sha first** (`git -C <fork> log -1
   --oneline <branch>`): other sessions merge their worktrees into it too, so it can move past the
   verified sha while the operator tests; deploy then refuses, and a plain push would ship
   unverified commits. Tell the operator what moved it and offer verify again or a push on their
   word. When the operator says it is done, check: the install's `git log -1` and,
   after `--push`, the pushed remote branch at the new sha; the app bundle's
   `Contents/Resources/app.asar` freshly written; `hermes gateway status` running. Report the
   final shas of the branch and the install.

## Conflicts

Resolve so both sides survive: the fork's features and upstream's change. When upstream moved
code the fork had edited (strings into a catalog, a function into a new module), carry the fork's
edit into the new place, in every copy (every locale), rather than picking a side. Watch for a
duplicate that a clean auto-merge leaves behind (the same element rendered twice). Branches that
each append a block to the same function conflict textually but need both blocks. Commit the
merge with the resolutions listed in the message.

## Hotfix from an open upstream PR

When the operator wants an unmerged upstream fix: check the PR is still open (if merged, the next
`merge.sh --upstream` brings it). Otherwise create a worktree from the integration branch,
`git fetch <upstream remote> pull/<n>/head`, cherry-pick its commits so authorship stays. Pin it
with a test shaped like the operator's real failure: red without the PR's commits, green with
them, and red again when the fixed line is mutated back by hand. Compare the suite's failures
with clean upstream; only new ones are ours. Commit the test to the worktree branch, then
`merge.sh`. When the PR fixes only part of the operator's case, say so plainly.

## Rules

- **Push only on the operator's explicit word**, to the fork's remote: `--push` pushes the
  integration branch before the deploy and, once the deploy succeeded, every feature-worktree
  branch, since others build the integration branch from the fork. A rejected worktree branch
  (diverged from the remote) is reported, never forced; force-push it only on the operator's word.
- **Inside Hermes the operator runs deploy, not the agent.** Deploy quits the app, which kills the
  agent's own turn, background subagents of other chats and the app's terminal panes. `deploy.sh`
  refuses when `HERMES_SESSION_ID` or `HERMES_DESKTOP_TERMINAL` is set (`--dry-run` still works)
  and prints the absolute command. Give the operator that command: close Hermes (Cmd+Q) once its
  chats and subagents are done, then run it in a terminal outside Hermes. Check the result
  afterwards (install and branch shas, the app bundle rewritten, the gateway running).
- **Live turns and background subagents block deploy.** Deploy closes every Hermes window, the test
  instance included, and stops SSH-started backends; both kill their running turns and the
  `delegate_task` children that outlive their parent's turn (the gateway restart drains its own).
  `deploy.sh` lists both (`live_turns` + `live_subagents` in lib.sh) and refuses; `--dry-run` shows
  them too. The operator has usually just been using the test instance, so ask them to let its
  turns end; pass `--ignore-live-turns` only on their word.
- **Find the app by executable path (`ps -o comm`), never `pgrep -f`:** macOS sometimes withholds a
  process's argv, and `pgrep/pkill -f` then miss the live app (deploy would skip the live-turn
  check and the quit). Only the test instance, told apart by its extra argv, is matched by argv.
- Deploy preconditions are checked before any side effect: both checkouts on the integration
  branch and clean (`package-lock.json` churn is exempt), the install's commit an ancestor of the
  fork's branch.

## What deploy does

Quit every Hermes window by process path (the test instance shares the bundle id) → `git merge
--ff-only` in the install → `<install>/.hermes/bin/hermes --version` (the first launch re-syncs the
environment) → `hermes_cli.source_completion --finish-update --desktop` (the tail `hermes update`
runs: TUI, web UI, packaged desktop, config migration, skills sync) → copy the build over the app
bundle when it differs → the post-deploy hook → `hermes gateway restart` → stop the backends that
a Desktop on another machine started here over SSH (`hermes serve … --ssh-session-token-file
<home>/desktop-ssh/…`; that Desktop respawns them on the new code) → reopen the app.

## Pitfalls

- **`hermes update` and the in-app update are not this flow.** They follow upstream's default
  branch: on the integration branch they switch the install back to upstream and the fork's
  features vanish. Update only through these scripts.
- **Other machines' installs (m1/m4 mac minis, Steam Deck)** have no fork checkout, so these
  scripts do not run there: replay deploy's steps over ssh, detached, per
  `steam-deck-remote-work` → `references/hermes-remote-install.md`. `hermes update` is just as
  wrong there.
- **Any `hermes` launch after a fast-forward of the install** runs the source-update completion,
  which rebuilds the desktop and overwrites the app unless it is running. Never `git pull` in the
  install by hand.
- **The runtime is the environment behind `<install>/.hermes/bin/hermes`**, not a `venv/` folder in
  the checkout.
- **`gateway restart` reloads neither plugins nor the desktop backend**; only an app restart does.
- **A backend another machine's Desktop started over SSH outlives the app and the gateway.** Left
  running after a deploy, it serves old code that imports the new files: plugin routes answer 404
  "Headless backend (hermes serve)" and `projects.tree` breaks its wire contract. Deploy stops
  them; one started by hand still needs a stop, and the remote Desktop reconnects by itself.
- **Test instance: "the '<provider>' package is required … lazy installs are disabled"** comes
  from the fork's environment, not the code: provider SDKs are lazy extras, and the environment
  refuses lazy installs from a process that is not the install's own. Install the extra into the
  fork's environment (`. ./activate -- && hermes pm install --extra <name>`), then
  `verify.sh --stop && verify.sh`.
- **After a deploy without `--push`** the install is ahead of the remote branch it tracks until the
  operator pushes; that is expected, not drift.
- **`scripts/run_tests.sh $FILES` under zsh gets one argument** (zsh does not word-split); pass an
  array or use `xargs`.
- The test instance's home has the live config and `.env` without messaging platforms, cron and
  the API server (also as top-level `config.yaml` keys), and no `auth.json`: OAuth-only providers
  need a sign-in there. Its backend is the Desktop's own local `gateway run` (older builds spawned
  `hermes serve`), which runs from the fork checkout's sources and outlives the app's quit:
  `verify.sh --stop` stops both, and a merge into the fork changes the code under a running one.
- **The live install is a partial clone (`tree:0`).** It advertises upstream commits whose trees
  and blobs it never downloaded, so after an upstream merge a plain fetch from the fork fails
  with "did not send all necessary objects"; `deploy.sh` then retries with
  `fetch.negotiationAlgorithm=noop` (one full local transfer, ~2 min). Never "fix" it by
  touching objects in the install by hand: each `cat-file` lazily fetches one blob and the next
  missing one appears.
- **A worktree branch the push rejects as diverged** was usually rebased locally after an old
  version was pushed. `git cherry <local> origin/<branch>` lists the remote's commits; when every
  one has a local counterpart (same subjects) and `git merge-base --is-ancestor
  origin/<branch> <integration branch>` holds, the old commits stay reachable and the fix is
  `git push --force-with-lease=<branch>:<old remote sha> origin <branch>`, on the operator's word.
  Merging the remote into the branch instead would duplicate every commit.
- **Fixing these scripts:** edit the source in the hermes-tools checkout
  (`skills/dz-hermes-rollout/`), `bash -n` it, run the change's dry-run path (`merge.sh
  --dry-run`, `deploy.sh --dry-run --push`), reinstall with `skills/install.sh --force
  dz-hermes-rollout`, commit there; push hermes-tools only on the operator's word. Before the
  reinstall, `diff -r` the installed copy against the source: `skill_manage` patches land in the
  installed copy only, and `--force` replaces it (the old copy goes to
  `~/.hermes/backups/skills/`). Carry installed-only edits into the source first.
