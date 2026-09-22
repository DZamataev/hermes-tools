#!/usr/bin/env bash
# All checks for comp-count.
#
#   ./tests/run.sh      # backend + renderer, offline, no keys, no gateway
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

if [[ $status -eq 0 ]]; then
  echo "✓ all checks passed"
else
  echo "✗ some checks failed" >&2
fi
exit $status
