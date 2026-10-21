#!/usr/bin/env bash
# All checks for voice-control.
#
#   ./tests/run.sh          # backend + renderer + remote.sh syntax (offline, ssh faked)
#   ./tests/run.sh --e2e    # also flip the real assistant's microphone off and back on
#
# The e2e pass changes the live assistant (and restores it), so it is opt-in.
set -uo pipefail

cd "$(dirname "$0")/.."
HERMES="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"
PY="$HERMES/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "✗ no interpreter at $PY — set HERMES_AGENT_DIR to your hermes-agent checkout" >&2
  exit 2
fi

status=0

echo "── remote.sh"
if bash -n dashboard/remote.sh; then echo "remote.sh: syntax ok"; else status=1; fi

echo "── remote.sh behaviour (stub tmux)"
python3 tests/test_remote.py || status=1

echo "── backend"
"$PY" tests/test_backend.py "$HERMES" || status=1

echo "── renderer"
python3 tests/test_renderer.py || status=1

if [[ "${1:-}" == "--e2e" ]]; then
  echo "── end-to-end (live assistant host)"
  "$PY" tests/test_e2e.py "$HERMES" || status=1
fi

if [[ $status -eq 0 ]]; then
  echo "✓ all checks passed"
else
  echo "✗ some checks failed" >&2
fi
exit $status
