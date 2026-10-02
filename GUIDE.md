# Setting up this stack yourself

This guide assembles the setup these tools are built for, from nothing:

```
 your Mac                                         VPS
┌──────────────────────────────────────┐        ┌──────────────────────────────┐
│ Hermes (fork, branch develop)        │  TLS   │ nginx :3443                  │
│   Desktop app + gateway (launchd)    │───────▶│   └▶ TeamClaude 127.0.0.1:3456│
│   ~/.hermes/config.yaml  providers   │        │        Claude Max accounts   │
│   ~/.hermes/.env         keys        │        └──────────────────────────────┘
│   plugins: comp-count, provider-limits│
│   skill:   hermes-kanban-development │  TLS   ┌──────────────────────────────┐
│                                      │───────▶│ codex-lb                     │
└──────────────────────────────────────┘        │   ChatGPT/Codex accounts     │
                                                └──────────────────────────────┘
```

- **Hermes** — [DZamataev/hermes-agent](https://github.com/DZamataev/hermes-agent), branch
  `develop`: upstream NousResearch plus fixes not merged upstream yet. Required for the
  TeamClaude OAuth-proxy route and for the Kanban method.
- **TeamClaude** — [DZamataev/teamclaude](https://github.com/DZamataev/teamclaude), branch
  `dzamataev/develop`: a proxy that pools several Claude subscriptions and rotates on quota.
  Runs on a VPS so every machine shares one pool.
- **codex-lb** — a load balancer over ChatGPT/Codex accounts. You do not install it to connect:
  you get a key for an existing instance.
- **hermes-tools** — this repository: desktop plugins and the Kanban skill.

Order matters: Hermes first, then the proxies, then the keys, then plugins and Kanban.

## 1. Install Hermes from the fork

Requirements: macOS (Apple Silicon) or Linux, `git`, `curl`. The installer brings its own
Python, Node and the rest.

```bash
curl -fsSL https://raw.githubusercontent.com/DZamataev/hermes-agent/develop/scripts/install.sh \
  | HERMES_REPO_URL=https://github.com/DZamataev/hermes-agent.git bash -s -- --branch develop --include-desktop
```

Both settings are needed. `HERMES_REPO_URL` points the clone at the fork, and `--branch develop`
selects the branch. With the default branch `main` you get the fork's `main`, which is a stale
copy of upstream without any of the fork's changes. Note where the variable sits: it is set for
`bash`, not for `curl`.

What you get:

| Path | What |
|---|---|
| `~/.hermes/hermes-agent` | the code, a git checkout of the fork on `develop` (`origin` = the fork) |
| `~/.hermes/config.yaml` | settings: providers, models, plugins, Kanban |
| `~/.hermes/.env` | secrets, read through `key_env` (section 4) |
| `~/.hermes/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app` | the desktop build; copy it to `/Applications` to keep it in the Dock |

The installer runs `hermes setup` and offers to install the gateway. Accept the gateway: it is a
launchd/systemd service, and it also hosts the Kanban dispatcher (section 7). Without a terminal
(or with `--non-interactive`) it skips both. Run `hermes setup` and `hermes gateway install` later.

Check that the install is the fork:

```bash
git -C ~/.hermes/hermes-agent remote get-url origin   # …/DZamataev/hermes-agent.git
git -C ~/.hermes/hermes-agent branch --show-current   # develop
hermes kanban create --help | tr -d ' \n' | grep -c local-commit-or-none   # 1; 0 = not the fork
```

(`tr` is needed: argparse wraps the help text at the hyphens.)

### Updating

```bash
hermes update --branch develop
```

**Always pass `--branch develop`.** A plain `hermes update` targets `main`. It moves the checkout
off `develop` onto the fork's `main` and every fork fix disappears from the running app. If that
happens, `hermes update --branch develop` brings it back.

## 2. TeamClaude on a VPS

You need:

- a Linux VPS with systemd and root access. A small one is enough;
- a domain name pointing at it, for TLS;
- one or more Claude Pro/Max subscriptions.

### 2.1 Node, the code, the accounts

```bash
# Node 20+ (nvm is fine: the service records the absolute node path)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
. ~/.nvm/nvm.sh && nvm install 24

git clone https://github.com/DZamataev/teamclaude.git ~/teamclaude-bootstrap
cd ~/teamclaude-bootstrap

node src/index.js login --token     # once per Claude account
```

`login --token` is the headless flow. It prints a URL. Open it in a browser on any machine, log
into that Claude account, and paste the code from the success page back into the terminal. Repeat
for each account. Accounts go to `~/.config/teamclaude.json`.

Why a clone, not `npm install -g @karpeleslab/teamclaude`: the npm package is upstream and has
no `deploy` command. The fork's `deploy` subcommand is what installs the service below.

### 2.2 Install it as a service

```bash
node src/index.js deploy install https://github.com/DZamataev/teamclaude.git --ref dzamataev/develop
```

This clones into `/opt/teamclaude`, runs the full test suite on the candidate release, installs
`/etc/systemd/system/teamclaude.service` (enabled, `Restart=always`) and the `teamclaude` command
in `/usr/local/bin`. The bootstrap clone is no longer needed afterwards.

```bash
teamclaude deploy status                 # release, service, node path
systemctl is-active teamclaude.service   # active
```

### 2.3 Proxy settings

Edit `/root/.config/teamclaude.json`. The fields that matter for this setup:

```json
{
  "proxy": {
    "port": 3456,
    "apiKey": "…generated on first run…",
    "trustLoopback": false
  },
  "switchThreshold": 0.95,
  "distributeSessions": true,
  "quotaProbeSeconds": 300
}
```

- The proxy listens on `127.0.0.1` only (the default `proxy.host`). nginx is the only way in.
- `trustLoopback: false`: behind a reverse proxy on the same host every caller looks like
  loopback. Loopback is exempt from the key by default, so without this setting anonymous
  requests would spend your quota. A request carrying `X-Forwarded-For` is never exempt, and the
  nginx block below sends it. This setting is the second lock.
- `distributeSessions: true` spreads concurrent conversations across accounts and keeps each
  conversation on its account, so the prompt cache stays warm. It only works within one
  `priority` tier, so give all accounts the same priority (0).
- `quotaProbeSeconds` re-reads quotas in the background without spending messages.

Apply with `teamclaude deploy restart` (or `POST /teamclaude/reload` for live-reloadable fields).

### 2.4 One key per client machine

```bash
teamclaude client add my-mac        # prints a tc-… key ONCE; copy it now
teamclaude client list              # keys masked; --show-keys reveals them
teamclaude client remove my-mac     # revoke
```

Give every machine (and every person) its own key. Usage is booked per key in
`teamclaude status` and the dashboard, and one machine can be revoked without touching the
others. `proxy.apiKey` works too, but its traffic is unattributed.

### 2.5 TLS and nginx

Certificate with [acme.sh](https://github.com/acmesh-official/acme.sh) (standalone mode needs
port 80 free during issue and renewal; use your DNS provider's API mode otherwise):

```bash
curl https://get.acme.sh | sh -s email=you@example.com
~/.acme.sh/acme.sh --issue --standalone -d teamclaude.example.com --keylength ec-256
mkdir -p /etc/ssl/teamclaude
~/.acme.sh/acme.sh --install-cert -d teamclaude.example.com --ecc \
  --fullchain-file /etc/ssl/teamclaude/fullchain.pem \
  --key-file       /etc/ssl/teamclaude/key.pem \
  --reloadcmd      "systemctl reload nginx"
```

`/etc/nginx/sites-available/teamclaude`, symlinked into `sites-enabled`:

```nginx
server {
    listen 3443 ssl;
    listen [::]:3443 ssl;
    server_name teamclaude.example.com;
    ssl_certificate     /etc/ssl/teamclaude/fullchain.pem;
    ssl_certificate_key /etc/ssl/teamclaude/key.pem;
    client_max_body_size 50m;
    location / {
        proxy_pass http://127.0.0.1:3456;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

- `proxy_buffering off` and the one-hour timeouts are required for streaming responses and long
  agent turns.
- **Do not add key checking in nginx.** nginx knows one static secret, so it would reject the
  per-client keys before TeamClaude sees them. TeamClaude checks keys itself.

`nginx -t && systemctl reload nginx`, then open the port in the firewall.

### 2.6 Check it from your Mac

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://teamclaude.example.com:3443/teamclaude/status
# 401 — the key gate works
curl -s -o /dev/null -w '%{http_code}\n' -H "x-api-key: tc-…" https://teamclaude.example.com:3443/teamclaude/status
# 200
```

The browser dashboard is at `https://teamclaude.example.com:3443/teamclaude/dashboard`. It asks
for the key once.

### 2.7 Day-to-day

```bash
teamclaude status                       # accounts, quotas, sessions, per-client usage
teamclaude watch                        # the same, refreshing in the terminal
teamclaude deploy ref dzamataev/develop # update: fetch, test, switch, restart, health check
teamclaude deploy rollback              # back to the previous release
teamclaude deploy logs --lines 200 --no-follow
```

A failing test suite never activates a release, and a failed health check restores the previous
one by itself.

Watch the VPS disk. Small disks fill with logs: set `SystemMaxUse=` for journald and check
`df -h /` from time to time.

## 3. codex-lb access

Ask the administrator of the codex-lb instance for its address and an API key. Check the key:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer sk-…" https://codex-lb.example.com/v1/usage
# 200; without the header — 401
```

Running your own codex-lb is a separate project ([Soju06/codex-lb](https://github.com/Soju06/codex-lb),
`uvx codex-lb`). It connects to Hermes the same way, with its own `base_url` and key.

## 4. Keys: where they go

Keys go only in **`~/.hermes/.env`**, never in `config.yaml`. The config names the variable
through `key_env`:

```bash
# ~/.hermes/.env
HERMES_CUSTOM_TEAMCLAUDE_API_KEY=tc-…          # from `teamclaude client add` (2.4)
CODEX_LB_API_KEY=sk-…                          # from the codex-lb administrator (3)
```

```bash
chmod 600 ~/.hermes/.env
```

- The variable names are free-form. They only have to match `key_env` in section 5.
- **Profiles have their own `.env`.** `hermes profile create --clone-from` copies `.env` into
  `~/.hermes/profiles/<name>/.env` once, at creation. Kanban creates three profiles per project
  (section 7). After rotating a key, update it in every profile, or those workers keep the old
  key:
  `grep -l HERMES_CUSTOM_TEAMCLAUDE_API_KEY ~/.hermes/.env ~/.hermes/profiles/*/.env`.
- Never paste a key into a chat with the agent, a commit or an issue. Revoke a leaked TeamClaude
  key with `teamclaude client remove`.

## 5. Connect Hermes to both proxies

In `~/.hermes/config.yaml`:

```yaml
model:
  provider: teamclaude
  default: claude-opus-5
  base_url: https://teamclaude.example.com:3443
  key_env: HERMES_CUSTOM_TEAMCLAUDE_API_KEY
  api_mode: anthropic_messages

providers:
  teamclaude:
    name: TeamClaude
    api: https://teamclaude.example.com:3443
    transport: anthropic_messages
    api_mode: anthropic_messages
    key_env: HERMES_CUSTOM_TEAMCLAUDE_API_KEY
    discover_models: true
    capabilities:
      anthropic_oauth_proxy: true

  codex-lb:
    name: Codex LB
    base_url: https://codex-lb.example.com/backend-api/codex
    key_env: CODEX_LB_API_KEY
    transport: codex_responses
    default_model: gpt-5.6-sol
    discover_models: true

# when TeamClaude is out of quota or unreachable, continue on Codex
fallback_providers:
  - provider: codex-lb
    model: gpt-5.6-sol

# subagents (delegate_task) and cron jobs use the main pool too
delegation:
  provider: teamclaude
  model: claude-opus-5
```

What the less obvious lines do:

- **`anthropic_oauth_proxy: true`** is the reason for using the fork. TeamClaude forwards to
  Anthropic with Claude subscription (OAuth) tokens, so the request has to look like Claude Code:
  Bearer auth, Claude Code identity headers, native thinking-signature replay. The flag also
  makes Hermes send `x-claude-code-session-id`, which `distributeSessions` uses to keep a
  conversation on one account. Without the flag, requests fail or all land on one account. Do
  not set it on providers that only implement an Anthropic-compatible API (GLM, DeepSeek): they
  reject the Claude Code identity.
- `base_url` for codex-lb ends in `/backend-api/codex`, and the transport is `codex_responses`.
  The plain `/v1` path is a different protocol.
- `discover_models: true` fills the model picker from the proxy. Model names change with the
  subscriptions, so check what each proxy offers in the picker (`/model`) rather than copying
  names from this file.

Check each route with a one-shot:

```bash
hermes chat -Q -q "reply with ok"                                   # main model, TeamClaude
hermes chat -Q --provider codex-lb -m gpt-5.6-sol -q "reply with ok"
```

Then restart the Desktop app (quit and reopen) and `hermes gateway restart`: both read the
config at start.

## 6. Plugins from hermes-tools

```bash
git clone https://github.com/DZamataev/hermes-tools.git ~/dev/hermes-tools
cd ~/dev/hermes-tools
./setup_hermes_tools.sh          # or: bun run setup
```

It installs two plugins into `~/.hermes/plugins/` and enables their backend halves in
`config.yaml`:

| Plugin | Shows |
|---|---|
| `provider-limits` | status-bar chip with each custom provider's remaining 5-hour quota (TeamClaude, codex-lb), and a popover with weekly windows, per-account rows and upstream status |
| `comp-count` | status-bar chip with the session's timeline: models, providers, compactions, tokens and estimated list-price cost |

Then:

1. **Quit and reopen Hermes Desktop.** Plugin backends are mounted only by the app's own server
   process at start. `hermes gateway restart` does not help: the gateway does not serve plugin
   routes.
2. Open **Capabilities → Plugins** and turn on the desktop half of both plugins. The script cannot
   do this step: desktop halves are off by default as a security boundary.

`provider-limits` needs no settings of its own. It reads the providers and their `key_env` from
`config.yaml`, and sends each key only to its own provider.

To update: `git pull && ./setup_hermes_tools.sh`. The script says whether a restart is needed.

## 7. Kanban for a project

Kanban runs one slice of work as **implement → blind review → fix**. Each role runs as a separate
unattended Hermes worker in its own profile, and the results report back to the session you
orchestrate from. The method is in [`kanban/SKILL.md`](kanban/SKILL.md). This section is the
setup only.

### 7.1 Once per machine

```bash
bash ~/dev/hermes-tools/kanban/install.sh       # → ~/.hermes/skills/software-development/hermes-kanban-development
hermes gateway status                           # the dispatcher lives in the gateway: it must run
```

The gateway's embedded dispatcher claims cards and spawns workers. If the gateway is down, cards
sit in `ready` forever. Install it with `hermes gateway install` if section 1 skipped it.

Card scripts refuse to run on a Hermes without the fork's Kanban changes. That is the
`local-commit-or-none` check from section 1.

### 7.2 Once per repository

```bash
K=~/.hermes/skills/software-development/hermes-kanban-development/scripts
cd ~/dev/my-project                      # needs at least one commit

bash $K/kanban-init.sh my-project        # scaffolds .kanban/, role templates, runbook; never overwrites
```

Edit what it created:

- **`.kanban/config.env`** (tracked): `KANBAN_BOARD`, `KANBAN_PROFILE_PREFIX`,
  `KANBAN_BASE_BRANCH` and, most importantly, **`KANBAN_GATE`** and **`KANBAN_GATE_OK`**: the
  repo's test command and the text its last line contains when it passes. Every writing card runs
  this gate.
- `docs/agents/kanban-templates/*.md`: role preambles. Fill in the repo-specific sections: test
  layers, defect classes, traps.
- `docs/hermes_kanban_development.md`: the repo's runbook.

Create the three role profiles. Put the reviewer on a **different model family** from the
author: a reviewer on the author's model is a second pass, not an independent review.

```bash
bash $K/kanban-profiles.sh myp --repo ~/dev/my-project \
  --model-impl   teamclaude:claude-opus-5 \
  --model-fix    teamclaude:claude-opus-5 \
  --model-review codex-lb:gpt-5.6-sol
```

This creates `mypimpl`, `mypreview` and `mypfix`, cloned from the default profile (with its
`.env`, see section 4). Each gets a role-only memory, and impl/fix get
`kanban.worker_fallback: wait`, so a quota wall requeues the card instead of finishing it on a
weaker model. Prefixes are lowercase letters and digits.

Create the board:

```bash
hermes kanban boards create my-project --name "My project"
```

Notifications to a chat (optional): copy `.kanban/notify.env.example` to `.kanban/notify.env`
(gitignored by init) and fill in `KANBAN_NOTIFY_CHAT_ID` (and `KANBAN_NOTIFY_THREAD_ID` for a
forum topic). The gateway must have that platform configured, e.g. `TELEGRAM_BOT_TOKEN` in
`~/.hermes/.env`. Never commit the filled copy: a chat id names a private group.

### 7.3 Running work

From a Hermes Desktop session in the repository, ask the agent to load the skill
(`/hermes-kanban-development`) and plan the work with it. Cards are created by the scripts, which
attach the role templates. The orchestrating session runs them from its terminal:

```bash
git worktree add -b feature/x ~/dev/my-project-wt/x main
python3 $K/kanban-chain.py --title "Feature X" --task docs/agents/kanban-templates/tasks/01-x.md \
  --workdir ~/dev/my-project-wt/x
```

`kanban-chain.py` subscribes the calling session to every card it creates, so completions and
blocks arrive in that chat as turns. Keep that session open. Watch the board with
`hermes kanban list` or the Kanban tab in Desktop.

When the method's rules change in hermes-tools, update the skill
(`bash kanban/install.sh --force`) and bring the project templates up to date:
`python3 $K/kanban-sync.py` reports what is missing, and `--apply <project>` inserts it.

## 8. Things that are easy to miss

- **`hermes update` without `--branch develop`** silently moves you onto the fork's stale
  `main` (section 1).
- **Profile `.env` files are copies.** Rotate a key in all of them (section 4).
- **Plugin changes need a Desktop restart**, not a gateway restart (section 6).
- **One TeamClaude key per machine.** Shared keys make usage unreadable and revocation all-or-nothing.
- **No auth in nginx in front of TeamClaude** (section 2.5).
- **Use the same `priority` on all TeamClaude accounts**, or `distributeSessions` balances
  nothing (section 2.3).
- **Claude Code can use the same TeamClaude** on any machine:
  `ANTHROPIC_BASE_URL=https://teamclaude.example.com:3443` plus your client key. See
  TeamClaude's `docs/usage.md`. The fork also ships a Claude Code status line with fleet quota in
  `examples/claude-code-statusline.sh`.
