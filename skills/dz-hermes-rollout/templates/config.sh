# dz-hermes-rollout configuration. Copy to ~/.config/dz-hermes-rollout/config.sh and edit.
# Every value can also be set as an environment variable of the same name (it wins over this file).
# shellcheck shell=bash disable=SC2034

# Required: your fork's checkout — merges, upstream sync, tests and the test build happen here.
ROLLOUT_FORK=$HOME/src/hermes-agent

# The fork's integration branch: feature worktrees merge into it, the install runs it.
ROLLOUT_BRANCH=develop

# Folder holding the fork's feature worktrees. Empty: every linked worktree of the fork.
ROLLOUT_WT_ROOT=

# <remote>/<branch> that `merge.sh --upstream` merges, and the baseline verify.sh compares
# failing tests against.
ROLLOUT_UPSTREAM=upstream/main

# Remote `deploy.sh --push` pushes the integration branch to.
ROLLOUT_PUSH_REMOTE=origin

# The live Hermes home and the git checkout the live install runs (defaults: $HERMES_HOME or
# ~/.hermes, and its hermes-agent/).
# ROLLOUT_LIVE_HOME=$HOME/.hermes
# ROLLOUT_INSTALL=$HOME/.hermes/hermes-agent

# Isolated state of the test instance (home, desktop user data, baseline worktree).
# ROLLOUT_TEST_HOME=$HOME/.hermes-rollout-test

# The desktop app bundle the Dock launches.
# ROLLOUT_APP=/Applications/Hermes.app

# Command run after the install update and before the gateway restart, e.g. reinstalling your own
# plugins. Gets HERMES_BIN (the install's launcher) and HERMES_INSTALL in its environment.
# ROLLOUT_POST_DEPLOY='~/src/my-plugins/install.sh'

# 0 when no gateway service is installed (desktop only).
# ROLLOUT_RESTART_GATEWAY=1
