---
name: dz-hermes-rollout
description: "Use when installing Hermes from scratch or rolling out a Hermes Agent fork to the local install: first-time setup, merging feature worktrees or upstream into the fork, verifying it on a test instance, deploying or pushing it, or porting an open upstream PR as a hotfix."
version: 1.1.0
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

The harness sets how deploy runs: inside Hermes Desktop (`hermes_desktop=yes`) always
`deploy.sh --detach`, since the app closes with the session in it; from any other harness, attached.
Initial setup needs a harness other than Hermes, since there is no Hermes yet: the skill installs
into Claude Code or Codex with `skills/install.sh --target claude|codex`.

## Procedure

1. **Merge.** `merge.sh --dry-run` (with `--upstream` when the operator wants upstream). Show the
   operator the branch list with each branch's commits. Every branch ahead of the integration
   branch is merged, a half-finished one included: when one is not meant to ship, stop and ask.
   Then `merge.sh`. A conflict aborts that merge and stops; resolve it by hand in the fork, then
   rerun (see Conflicts).
2. **Verify.** `verify.sh`. The changed tests run; failures are re-run alone, then on the upstream
   commit the branch last merged, and only failures upstream lacks are ours: fix those in the
   owning worktree, merge again, verify again. Desktop tests are not part of it: when merged
   branches touch `apps/desktop`, also run its typecheck and the changed `*.test.ts(x)` files
   headless with vitest. The step then starts the **test instance**, a second Hermes window on the
   fork's build and backend with an isolated home. Run the one-shot check the script prints, then
   hand the operator a checklist: a normal chat plus each shipped feature, concretely. Wait for
   their confirmation.
3. **Deploy, only after the operator confirms.** `deploy.sh` (`--push` only on the operator's
   explicit word). It refuses a sha `verify.sh` did not pass (`--unverified` only on their word)
   and refuses while a window it would close has a turn running (see Rules). Report the final shas
   of the branch and the install.

## Conflicts

Resolve so both sides survive: the fork's features and upstream's change. When upstream moved
code the fork had edited (strings into a catalog, a function into a new module), carry the fork's
edit into the new place, in every copy (every locale), rather than picking a side. Watch for a
duplicate that a clean auto-merge leaves behind (the same element rendered twice). Commit the
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

- **Push only on the operator's explicit word**, and only the integration branch to its remote.
- **When `state.sh` says `hermes_desktop=yes`, always `deploy.sh --detach`**, and tell the operator
  the app will close and reopen: deploy quits every Hermes window and the session that ran it dies.
- **Live turns block deploy.** Deploy closes every Hermes window, the test instance included, and
  stops SSH-started backends; both kill their running turns (the gateway restart drains its own). `deploy.sh` lists the
  running turns and refuses; `--dry-run` shows them too. The operator has usually just been using
  the test instance, so ask them to let its turns end; pass `--ignore-live-turns` only on their
  word.
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
- The test instance's home has the live config and `.env` without messaging platforms, cron and
  the API server, and no `auth.json`: OAuth-only providers need a sign-in there.
