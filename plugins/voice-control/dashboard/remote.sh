#!/bin/bash
# voice-control, remote half. Runs ON the assistant host, fed over ssh:
#   ssh <host> bash -s -- <status|on|off> <tmux-session>  < remote.sh
# Drives the Hermes CLI that hermes-voice.service keeps in a tmux session, and
# prints key=value lines the backend parses. Nothing is installed on the host.
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

report() {
  if mic_open; then out listening true; else out listening false; fi
  out wake_enabled "$(wake_enabled)"
}

if ! tmux has-session -t "$session" 2>/dev/null; then
  out error no_session
  report
  exit 0
fi

case $action in
  status) ;;
  on|off)
    for _ in $(seq 1 40); do busy || break; sleep 0.5; done
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
