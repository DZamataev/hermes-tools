#!/usr/bin/env bash
# All checks for provider-limits.
#
#   ./tests/run.sh              # backend + renderer (offline, no keys needed)
#   ./tests/run.sh --e2e        # also mount the real route and call live upstreams
#
# The e2e pass needs network and real API keys, so it is opt-in: a failure there
# usually means an upstream is down, which is not the same as this code being
# broken, and a check that cries wolf stops being read.
set -uo pipefail

cd "$(dirname "$0")/.."
HERMES="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"
PY="$HERMES/venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "✗ no interpreter at $PY — set HERMES_AGENT_DIR to your hermes-agent checkout" >&2
  exit 2
fi

status=0

echo "── backend"
"$PY" tests/test_backend.py "$HERMES" || status=1

echo "── renderer"
python3 tests/test_renderer.py || status=1

if [[ "${1:-}" == "--e2e" ]]; then
  echo "── end-to-end (live upstreams)"
  "$PY" tests/test_e2e.py "$HERMES" 2>&1 | grep -v '"jsonrpc"' || status=1
fi

if [[ $status -eq 0 ]]; then
  echo "✓ all checks passed"
else
  echo "✗ some checks failed" >&2
fi
exit $status
