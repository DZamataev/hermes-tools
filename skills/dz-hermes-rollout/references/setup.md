# First-run setup

Ask the operator each question below in order, one at a time, in their language, and act on the
answer before the next. Every change to `config.yaml` (in the live Hermes home, `~/.hermes` unless
`HERMES_HOME` says otherwise) starts with a timestamped copy next to it, and ends with
`hermes config check`. API keys go only into the home's `.env`, typed by the operator in their own
editor: name the variable, never ask for its value in the chat.

## 1. Rollout paths

Copy `templates/config.sh` to `~/.config/dz-hermes-rollout/config.sh` and fill it in from the
operator's answers: the fork checkout, its integration branch, the folder of feature worktrees, the
upstream ref (`<remote>/<branch>`), the push remote. Defaults cover the live home, the install
checkout and the app bundle; ask only when the operator's differ. Confirm with
`merge.sh --dry-run`: it lists the worktree branches.

## 2. Plugins

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

## 3. Backends: TeamClaude and codex-lb

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

## 4. Compression for ChatGPT models

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
