#!/usr/bin/env python3
"""End-to-end: mount the real FastAPI app, call the route, render the real chip.

This is the only check that exercises the whole path — route mount, auth,
upstream fetches, and the shipped `plugin.js` against the payload the backend
actually produced. It talks to the live upstreams, so it needs network and real
API keys; `tests/run.sh` skips it when they are absent.

It deliberately renders through `plugin.js` rather than reimplementing the chip
rule in Python: a second copy of the rule can agree with itself while disagreeing
with what ships.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
HERMES = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/frenzy/.hermes/hermes-agent")
TOKEN = "provider-limits-e2e-probe"
PORT = 8807

sys.path.insert(0, str(HERMES))
os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] = TOKEN
# Prove the route survives the headless guard that 404s everything else.
os.environ["HERMES_SERVE_HEADLESS"] = "1"

import uvicorn  # noqa: E402

from hermes_cli.web_server import app  # noqa: E402
from hermes_cli.web_server_dashboard import _mount_plugin_api_routes  # noqa: E402

_mount_plugin_api_routes()

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        failures.append(f"{name}{f' — {detail}' if detail else ''}")


config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
server = uvicorn.Server(config)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)

url = f"http://127.0.0.1:{PORT}/api/plugins/provider-limits/limits"

# Unauthenticated callers must be refused.
try:
    urllib.request.urlopen(urllib.request.Request(url), timeout=30)
    check("route-requires-auth", False, "unauthenticated request succeeded")
except urllib.error.HTTPError as exc:
    check("route-requires-auth", exc.code == 401, f"got HTTP {exc.code}")

request = urllib.request.Request(url)
request.add_header("Authorization", f"Bearer {TOKEN}")
with urllib.request.urlopen(request, timeout=90) as response:
    payload = json.load(response)

server.should_exit = True

providers = payload.get("providers", [])
check("providers-returned", len(providers) > 0, "no providers configured?")
check("every-provider-has-poolwindows", all("poolWindows" in p for p in providers))
check("every-bucket-has-window",
      all(b.get("window") for p in providers for b in p["buckets"]),
      str([b.get("label") for p in providers for b in p["buckets"] if not b.get("window")]))
check("services-returned", len(payload.get("services", [])) == 2,
      str([s.get("label") for s in payload.get("services", [])]))
check("no-key-in-payload", not any(
    tok and tok in json.dumps(payload)
    for tok in (os.environ.get("CODEX_LB_API_KEY"), os.environ.get("HERMES_CUSTOM_TEAMCLAUDE_API_KEY"))))

for provider in providers:
    if provider.get("ok") is False:
        print(f"  note: {provider['label']} did not answer ({provider.get('error')})")

# Render the shipped chip against this exact payload.
work = pathlib.Path(tempfile.mkdtemp(prefix="provider-limits-e2e-"))
sdk = work / "node_modules" / "@hermes" / "plugin-sdk"
react = work / "node_modules" / "react"
sdk.mkdir(parents=True)
(react / "jsx-runtime").mkdir(parents=True)
(work / "package.json").write_text(json.dumps({"name": "e2e", "type": "module"}))
(sdk / "package.json").write_text(json.dumps(
    {"name": "@hermes/plugin-sdk", "type": "module", "main": "index.js"}))
(sdk / "index.js").write_text("""
export const Button = () => null
export const Popover = () => null
export const PopoverContent = () => null
export const PopoverTrigger = () => null
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const useQuery = () => globalThis.__probeQuery
export const useQueryClient = () => ({ setQueryData() {} })
""")
(react / "package.json").write_text(json.dumps({
    "name": "react", "type": "module",
    "exports": {".": "./index.js", "./jsx-runtime": "./jsx-runtime/index.js"}}))
(react / "index.js").write_text(
    "export const useState = i => [i, () => {}]\nexport default { useState }\n")
(react / "jsx-runtime" / "index.js").write_text(
    "export const jsx = (t, p) => ({ t, p })\nexport const jsxs = (t, p) => ({ t, p })\n")
shutil.copy(ROOT / "desktop" / "plugin.js", work / "plugin.js")
(work / "render.mjs").write_text(f"""
globalThis.document = {{ createElement: () => ({{ textContent: '', remove() {{}} }}), head: {{ append() {{}} }} }}
const payload = {json.dumps(payload)}
globalThis.__probeQuery = {{ data: payload, error: null, isFetching: false }}
const plugin = (await import('./plugin.js')).default
const reg = []
plugin.register({{ rest: async () => payload, onDispose() {{}}, register: c => reg.push(c) }})
const collect = node => {{
  const out = []
  ;(function walk(n) {{
    if (n === null || n === undefined || n === false) return
    if (typeof n === 'string' || typeof n === 'number') {{ out.push(String(n)); return }}
    if (Array.isArray(n)) {{ n.forEach(walk); return }}
    if (typeof n !== 'object') return
    if (typeof n.t === 'function') {{
      const r = n.t(n.p ?? {{}})
      if (r !== null && r !== undefined) {{ walk(r); return }}
    }}
    walk(n.p?.children)
  }})(node)
  return out.join(' ')
}}
const root = reg[0].render()
const popover = root.t(root.p ?? {{}})
console.log(JSON.stringify({{
  chip: collect(popover.p.children[0]),
  panel: collect(popover.p.children[1])
}}))
""")
render = subprocess.run(["node", "render.mjs"], cwd=work, capture_output=True, text=True)
shutil.rmtree(work, ignore_errors=True)

if render.returncode != 0:
    check("chip-renders", False, render.stderr.strip()[:400])
else:
    rendered = json.loads(render.stdout)
    chip, panel = rendered["chip"], rendered["panel"]
    print(f"  chip: {chip}")
    figures = [part for part in chip.split("|")]
    check("chip-one-figure-per-provider", len(figures) == len(providers),
          f"{len(figures)} figures for {len(providers)} providers")
    check("chip-has-no-names", not any(c.isalpha() and c.isascii() for c in chip), chip)
    # Every provider must be traceable from the panel's legend.
    check("panel-names-every-provider",
          all(p["label"] in panel for p in providers),
          str([p["label"] for p in providers if p["label"] not in panel]))

if failures:
    print(f"e2e: {len(failures)} FAILED")
    for line in failures:
        print(f"  ✗ {line}")
    sys.exit(1)

print("e2e: route, payload and rendered chip all verified")
