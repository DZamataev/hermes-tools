#!/usr/bin/env python3
"""Renderer checks: build a stub SDK, load plugin.js, exercise it.

`plugin.js` imports `@hermes/plugin-sdk`, which only exists inside the desktop app,
so this stands up a throwaway node_modules with the exports the plugin uses. The
stub `jsx()` returns `{t, p}`; `collect` walks that tree and invokes components.

Run via ``tests/run.sh``.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

PLUGIN = pathlib.Path(__file__).resolve().parent.parent / "desktop" / "plugin.js"

root = pathlib.Path(tempfile.mkdtemp(prefix="voice-control-probe-"))
sdk = root / "node_modules" / "@hermes" / "plugin-sdk"
react = root / "node_modules" / "react"
sdk.mkdir(parents=True)
(react / "jsx-runtime").mkdir(parents=True)
(root / "package.json").write_text(json.dumps({"name": "probe", "type": "module"}))
(sdk / "package.json").write_text(json.dumps({"name": "@hermes/plugin-sdk", "type": "module", "main": "index.js"}))
(sdk / "index.js").write_text("""
export const Button = () => null
export const Popover = () => null
export const PopoverContent = () => null
export const PopoverTrigger = () => null
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
export const useQuery = () => globalThis.__probeQuery ?? { data: null, error: null }
export const useQueryClient = () => ({ setQueryData(key, value) { (globalThis.__probeSet ??= []).push(value) } })
""")
(react / "package.json").write_text(json.dumps({
    "name": "react", "type": "module",
    "exports": {".": "./index.js", "./jsx-runtime": "./jsx-runtime/index.js"}}))
(react / "index.js").write_text(
    "export const useState = initial => [globalThis.__probePending ?? initial, v => { globalThis.__probePending = v }]\n"
    "export default { useState }\n")
(react / "jsx-runtime" / "index.js").write_text(
    "export const jsx = (t, p) => ({ t, p })\nexport const jsxs = (t, p) => ({ t, p })\n")
shutil.copy(PLUGIN, root / "plugin.js")

(root / "probe.mjs").write_text(r"""
const failures = []
let checks = 0
const check = (name, ok, detail = '') => { checks++; if (!ok) failures.push(name + (detail ? ` — ${detail}` : '')) }

globalThis.document = {
  createElement: () => ({ textContent: '', remove() { this.removed = true } }),
  head: { append() {} }
}
const plugin = (await import('./plugin.js')).default
const { Chip, chipView, Panel, stateLabel } = await import('./plugin.js')

const calls = []
const registered = []
plugin.register({
  rest: async (path, opts) => { calls.push([path, opts]); return { host: 'ct125-voice', listening: false, wake_enabled: false, error: null } },
  onDispose: () => {},
  register: c => registered.push(c)
})
check('registers-one-statusbar-item', registered.length === 1 && registered[0].area === 'statusBar.right')

// Walk the stub tree: invoke components, keep host elements' props (buttons carry onClick/disabled).
function collect(node, acc = { text: [], buttons: [] }) {
  if (node == null || node === false) return acc
  if (typeof node === 'string' || typeof node === 'number') { acc.text.push(String(node)); return acc }
  if (Array.isArray(node)) { node.forEach(n => collect(n, acc)); return acc }
  if (typeof node.t === 'function') {
    if (node.t.name === 'Button') acc.buttons.push(node.p)
    const out = node.t(node.p)
    return collect(out ?? node.p?.children, acc)
  }
  return collect(node.p?.children, acc)
}

// --- chip states ----------------------------------------------------------
const ON = { host: 'ct125-voice', listening: true, wake_enabled: true, error: null, message: '' }
const OFF = { ...ON, listening: false, wake_enabled: false }
check('chip-listening', chipView(ON, null, false).glyph === '🎙' && chipView(ON, null, false).tone === 'on')
check('chip-off', chipView(OFF, null, false).glyph === '🔇' && chipView(OFF, null, false).tone === 'off')
check('chip-pending-wins', chipView(ON, null, true).glyph === '…')
check('chip-backend-down', chipView(null, new Error('404'), false).glyph === '—')
const unreachable = { host: 'ct125-voice', listening: null, error: 'unreachable', message: 'ssh to the assistant host failed' }
check('chip-unknown-is-dash-not-off', chipView(unreachable, null, false).glyph === '—', JSON.stringify(chipView(unreachable, null, false)))
check('chip-unknown-title-says-why', chipView(unreachable, null, false).title.includes('ssh to the assistant host failed'))
check('chip-title-names-host', chipView(ON, null, false).title.includes('ct125-voice'))
check('state-labels', [stateLabel(ON), stateLabel(OFF), stateLabel(unreachable), stateLabel(null)].join('|') === 'listening|microphone off|unknown|unknown')

// --- popover ----------------------------------------------------------------
let r = collect({ t: Panel, p: { data: ON, error: null, pending: false, onToggle: () => {} } })
const toggle = r.buttons.at(-1)
check('panel-shows-host', r.text.includes('ct125-voice'), r.text.join('|'))
check('panel-on-offers-off', toggle?.children === 'Turn microphone off' && toggle.disabled === false, JSON.stringify(toggle))
r = collect({ t: Panel, p: { data: OFF, error: null, pending: false, onToggle: () => {} } })
check('panel-off-offers-on', r.buttons.at(-1)?.children === 'Turn microphone on')
r = collect({ t: Panel, p: { data: unreachable, error: null, pending: false, onToggle: () => {} } })
check('panel-unknown-disables-toggle', r.buttons.at(-1)?.disabled === true)
check('panel-shows-error-message', r.text.includes('ssh to the assistant host failed'), r.text.join('|'))
r = collect({ t: Panel, p: { data: ON, error: null, pending: true, onToggle: () => {} } })
check('panel-pending-disables-toggle', r.buttons.at(-1)?.disabled === true && r.text.includes('switching…'))
r = collect({ t: Panel, p: { data: undefined, error: new Error('404'), pending: false, onToggle: () => {} } })
check('panel-backend-down-says-how-to-fix', r.text.some(t => t.includes('hermes plugins enable voice-control')))

// --- the toggle posts the opposite state and stores the answer -----------------
globalThis.__probeQuery = { data: ON, error: null }
globalThis.__probePending = false
const chipTree = collect({ t: Chip, p: {} })
const panelNode = (function find(n) {
  if (!n || typeof n !== 'object') return null
  if (n.t === Panel) return n
  for (const c of [].concat(n.p?.children ?? [])) { const f = find(c); if (f) return f }
  return null
})(Chip({}))
await panelNode.p.onToggle()
check('toggle-posts-opposite', calls.length === 1 && calls[0][0] === '/state' && calls[0][1].method === 'POST'
  && calls[0][1].body.listening === false, JSON.stringify(calls))
check('toggle-allows-a-long-request', calls[0][1].timeoutMs >= 60000)
check('toggle-stores-reply', globalThis.__probeSet?.at(-1)?.listening === false, JSON.stringify(globalThis.__probeSet))
check('toggle-clears-pending', globalThis.__probePending === false)

if (failures.length) {
  console.log(`renderer: ${failures.length} of ${checks} checks FAILED`)
  for (const f of failures) console.log('  ✗', f)
  process.exit(1)
}
console.log(`renderer: ${checks} checks passed`)
""")

done = subprocess.run(["node", "probe.mjs"], cwd=root, capture_output=True, text=True)
print((done.stdout + done.stderr).strip())
shutil.rmtree(root, ignore_errors=True)
sys.exit(done.returncode)
