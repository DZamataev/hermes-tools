#!/bin/bash
# voice-control, remote half. Runs ON the assistant host, fed over ssh:
#   ssh <host> bash -s -- <status|on|off> <tmux-session>  < remote.sh
#   ssh <host> bash -s -- music-status <tmux-session>  < remote.sh
#   ssh <host> bash -s -- music <tmux-session> <cmd> [<arg>]  < remote.sh
#   ssh <host> bash -s -- prompt <tmux-session> <base64-text>  < remote.sh
# Drives the Hermes CLI that hermes-voice.service keeps in a tmux session, and
# prints key=value lines the backend parses. Nothing is installed on the host.
# music-* actions drive the mpv player in $HOME/.local/bin/music and ignore the
# tmux session: the player lives outside it and outside the CLI's turns.
#
# "Listening" is read from the kernel, not from what the CLI prints: it is true
# while the CLI's process tree holds an ALSA capture device (/dev/snd/pcmC*D*c)
# open — the wake-word listener holds one for as long as it runs.
set -u
action=${1:-status}
session=${2:-voice}
config=${HERMES_HOME:-$HOME/.hermes}/config.yaml

out() { printf '%s=%s\n' "$1" "$2"; }

descendants() {
  local p
  for p in $(pgrep -P "$1"); do echo "$p"; descendants "$p"; done
}

mic_open() {
  local pane p
  pane=$(tmux list-panes -t "$session" -F '#{pane_pid}' 2>/dev/null | head -1)
  [ -n "$pane" ] || return 1
  for p in "$pane" $(descendants "$pane"); do
    ls -l "/proc/$p/fd" 2>/dev/null
  done | grep -q '/dev/snd/pcmC[0-9]*D[0-9]*c'
}

# wake_word.enabled from config.yaml: what the next service start will do.
wake_enabled() {
  awk '/^wake_word:/{s=1;next} s&&/^[^ ]/{exit} s&&/^  enabled:/{print $2; exit}' "$config" 2>/dev/null
}

prompt_line() { tmux capture-pane -p -t "$session" 2>/dev/null | grep '❯' | tail -1; }

# While a turn runs the prompt shows the busy-input hint (msg=interrupt|queue|steer).
# Typing then would redirect the turn with our command as a message, so wait.
busy() { prompt_line | grep -qE '❯ msg=(interrupt|queue|steer)'; }

send() {
  local cmd=$1
  tmux send-keys -t "$session" C-e C-u          # drop anything half-typed in the input
  tmux send-keys -t "$session" -l "$cmd"
  # Keys typed in one burst read as a paste: Enter right after the text stays in the input.
  sleep 0.4
  tmux send-keys -t "$session" Enter
  sleep 0.8
  # The slash-completion menu takes the first Enter.
  if prompt_line | grep -qF -- "$cmd"; then
    tmux send-keys -t "$session" Enter
    sleep 0.8
  fi
}

wait_idle() { for _ in $(seq 1 40); do busy || break; sleep 0.5; done; }

# Text for `prompt`, decoded into $decoded. ssh joins its argv into one command line for
# the remote shell, so the text travels base64'd (never as an argument) and is typed literally.
decode_prompt() {
  case ${1:-} in
    ''|*[!A-Za-z0-9+/=]*) return 1 ;;
  esac
  decoded=$(printf '%s' "$1" | base64 -d 2>/dev/null) || return 1
  # control characters would press keys of their own (a newline submits, an escape moves the
  # cursor); a leading / would run a slash command, and those belong to the mic switch.
  [[ -n $decoded && $decoded != /* && ! $decoded =~ [[:cntrl:]] ]]
}

report() {
  if mic_open; then out listening true; else out listening false; fi
  out wake_enabled "$(wake_enabled)"
}

# --- music: mpv on this host over its own socket, so no tmux session and no
# busy-prompt wait. Last stdout line is always `music status --json`; commands
# print their own one-liner before it.
music_bin=$HOME/.local/bin/music

music_run() {
  local cmd=$1 arg=${2:-}
  case $cmd in
    pause|resume|toggle|next|prev|stop) [ -z "$arg" ] || { out error bad_music_arg; return 2; } ;;
    volume) [[ $arg =~ ^[+-]?[0-9]{1,3}$ ]] || { out error bad_music_arg; return 2; } ;;
    *) out error bad_music; return 2 ;;
  esac
  "$music_bin" "$cmd" ${arg:+"$arg"}
}

case $action in
  music-status) "$music_bin" status --json; exit $? ;;
  music)
    music_run "${3:-}" "${4:-}" || exit $?
    "$music_bin" status --json
    exit 0
    ;;
esac

if ! tmux has-session -t "$session" 2>/dev/null; then
  out error no_session
  report
  exit 0
fi

case $action in
  status) ;;
  prompt)
    if ! decode_prompt "${3:-}"; then out error bad_prompt; exit 2; fi
    wait_idle
    if busy; then
      out error busy
      exit 0
    fi
    send "$decoded"
    out sent true
    exit 0
    ;;
  on|off)
    wait_idle
    if busy; then
      out error busy
      report
      exit 0
    fi
    if [ "$action" = on ]; then
      send '/voice on'; send '/wake on'; want=0
    else
      # /wake off stops the listener and persists wake_word.enabled=false; /voice off
      # also cancels a recording already in progress (a follow-up window, Ctrl+B).
      send '/wake off'; send '/voice off'; want=1
    fi
    for _ in $(seq 1 12); do
      mic_open; [ $? -eq $want ] && break   # want: 0 = open, 1 = closed
      sleep 0.5
    done
    ;;
  *) out error bad_action ;;
esac
report
