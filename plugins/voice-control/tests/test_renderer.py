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
export const Input = () => null
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
const { Chip, chipView, Panel, stateLabel, MusicSection, musicLine, PromptBox } = await import('./plugin.js')

const calls = []
const registered = []
const OFF_REPLY = { host: 'ct125-voice', listening: false, wake_enabled: false, error: null }
plugin.register({
  rest: async (path, opts) => {
    calls.push([path, opts])
    if (globalThis.__probeThrow) throw new Error(globalThis.__probeThrow)
    return globalThis.__probeReply ?? (path === '/music' ? { state: 'stopped', volume: 15 } : OFF_REPLY)
  },
  onDispose: () => {},
  register: c => registered.push(c)
})
check('registers-one-statusbar-item', registered.length === 1 && registered[0].area === 'statusBar.right')

// Walk the stub tree: invoke components, keep host elements' props (buttons carry onClick/disabled).
function collect(node, acc = { text: [], buttons: [], inputs: [] }) {
  if (node == null || node === false) return acc
  if (typeof node === 'string' || typeof node === 'number') { acc.text.push(String(node)); return acc }
  if (Array.isArray(node)) { node.forEach(n => collect(n, acc)); return acc }
  if (typeof node.t === 'function') {
    if (node.t.name === 'Button') acc.buttons.push(node.p)
    if (node.t.name === 'Input') acc.inputs.push(node.p)
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
const byLabel = (buttons, label) => buttons.find(b => b['aria-label'] === label || b.children === label)
let r = collect({ t: Panel, p: { data: ON, error: null, pending: false, onToggle: () => {} } })
check('panel-shows-host', r.text.includes('ct125-voice'), r.text.join('|'))
check('panel-on-offers-off', byLabel(r.buttons, 'Turn microphone off')?.disabled === false,
  JSON.stringify(r.buttons.map(b => b.children)))
r = collect({ t: Panel, p: { data: OFF, error: null, pending: false, onToggle: () => {} } })
check('panel-off-offers-on', !!byLabel(r.buttons, 'Turn microphone on'))
r = collect({ t: Panel, p: { data: unreachable, error: null, pending: false, onToggle: () => {} } })
check('panel-unknown-disables-toggle', byLabel(r.buttons, 'Turn microphone on')?.disabled === true)
check('panel-shows-error-message', r.text.includes('ssh to the assistant host failed'), r.text.join('|'))
r = collect({ t: Panel, p: { data: ON, error: null, pending: true, onToggle: () => {} } })
check('panel-pending-disables-toggle', byLabel(r.buttons, 'Turn microphone off')?.disabled === true && r.text.includes('switching…'))
r = collect({ t: Panel, p: { data: undefined, error: new Error('404'), pending: false, onToggle: () => {} } })
check('panel-backend-down-says-how-to-fix', r.text.some(t => t.includes('hermes plugins enable voice-control')))
check('panel-carries-the-music-block', r.text.includes('Музыка'), r.text.join('|'))
check('panel-carries-the-prompt-box', r.text.includes('Отправить'), r.text.join('|'))

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

// --- the music block: each state, then one click per button -------------------
const PLAYING = { state: 'playing', kind: 'jellyfin', artist: 'Кино', title: 'Группа крови', station: '', icy_title: '',
  pos: 2, count: 9, volume: 40, error: null }
const RADIO = { state: 'playing', kind: 'radio', artist: '', title: 'Наше радио', station: 'Наше радио',
  icy_title: 'Dua Lipa — Levitating', pos: 1, count: 1, volume: 25, error: null }
const STOPPED = { state: 'stopped', kind: null, artist: '', title: '', station: '', icy_title: '',
  pos: 0, count: 0, volume: 15, error: null }

check('music-line-jellyfin', musicLine(PLAYING) === 'Кино — Группа крови', musicLine(PLAYING))
check('music-line-radio', musicLine(RADIO) === 'Наше радио · Dua Lipa — Levitating', musicLine(RADIO))
check('music-line-radio-without-icy', musicLine({ ...RADIO, icy_title: '' }) === 'Наше радио', musicLine({ ...RADIO, icy_title: '' }))
check('music-line-stopped', musicLine(STOPPED) === 'ничего не играет', musicLine(STOPPED))
check('music-line-paused-marks-it', musicLine({ ...PLAYING, state: 'paused' }) === '⏸ Кино — Группа крови')
check('music-line-unknown', musicLine(null) === '—' && musicLine({ state: null }) === '—')

globalThis.__probeQuery = { data: PLAYING, error: null }
globalThis.__probePending = false
r = collect({ t: MusicSection, p: {} })
check('music-section-now-playing', r.text.includes('Кино — Группа крови'), r.text.join('|'))
check('music-section-shows-volume', r.text.includes('40'))
check('music-section-controls', ['⏯', '⏭', '⏹', '−', '+'].every(g => byLabel(r.buttons, g)),
  JSON.stringify(r.buttons.map(b => b.children)))
check('music-playing-enables-controls', r.buttons.length === 5 && r.buttons.every(b => b.disabled === false))

globalThis.__probeQuery = { data: STOPPED, error: null }
r = collect({ t: MusicSection, p: {} })
check('music-stopped-says-so', r.text.includes('ничего не играет'), r.text.join('|'))
check('music-stopped-disables-controls', r.buttons.length === 5 && r.buttons.every(b => b.disabled === true))

globalThis.__probeQuery = { data: PLAYING, error: null }
globalThis.__probePending = true
r = collect({ t: MusicSection, p: {} })
check('music-pending-disables-controls', r.text.includes('…') && r.buttons.every(b => b.disabled === true))

globalThis.__probeQuery = { data: { state: null, error: 'unreachable', message: 'ssh to the assistant host failed' }, error: null }
globalThis.__probePending = false
r = collect({ t: MusicSection, p: {} })
check('music-shows-error-message', r.text.includes('ssh to the assistant host failed'), r.text.join('|'))
check('music-unknown-disables-controls', r.buttons.every(b => b.disabled === true))

globalThis.__probeQuery = { data: PLAYING, error: null }
globalThis.__probeReply = PLAYING
r = collect({ t: MusicSection, p: {} })
const press = async glyph => { calls.length = 0; await byLabel(r.buttons, glyph).onClick(); return calls.at(-1) }
let c = await press('⏯')
check('music-toggle-posts', c[0] === '/music' && c[1].method === 'POST' && c[1].body.cmd === 'toggle', JSON.stringify(c))
check('music-toggle-allows-the-backend-timeout', c[1].timeoutMs >= 15000)
c = await press('⏭')
check('music-next-posts', c[1].body.cmd === 'next', JSON.stringify(c[1].body))
c = await press('⏹')
check('music-stop-posts', c[1].body.cmd === 'stop', JSON.stringify(c[1].body))
c = await press('+')
check('music-volume-up-posts', c[1].body.cmd === 'volume' && c[1].body.arg === '+10', JSON.stringify(c[1].body))
c = await press('−')
check('music-volume-down-posts', c[1].body.arg === '-10', JSON.stringify(c[1].body))
check('music-stores-the-fresh-status', globalThis.__probeSet?.at(-1)?.state === 'playing', JSON.stringify(globalThis.__probeSet))
check('music-clears-pending', globalThis.__probePending === false)

// --- a failed command must not wipe the status it already knows ---------------
globalThis.__probeQuery = { data: PLAYING, error: null }
globalThis.__probeReply = { state: null, error: 'timeout', message: 'the assistant host did not answer in time' }
r = collect({ t: MusicSection, p: {} })
await press('⏯')
const failed = globalThis.__probeSet?.at(-1)
check('music-failure-keeps-last-status', failed?.state === 'playing', JSON.stringify(failed))
check('music-failure-attaches-the-error', failed?.error === 'timeout'
  && failed?.message === 'the assistant host did not answer in time', JSON.stringify(failed))
globalThis.__probeQuery = { data: failed, error: null }
globalThis.__probePending = false
r = collect({ t: MusicSection, p: {} })
check('music-failure-keeps-the-now-playing-line', r.text.includes('Кино — Группа крови'), r.text.join('|'))
check('music-failure-keeps-controls-enabled', r.buttons.length === 5 && r.buttons.every(b => b.disabled === false),
  JSON.stringify(r.buttons.map(b => b.disabled)))
const errorBox = (function find(n) {
  if (!n || typeof n !== 'object') return null
  if (n.p?.className === 'vc-error') return [].concat(n.p.children ?? []).join('')
  for (const c of [].concat(n.p?.children ?? [])) { const f = find(c); if (f) return f }
  return null
})(MusicSection({}))
check('music-failure-shows-the-error-in-red', errorBox === 'the assistant host did not answer in time', JSON.stringify(errorBox))

// --- the prompt box: type into the CLI's session; the answer comes back as speech ----
// The stub useState() hands out one global value, so the box keeps its whole draft in one object.
const errorText = tree => (function find(n) {
  if (!n || typeof n !== 'object') return null
  if (n.p?.className === 'vc-error') return [].concat(n.p.children ?? []).join('')
  for (const c of [].concat(n.p?.children ?? [])) { const f = find(c); if (f) return f }
  return null
})(tree)

globalThis.__probeThrow = null
globalThis.__probeReply = { sent: true, error: null, message: '' }
delete globalThis.__probePending
r = collect({ t: PromptBox, p: {} })
check('prompt-box-has-a-field-and-a-button', r.inputs.length === 1 && !!byLabel(r.buttons, 'Отправить'),
  JSON.stringify({ inputs: r.inputs.length, buttons: r.buttons.map(b => b.children) }))
check('prompt-box-idle-is-enabled', r.inputs[0].disabled === false && byLabel(r.buttons, 'Отправить').disabled === false,
  JSON.stringify([r.inputs[0].disabled, byLabel(r.buttons, 'Отправить').disabled]))

calls.length = 0
r.inputs[0].onChange({ target: { value: '  привет  ' } })
r = collect({ t: PromptBox, p: {} })
check('prompt-box-keeps-what-was-typed', r.inputs[0].value === '  привет  ', JSON.stringify(r.inputs[0].value))
await byLabel(r.buttons, 'Отправить').onClick()
let pc = calls.at(-1)
check('prompt-posts-the-trimmed-text', pc[0] === '/prompt' && pc[1].method === 'POST' && pc[1].body.text === 'привет',
  JSON.stringify(pc))
check('prompt-allows-the-backend-timeout', pc[1].timeoutMs >= 60000, String(pc[1].timeoutMs))
const sentDraft = globalThis.__probePending
check('prompt-clears-the-field-on-success', sentDraft?.text === '' && sentDraft?.pending === false, JSON.stringify(sentDraft))
check('prompt-says-it-was-sent', !sentDraft?.status?.error && sentDraft?.status?.message.includes('Отправлено'),
  JSON.stringify(sentDraft?.status))
r = collect({ t: PromptBox, p: {} })
check('prompt-renders-the-status', r.text.some(t => t.includes('Отправлено')), r.text.join('|'))
// Screen readers only hear it if the line is a live region.
const liveStatus = (function find(n) {
  if (!n || typeof n !== 'object') return null
  if (n.p?.role === 'status' && n.p?.['aria-live'] === 'polite') return [].concat(n.p.children ?? []).join('')
  for (const c of [].concat(n.p?.children ?? [])) { const f = find(c); if (f) return f }
  return null
})(PromptBox({}))
check('prompt-status-is-a-live-region', typeof liveStatus === 'string' && liveStatus.includes('Отправлено'),
  JSON.stringify(liveStatus))

calls.length = 0
globalThis.__probeReply = { sent: true, error: null }
r.inputs[0].onChange({ target: { value: 'громче' } })
r = collect({ t: PromptBox, p: {} })
let prevented = false
r.inputs[0].onKeyDown({ key: 'Enter', preventDefault: () => { prevented = true } })
await new Promise(done => setTimeout(done, 0))
check('prompt-enter-submits', prevented && calls.at(-1)?.[0] === '/prompt' && calls.at(-1)[1].body.text === 'громче',
  JSON.stringify(calls))
calls.length = 0
r.inputs[0].onKeyDown({ key: 'a', preventDefault() {} })
check('prompt-other-keys-do-not-submit', calls.length === 0, JSON.stringify(calls))

globalThis.__probePending = { text: 'ещё', pending: true, status: null }
r = collect({ t: PromptBox, p: {} })
check('prompt-pending-disables-the-field-and-button',
  r.inputs[0].disabled === true && byLabel(r.buttons, 'Отправить').disabled === true,
  JSON.stringify([r.inputs[0].disabled, byLabel(r.buttons, 'Отправить').disabled]))
calls.length = 0
await byLabel(r.buttons, 'Отправить').onClick()
check('prompt-pending-does-not-post', calls.length === 0, JSON.stringify(calls))

globalThis.__probePending = { text: '   ', pending: false, status: null }
r = collect({ t: PromptBox, p: {} })
calls.length = 0
await byLabel(r.buttons, 'Отправить').onClick()
check('prompt-blank-does-not-post', calls.length === 0, JSON.stringify(calls))

// A refused send: the text stays in the field, the backend's reason is drawn in red.
globalThis.__probeReply = { sent: false, error: 'busy',
  message: 'the assistant is answering right now; try again when it finishes' }
globalThis.__probePending = { text: 'привет', pending: false, status: null }
r = collect({ t: PromptBox, p: {} })
await byLabel(r.buttons, 'Отправить').onClick()
const refusedDraft = globalThis.__probePending
check('prompt-failure-keeps-the-text', refusedDraft?.text === 'привет' && refusedDraft?.status?.error === 'busy',
  JSON.stringify(refusedDraft))
r = collect({ t: PromptBox, p: {} })
check('prompt-failure-shows-the-reason', r.text.some(t => t.includes('answering right now')), r.text.join('|'))
check('prompt-failure-is-drawn-in-red',
  (errorText(PromptBox({})) || '').includes('answering right now'), JSON.stringify(errorText(PromptBox({}))))

globalThis.__probeThrow = 'backend is down'
globalThis.__probePending = { text: 'привет', pending: false, status: null }
r = collect({ t: PromptBox, p: {} })
await byLabel(r.buttons, 'Отправить').onClick()
const thrownDraft = globalThis.__probePending
check('prompt-request-failure-is-shown',
  thrownDraft?.status?.error === 'request_failed' && thrownDraft.status.message.includes('backend is down')
  && thrownDraft?.pending === false, JSON.stringify(thrownDraft))
globalThis.__probeThrow = null

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
