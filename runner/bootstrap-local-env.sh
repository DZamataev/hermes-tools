#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
ENV_FILE="$ROOT/.env.local"

umask 077
if [[ ! -e "$ENV_FILE" ]]; then
  : > "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

has_assignment() {
  awk -v name="$1" -F '=' '$1 == name { found = 1; exit } END { exit !found }' "$ENV_FILE"
}

append_if_absent() {
  if ! has_assignment "$1"; then
    print -r -- "$1=$2" >> "$ENV_FILE"
  fi
}

append_if_absent "OPENWEBUI_API_KEY" ""
append_if_absent "BRIDGE_HOST_PORT" "8787"
append_if_absent "BRIDGE_LOG_LEVEL" "info"

if awk '
  BEGIN { found = 0 }
  /^HERMES_BRIDGE_SECRET=/ {
    found = 1
    value = $0
    sub(/^HERMES_BRIDGE_SECRET=/, "", value)
    exit value ~ /^[[:space:]]*$/ ? 0 : 1
  }
  END { if (!found) exit 1 }
' "$ENV_FILE"; then
  secret_file="$(mktemp)"
  temp_file="$(mktemp "${ENV_FILE}.XXXXXX")"
  trap 'rm -f "$secret_file" "$temp_file"' EXIT
  openssl rand -hex 32 > "$secret_file"
  chmod 600 "$secret_file" "$temp_file"
  awk -v secret_file="$secret_file" '
    BEGIN { getline secret < secret_file; close(secret_file); replaced = 0 }
    /^HERMES_BRIDGE_SECRET=/ && !replaced {
      print "HERMES_BRIDGE_SECRET=" secret
      replaced = 1
      next
    }
    { print }
  ' "$ENV_FILE" > "$temp_file"
  mv "$temp_file" "$ENV_FILE"
  trap - EXIT
  rm -f "$secret_file"
elif ! has_assignment "HERMES_BRIDGE_SECRET"; then
  secret_file="$(mktemp)"
  trap 'rm -f "$secret_file"' EXIT
  openssl rand -hex 32 > "$secret_file"
  print -r -- "HERMES_BRIDGE_SECRET=$(<"$secret_file")" >> "$ENV_FILE"
  rm -f "$secret_file"
  trap - EXIT
fi
