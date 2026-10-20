#!/usr/bin/env python3
"""End-to-end against the REAL assistant host (opt-in: ``tests/run.sh --e2e``).

Mounts this plugin's router in a throwaway FastAPI app (not the user's gateway), then
over HTTP: reads the state, flips the microphone, checks it flipped, flips it back,
and leaves the assistant as it found it. Needs the ssh host from the plugin settings
to be reachable and the voice Hermes to be idle.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERMES = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/frenzy/.hermes/hermes-agent")
sys.path.insert(0, str(HERMES))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

spec = importlib.util.spec_from_file_location("vc_api", ROOT / "dashboard" / "plugin_api.py")
vc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vc)

app = FastAPI()
app.include_router(vc.router, prefix="/api/plugins/voice-control")
client = TestClient(app)
base = "/api/plugins/voice-control/state"

start = client.get(base).json()
print("start:", start)
if start.get("listening") is None:
    print(f"✗ host not usable: {start.get('error')} — {start.get('message')}")
    sys.exit(1)

ok = True
original = start["listening"]
try:
    flipped = client.post(base, json={"listening": not original}).json()
    print("flipped:", flipped)
    ok &= flipped.get("listening") is (not original) and flipped.get("wake_enabled") is (not original) and not flipped.get("error")
    again = client.get(base).json()
    ok &= again.get("listening") is (not original)
    bad = client.post(base, json={"listening": "yes"})
    ok &= bad.status_code == 422
finally:
    back = client.post(base, json={"listening": original}).json()
    print("restored:", back)
    ok &= back.get("listening") is original

print("✓ e2e passed" if ok else "✗ e2e failed")
sys.exit(0 if ok else 1)
