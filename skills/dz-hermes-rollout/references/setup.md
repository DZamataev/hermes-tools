# Setup

Run `state.sh` first and branch on its `mode`. Tell the operator in one line what it found: the
harness this runs in, the live install (and which code it runs), the fork checkout. Ask every
question below one at a time, in the operator's language, and act on the answer before the next.

Every change to `config.yaml` (in the live Hermes home, `~/.hermes` unless `HERMES_HOME` says
otherwise) starts with a timestamped copy next to it and ends with `hermes config check`. API keys
go only into the home's `.env`, typed by the operator in their own editor: name the variable, never
ask for its value in the chat.

Long installs and builds run from the harness's shell. Wizards that wait for keyboard input
(`hermes setup`, a gateway prompt) cannot be answered from there: pass `--non-interactive` and hand
the operator the command to run in their own terminal when they want the wizard.

## Modes

| `mode` | Means | Do |
|---|---|---|
| `initial-setup` | no live install, no fork checkout | [Initial setup](#initial-setup), then [Common steps](#common-steps) |
| `install-only` | Hermes is installed, nothing to roll out from | by `install_kind`: `dz-fork` → offer `hermes update --branch develop` or [Prepare a fork for rollout](#prepare-a-fork-for-rollout); `vanilla` → `hermes update`, or move to a fork via [Initial setup](#initial-setup) option 1 or 3; `other-fork` → [Prepare a fork for rollout](#prepare-a-fork-for-rollout). Then the [Common steps](#common-steps) not done yet |
| `dev-only` | a fork checkout, no live install | install from the fork: the installer of option 3 with the checkout's tracked remote and branch (`dev_origin`), then rollout |
| `rollout` | both present | write the rollout config when `config=missing` ([Prepare a fork for rollout](#prepare-a-fork-for-rollout), last step), then the skill's Procedure |

## Initial setup

Nothing is installed: this is initial setup, so ask the operator to confirm one of three ways, with
the trade-offs below, and wait for their choice.

1. **Install the DZamataev fork** (`DZamataev/hermes-agent`, branch `develop`): upstream Hermes plus
   fixes not merged upstream yet, which the TeamClaude route and the Kanban method rely on. The way to use it without changing Hermes yourself. Updates later:
   `hermes update --branch develop` (a plain `hermes update` moves the install to the fork's stale
   `main`).
2. **Install vanilla Hermes** (`NousResearch/hermes-agent` through git), then the plugins and skills
   on top. Say plainly that part will not work: the TeamClaude OAuth-proxy route
   (`anthropic_oauth_proxy`), the Kanban method (`hermes-kanban-development`, `dz-kanban` refuse
   without the fork's Kanban), and rollout itself (there is no fork to roll out; update with
   `hermes update`). The plugins are not tested on vanilla.
3. **Your own fork, prepared for rollout**: for an operator who changes Hermes themselves. Ask for
   the fork's URL. Recommend forking `DZamataev/hermes-agent` rather than upstream, so the fork
   starts with its fixes: `gh repo fork DZamataev/hermes-agent --clone=false`, or the Fork button on
   GitHub. Creating the fork creates a repository on the operator's account: run it only on their
   word. Then install from the fork and [prepare it for rollout](#prepare-a-fork-for-rollout).

The installer (macOS, needs `git` and `curl`; it brings its own Python and Node):

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/hermes-agent/<branch>/scripts/install.sh \
  | HERMES_REPO_URL=https://github.com/<owner>/hermes-agent.git bash -s -- \
    --branch <branch> --include-desktop --non-interactive
```

| Way | `<owner>` | `<branch>` |
|---|---|---|
| 1 | `DZamataev` | `develop` |
| 2 | `NousResearch` | `main` |
| 3 | the operator's fork | its integration branch (`develop` for a fork of `DZamataev`) |

`HERMES_REPO_URL` belongs to `bash`, not `curl`, and `--branch` is required: both select what is
cloned. Afterwards:

- Check it: `git -C ~/.hermes/hermes-agent remote get-url origin` and `branch --show-current`; for
  1 and 3 also `hermes kanban create --help | tr -d ' \n' | grep -c local-commit-or-none` prints 1
  (0 means the fork's Kanban is missing).
- Offer the gateway (`hermes gateway install`): a background service for messaging platforms and
  cron, and the Kanban dispatcher.
- The desktop build is at `~/.hermes/hermes-agent/apps/desktop/release/mac-*/Hermes.app`: offer to
  copy it to `/Applications` (`ditto`) so it stays in the Dock.
- Provider and model: the [Backends](#backends-teamclaude-and-codex-lb) step below covers TeamClaude
  and codex-lb; for any other provider, give the operator `hermes setup` to run in a terminal.

## Prepare a fork for rollout

The fork checkout is where the operator's changes are made and merged; rollout carries the
integration branch from it into the live install.

1. Ask where to keep it (e.g. `~/dev/hermes-agent`) and clone the fork there on its integration
   branch.
2. Remotes: `origin` = the operator's fork; `upstream` = `https://github.com/NousResearch/hermes-agent`;
   for a fork of DZamataev also `dz` = `https://github.com/DZamataev/hermes-agent`. Ask which branch
   `merge.sh --upstream` follows: `dz/develop` (recommended for a fork of DZamataev: it already
   carries upstream plus the fork's fixes) or `upstream/main`. Fetch both.
3. `cd <checkout> && source ./activate` once: it provisions the checkout's own environment (several
   minutes the first time). Tests and the test instance run from it.
4. Ask where feature worktrees go (e.g. `~/dev/hermes-wt`). Work is done there:
   `git -C <checkout> worktree add -b <branch> <folder>/<name> develop`, then rollout merges it.
5. The live install must run the same branch: its commit has to be an ancestor of the checkout's
   branch (true right after option 3). An install of another origin is moved over with the
   operator's word: `git -C ~/.hermes/hermes-agent remote add fork <fork URL>`, fetch, check out the
   integration branch tracking `fork/<branch>`.
6. Copy `templates/config.sh` to `~/.config/dz-hermes-rollout/config.sh` and fill it in: checkout,
   integration branch, worktree folder, upstream ref, push remote (`origin`). Confirm with
   `state.sh` (`mode=rollout`) and `merge.sh --dry-run`.

## Common steps

Offer each; skip what `state.sh` and the config show is already done.

### Skills

Install the hermes-tools skills into Hermes (`skills/install.sh`, default target) so the agent there
can use them, and into the harness running this when it is not Hermes
(`skills/install.sh --target claude` or `--target codex`). The Kanban method skill installs with
`kanban/install.sh`. Offer the Kanban ones only on a fork (ways 1 and 3).

### Plugins

Ask whether to install the hermes-tools plugins:

| Plugin | Shows |
|---|---|
| `provider-limits` | status-bar chip with each custom provider's remaining quota (5-hour and weekly windows, per account) |
| `comp-count` | status-bar chip with the session's timeline: models, providers, compactions, tokens, estimated cost |

On yes: clone https://github.com/DZamataev/hermes-tools (or use the operator's checkout), run its
`setup_hermes_tools.sh`, and set `ROLLOUT_POST_DEPLOY='<checkout>/setup_hermes_tools.sh'` in the
rollout config so every deploy reinstalls them. Then tell the operator the two manual steps:
quit and reopen Hermes Desktop (plugin backends mount only at app start), and turn on each
plugin's desktop half in **Capabilities → Plugins** (off by default as a security boundary).

### Backends: TeamClaude and codex-lb

Ask whether to connect each kind and **how many of each**: an operator may have several codex-lb
instances or several TeamClaude proxies. For every backend ask for a provider name (lowercase,
unique, e.g. `codex-lb`, `codex-lb-team`), its URL, and the `.env` variable name for its key.

- **TeamClaude**: a proxy over Claude subscription (OAuth) accounts
  ([DZamataev/teamclaude](https://github.com/DZamataev/teamclaude)); the key comes from
  `teamclaude client add <machine>` on its server.
- **codex-lb**: a load balancer over ChatGPT/Codex accounts
  ([Soju06/codex-lb](https://github.com/Soju06/codex-lb)); the key comes from its administrator.

Add one entry per backend under `providers:`:

```yaml
providers:
  teamclaude:                        # the provider name the operator chose
    name: TeamClaude
    api: https://teamclaude.example.com:3443
    transport: anthropic_messages
    api_mode: anthropic_messages
    key_env: HERMES_CUSTOM_TEAMCLAUDE_API_KEY
    discover_models: true
    capabilities:
      anthropic_oauth_proxy: true    # TeamClaude only: Claude Code identity + per-session account pinning

  codex-lb:
    name: Codex LB
    base_url: https://codex-lb.example.com/backend-api/codex   # ends in /backend-api/codex
    key_env: CODEX_LB_API_KEY
    transport: codex_responses
    discover_models: true
```

- `anthropic_oauth_proxy: true` belongs on TeamClaude only. Providers that merely speak the
  Anthropic API (GLM, DeepSeek) reject the Claude Code identity it sends.
- The codex-lb path is `/backend-api/codex` with `codex_responses`; its `/v1` path is a different
  protocol.
- Model names come from the proxy (`discover_models`); pick them in the model picker rather than
  writing them down.

Then ask which backend is the main one (`model.provider` / `model.default`), and whether to add the
others as `fallback_providers` and as the `delegation` provider for subagents. Once the operator
has put the keys into `.env`, check every route with a one-shot:

```bash
hermes chat -Q --provider <name> -m <model> -q "reply with ok"
```

Restart Hermes Desktop and `hermes gateway restart` afterwards: both read the config at start.

### Compression for ChatGPT models

Ask whether to give ChatGPT models their own compression threshold, and say why it matters:

> This is an important step. Hermes raises the compression trigger to 85% of the context window
> for ChatGPT models only on the official Codex sign-in route. Through a proxy such as codex-lb the
> global threshold applies instead: 50% by default, raised to 75% for windows under 512K tokens.
> So on default settings a long session with a ChatGPT model gets compressed well before it needs
> to, losing detail to summaries and spending extra summary calls. A per-model threshold of 0.85
> keeps the whole window in use.

On yes, add a key per ChatGPT model family the operator uses through the proxy (the keys are
substrings of the model name; the longest match wins):

```yaml
compression:
  model_thresholds:
    gpt-5: 0.85
    gpt-6: 0.85
```

Narrow a key when a family is also used through another route with a smaller window (e.g.
`gpt-5.6` instead of `gpt-5`). The new threshold applies from the next session or `/model` switch.

## Finish

Setup is finished when every chosen step succeeded: `state.sh` shows the install (and, for way 3,
`mode=rollout`), and each connected backend answered its one-shot. Then:

1. Give the operator one short summary: what is installed and from where, which backends answer,
   what was skipped, and what is left to them (keys still missing, the desktop halves of the
   plugins, `hermes setup` for another provider).
2. On a fork install (ways 1 and 3, or `install_kind` `dz-fork`/`other-fork`), offer to run
   **`dz-kanban setup`** next to set up Kanban development: it checks the fork's Kanban, installs
   or updates the method skill and brings a project onto a board. Run it here when `dz-kanban` is
   installed in this harness (`skills/install.sh --target …` installs it), or tell the operator to
   ask for it in Hermes. On vanilla say Kanban needs the fork, and skip it.

When a step failed, stop before Finish: report the failure and what is left, and offer no Kanban.
