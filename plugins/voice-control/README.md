# voice-control

A status-bar switch for a headless voice Hermes: a Hermes CLI that a service keeps in a tmux
session on another machine (here CT 125 `voice`, started by `hermes-voice.service`). One click
turns its microphone off, another turns it back on. The popover also carries a prompt field that
types into that session and a «Музыка» block for the player on that machine.

| Chip | Meaning |
|---|---|
| 🎙 | listening: the "Hey Hermes" listener holds the microphone |
| 🔇 | microphone off |
| — | unknown: host unreachable, tmux session missing, or this plugin's backend not enabled. The tooltip and popover say which |
| … | switching |

## What it does on the assistant host

The backend runs `ssh <ssh_host> bash -s -- <status|on|off> <tmux_session>` and feeds
[`dashboard/remote.sh`](dashboard/remote.sh) over stdin. Nothing is installed on the host.

- **Off** types `/wake off` and `/voice off` into the CLI. `/wake off` stops the listener and saves
  `wake_word.enabled: false`, so the microphone stays off after a service restart or reboot.
  `/voice off` also cancels a recording already in progress (a follow-up window).
- **On** types `/voice on` and `/wake on` (saves `wake_word.enabled: true`).
- **State** is read from the kernel, not from the CLI's output: listening means the CLI's process
  tree holds an ALSA capture device (`/dev/snd/pcmC*D*c`) open.
- **A running turn is never interrupted.** With the default `busy_input_mode: interrupt`, a line
  typed during a turn redirects that turn, so the script waits while the prompt shows
  `❯ msg=interrupt|queue|steer` (up to 20 s) and then reports `busy` instead of typing.

## Prompt

The popover has a text field and «Отправить» (Enter sends too). The text is typed into the CLI's
tmux session exactly as if someone had typed it there, so the assistant answers — and **speaks the
answer**. The reply is not shown in Desktop: the field and the status line only say whether the
text went through.

Remote-side it is `ssh <ssh_host> bash -s -- prompt <tmux_session> <base64>`; the desktop half is
`POST /prompt {"text": "..."}`.

- **The text** is stripped, must be 1–2000 characters, and may not contain control characters.
  Newlines, CRs and tabs become spaces: a newline would submit the line before the rest of the text
  arrives.
- **Text starting with `/` is refused**, by the backend and again by `remote.sh`: a slash command
  would run instead of a prompt, and those belong to the microphone switch.
- **Injection safety.** ssh joins its argv into one command line for the remote shell, so the text
  never travels as an argument: the backend sends base64, `remote.sh` checks the base64 charset
  again, decodes it and types the result with `tmux send-keys -l` (literal — no key names, no shell).
- **A running turn is never typed into** — the same 20 s wait as the microphone switch, then
  `busy`. The field keeps the text when the send failed, so it is not lost.

## Music

The popover also shows a «Музыка» block: one line for what is playing, then ⏯ (toggle), ⏭ (next),
⏹ (stop) and −/+ for the volume (10 at a time, the current level in between).

It drives the player on the same host: `$HOME/.local/bin/music`, mpv over its own socket
(`homelab/scripts/music.py` on CT 125). So it needs neither the tmux session nor an idle CLI, and
it never waits for a turn to finish.

- **The line** is `artist — title` for Jellyfin, `station · icy_title` for radio, `⏸ …` while
  paused, and «ничего не играет» when nothing plays.
- **Reading** is `GET /music` → `music status --json` (one JSON object). **A click** is
  `POST /music {"cmd": "...", "arg": "..."}` → that one command, then the fresh status.
  Remote-side it is `ssh <ssh_host> bash -s -- music-status <tmux_session>` and
  `ssh <ssh_host> bash -s -- music <tmux_session> <cmd> [<arg>]`.
- **Only** `pause`, `resume`, `toggle`, `next`, `prev`, `stop` and `volume` are accepted, and
  `volume` takes `N`, `+N` or `-N`. Both `plugin_api.py` and `remote.sh` check the same list, so a
  request body never reaches a shell.
- **Status is fetched when the popover opens**, and a click's reply becomes the new status. There
  is no polling: the player takes no notice of this plugin.
- **The block never starts playback** — no `play`, `radio`, `hold` or `release`; starting music
  belongs to the voice CLI. The buttons are disabled while nothing plays and while a click is in
  flight.

## Settings

Settings → Plugins → voice-control (stored in `plugins.entries.voice-control.settings`):

| Key | Default | |
|---|---|---|
| `ssh_host` | `ct125-voice` | `~/.ssh/config` alias or `user@host`. ssh runs with `BatchMode=yes`: the key must not ask for a passphrase |
| `tmux_session` | `voice` | the session holding the voice CLI |

The host must already accept `ssh <ssh_host>` from this Mac. For CT 125 the alias goes through PVE,
because the container's subnet is not routed from the LAN — see
`homelab/hosts/ct125-voice.md`.

## Install

`./setup_hermes_tools.sh` from the repository root, restart Hermes Desktop, then turn on the
desktop half in Capabilities → Plugins.

## Tests

```bash
./tests/run.sh          # remote.sh syntax + behaviour (stub tmux), backend (ssh faked), renderer — offline
./tests/run.sh --e2e    # flips the real assistant's microphone off and back on, restoring it
```

`--e2e` never sends a prompt: the assistant would answer out loud. The prompt path is covered
offline — `tests/test_remote.py` runs the real `remote.sh` against a stub `tmux` on PATH and checks
what would be typed.
