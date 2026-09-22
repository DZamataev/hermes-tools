#!/usr/bin/env python3
"""Renderer checks: build a stub SDK, load plugin.js, exercise it.

`plugin.js` imports `@hermes/plugin-sdk`, which only exists inside the desktop
app, so this stands up a throwaway node_modules with the handful of exports the
plugin actually uses. The stub `jsx()` returns `{t, p}` rather than real React
elements; `collectText` walks that tree, invoking components itself.

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

root = pathlib.Path(tempfile.mkdtemp(prefix="provider-limits-probe-"))
sdk = root / "node_modules" / "@hermes" / "plugin-sdk"
react = root / "node_modules" / "react"
sdk.mkdir(parents=True)
(react / "jsx-runtime").mkdir(parents=True)

(root / "package.json").write_text(json.dumps({"name": "probe", "type": "module"}))
(sdk / "package.json").write_text(json.dumps(
    {"name": "@hermes/plugin-sdk", "type": "module", "main": "index.js"}))
(sdk / "index.js").write_text("""
export const Button = () => null
export const Popover = () => null
export const PopoverContent = () => null
export const PopoverTrigger = () => null
export const STATUSBAR_AREAS = { left: 'statusBar.left', right: 'statusBar.right' }
// The probe drives the query layer through this global, so a rendered chip can
// be exercised against a chosen payload.
export const useQuery = () => globalThis.__probeQuery ?? { data: null, error: null, isFetching: false }
export const useQueryClient = () => ({ setQueryData() {} })
""")
(react / "package.json").write_text(json.dumps({
    "name": "react", "type": "module",
    "exports": {".": "./index.js", "./jsx-runtime": "./jsx-runtime/index.js"}}))
(react / "index.js").write_text(
    "export const useState = initial => [initial, () => {}]\n"
    "export const useEffect = () => {}\n"
    "export const useRef = initial => ({ current: initial })\n"
    "export default { useState, useEffect, useRef }\n")
(react / "jsx-runtime" / "index.js").write_text(
    "export const jsx = (t, p) => ({ t, p })\nexport const jsxs = (t, p) => ({ t, p })\n")

shutil.copy(PLUGIN, root / "plugin.js")

(root / "probe.mjs").write_text(r"""
const failures = []
let checks = 0
const check = (name, ok, detail = '') => {
  checks++
  if (!ok) failures.push(name + (detail ? ` — ${detail}` : ''))
}

const appended = []
globalThis.document = {
  createElement: () => ({ textContent: '', remove() { this.removed = true } }),
  head: { append(node) { appended.push(node) } }
}

const plugin = (await import('./plugin.js')).default
const {
  ChipExplainer, chipEntries, clock24, downServices, list, pct,
  relativeReset, resetAt24, ServiceRow, ServicesBanner, shortWindowBucket, tone
} = await import('./plugin.js')

const registered = []
const disposers = []
plugin.register({
  rest: async () => ({ providers: [] }),
  onDispose: fn => disposers.push(fn),
  register: contribution => registered.push(contribution)
})

check('registers-one-statusbar-item',
  registered.length === 1 && registered[0].area === 'statusBar.right',
  JSON.stringify(registered.map(r => r.area)))

// --- percentages and tone -------------------------------------------------

check('tone-thresholds', [3, 10, 11, 25, 26, 90].map(tone).join(',') === 'low,low,warn,warn,ok,ok')
check('pct-rejects-nonnumbers',
  [null, undefined, NaN, Infinity, '50', true].every(v => pct(v) === null))
check('pct-clamps', pct(-5) === 0 && pct(150) === 100 && pct(42.4) === 42.4)

// --- the 5h selection -----------------------------------------------------

// Realistic labels, not "A"/"B": one-letter names would let a chip that wrongly
// prints provider names still pass the "numbers only" check below.
const fixture = [
  { id: 'a', label: 'TeamClaude', ok: true, buckets: [
    { key: '5h', label: '5 hours', window: '5h', remainingPct: 80 }] },
  { id: 'b', label: 'Codex LB', ok: true, buckets: [
    { key: '7d', label: 'Week', window: '7d', remainingPct: 4 },
    { key: '5h:credits', label: '5 hours (credits)', window: '5h', remainingPct: 44 },
    { key: '5h:cost', label: '5 hours (cost_usd)', window: '5h', remainingPct: 91 }] },
  { id: 'c', label: 'Codex LB OneClick', ok: false, buckets: [] }
]

check('picks-5h-by-window', shortWindowBucket(fixture[0])?.value === 80)
check('picks-tightest-of-several-5h', shortWindowBucket(fixture[1])?.value === 44)
check('no-5h-row-is-null', shortWindowBucket(fixture[2]) === null && shortWindowBucket({}) === null)
// Selection must key on `window`, never on label text: labels are prose and
// gain "(cost_usd)" suffixes, so text matching would silently rot.
check('ignores-label-text',
  shortWindowBucket({ buckets: [{ label: '5 hours', window: '7d', remainingPct: 3 }] }) === null)

// --- chip entries ---------------------------------------------------------

const entries = chipEntries(fixture, [])
check('keeps-every-provider', entries.length === fixture.length)
check('order-follows-config', entries.map(e => e.id).join(',') === 'a,b,c')
check('failed-provider-has-no-number', entries[2].value === null && entries[2].tone === 'unknown')
check('skips-nonnumeric', chipEntries(
  [{ id: 'a', label: 'A', ok: true, buckets: [
    { window: '5h', remainingPct: NaN }, { window: '5h', remainingPct: null }] }], [])[0].value === null)
// An upstream incident outranks a healthy percentage.
check('outage-overrides-tone',
  chipEntries(fixture, [{ id: 'o', label: 'OpenAI', ok: false }]).every(e => e.tone === 'down'))

// --- the rendered chip ----------------------------------------------------

const collectText = node => {
  const out = []
  ;(function walk(n) {
    if (n === null || n === undefined || n === false) return
    if (typeof n === 'string' || typeof n === 'number') { out.push(String(n)); return }
    if (Array.isArray(n)) { n.forEach(walk); return }
    if (typeof n !== 'object') return
    // Components render; the SDK stubs return null, which would truncate the
    // tree, so fall through to their children in that case.
    if (typeof n.t === 'function') {
      const rendered = n.t(n.p ?? {})
      if (rendered !== null && rendered !== undefined) { walk(rendered); return }
    }
    walk(n.p?.children)
  })(node)
  return out.join(' ')
}

globalThis.__probeQuery = { data: { providers: fixture, services: [] }, error: null, isFetching: false }
const root = registered[0].render()
const popover = root.t(root.p ?? {})
// children[0] is the trigger (the status bar); children[1] is the panel.
const chipText = collectText(popover.p.children[0])
check('chip-renders-numbers-only', !/[A-Za-z]{3,}/.test(chipText), JSON.stringify(chipText))
check('chip-uses-pipe-separator', chipText.includes('|'), JSON.stringify(chipText))
check('chip-one-figure-per-provider',
  (chipText.match(/\d+%|—/g) ?? []).length === fixture.length, JSON.stringify(chipText))
globalThis.__probeQuery = undefined

// --- the explainer --------------------------------------------------------

const explain = (p, s) => ChipExplainer({ providers: p, services: s ?? [] }).p.children

const real = [
  { id: 'cl', label: 'Codex LB', ok: true, buckets: [
    { key: '5h', label: '5 hours', window: '5h', remainingPct: 98.4 },
    { key: 'w', label: 'Week', window: '7d', remainingPct: 4.2 }] },
  { id: 'tc', label: 'TeamClaude', ok: true, buckets: [
    { key: '5h', label: '5 hours', window: '5h', remainingPct: 79.1 },
    { key: 'w', label: 'Week', window: '7d', remainingPct: 40.7 }] }
]
const realText = explain(real)
// The legend is the ONLY place a bare number can be traced to its provider.
check('explainer-names-each-5h',
  realText.includes('Codex LB 98%') && realText.includes('TeamClaude 79%') && !realText.includes('4%'),
  realText)
// The chip covers one window; a nearly-empty weekly limit must not hide.
check('explainer-warns-other-windows', realText.includes('Weekly and other windows are not'))
check('explainer-marks-missing',
  explain([real[0], { id: 'x', label: 'Broken', ok: false, buckets: [] }]).includes('no 5-hour limit'))
check('explainer-reports-outage',
  explain(real, [{ id: 'o', label: 'OpenAI', ok: false }]).includes('incident'))
check('explainer-empty-claims-nothing', explain([]).includes('no provider is configured'))

// --- upstream service banner ----------------------------------------------

const rowState = s => ServiceRow({ service: s }).p['data-state']
const UP = { id: 'a', label: 'Anthropic', ok: true, description: 'All Systems Operational',
             component: 'Claude API', componentStatus: 'operational', incidents: [] }
const DOWN = { id: 'o', label: 'OpenAI', ok: false, description: 'Partial System Outage',
               component: 'Codex API', componentStatus: 'major_outage',
               incidents: ['Elevated errors on Codex API'] }
const UNKNOWN = { id: 'x', label: 'Anthropic', ok: null, error: 'ConnectError',
                  component: 'Claude API', componentStatus: '', incidents: [] }

check('service-three-states', [UP, DOWN, UNKNOWN].map(rowState).join(',') === 'up,down,unknown')
// An unreachable status page is not evidence of health — the whole point of
// having a third state rather than a boolean.
check('unreachable-is-not-up', rowState(UNKNOWN) === 'unknown')
check('only-confirmed-outage-counts',
  downServices([UP, DOWN, UNKNOWN]).length === 1 && downServices([UP, UNKNOWN]).length === 0)
check('banner-lists-incidents',
  collectText(ServicesBanner({ services: [UP, DOWN] })).includes('Elevated errors on Codex API'))
// An incident name must NOT reuse the note class beside the service dot: that
// one is a short aside next to a coloured dot, and it clipped a real 425px
// ChatGPT incident down to 190px — hiding exactly why the service was degraded.
const bannerClasses = node => {
  const found = []
  ;(function walk(n) {
    if (Array.isArray(n)) { n.forEach(walk); return }
    if (!n || typeof n !== 'object') return
    if (n.p?.className) found.push(n.p.className)
    walk(n.p?.children)
  })(node)
  return found
}
check('incident-uses-its-own-full-width-class',
  bannerClasses(ServicesBanner({ services: [UP, DOWN] })).includes('pl-incident'),
  JSON.stringify(bannerClasses(ServicesBanner({ services: [UP, DOWN] }))))
// The CSS behind both classes must let text wrap rather than cut it off.
const css = (await import('node:fs')).readFileSync(
  new URL('./plugin.js', import.meta.url), 'utf8')
const rule = name => (css.match(new RegExp(`\\.${name}\\{([^}]*)\\}`)) ?? [, ''])[1]
check('incident-css-wraps', /flex-basis:100%/.test(rule('pl-incident')) &&
  !/white-space:nowrap/.test(rule('pl-incident')), rule('pl-incident'))
check('service-note-css-wraps', !/white-space:nowrap/.test(rule('pl-service-note')) &&
  !/max-width/.test(rule('pl-service-note')), rule('pl-service-note'))
check('banner-empty-is-null',
  ServicesBanner({ services: [] }) === null && ServicesBanner({ services: undefined }) === null)

// --- time formatting ------------------------------------------------------

const times = [
  new Date(2026, 8, 21, 0, 5), new Date(2026, 8, 21, 9, 7),
  new Date(2026, 8, 21, 13, 42), new Date(2026, 8, 21, 23, 59)
].map(d => clock24(d.getTime()))
check('clock-24h', times.join('|') === '00:05|09:07|13:42|23:59', times.join('|'))
check('clock-no-meridiem', !times.some(t => /[AaPp]\.?[Mm]/.test(t)))

const today = new Date(); today.setHours(14, 30, 0, 0)
const later = new Date(); later.setDate(later.getDate() + 5); later.setHours(8, 5, 0, 0)
check('reset-today-is-time-only', resetAt24(today.getTime()) === clock24(today.getTime()))
check('reset-other-day-carries-date', resetAt24(later.getTime()).includes(' '))
// `true`, NaN and strings used to render as "in NaNd".
check('reset-rejects-nontimestamps',
  [true, NaN, Infinity, 'nope', null, 0, -1].every(v => relativeReset(v) === ''))
check('reset-sub-minute', relativeReset(Date.now() + 1000) === 'in <1m')
check('reset-hostile-resetAt24', [true, NaN, Infinity, 'nope'].every(v => resetAt24(v) === ''))

// --- malformed payloads ---------------------------------------------------

const hostile = [
  undefined, null, 0, 'nope', {}, { providers: {} },
  [null], [{}], [{ buckets: null }], [{ buckets: [{}] }],
  [{ buckets: [{ remainingPct: null }] }], [{ buckets: [{ remainingPct: NaN }] }],
  [{ buckets: 'nope', accounts: 7, poolWindows: 'x' }]
]
let threw = null
for (const p of hostile) {
  try { chipEntries(p, []); downServices(p); explain(p) }
  catch (e) { threw = `${JSON.stringify(p)} -> ${e.name}: ${e.message}` }
}
for (const s of [{}, { incidents: 'x' }, { ok: 'yes' }, { label: null }]) {
  try { ServiceRow({ service: s }) } catch (e) { threw = `ServiceRow ${e.name}` }
}
for (const p of [undefined, null, 'x', 7, {}, [null], ['x']]) {
  try { ServicesBanner({ services: p }) } catch (e) { threw = `ServicesBanner ${e.name}` }
}
check('malformed-payloads-never-throw', threw === null, threw ?? '')

// --- lifecycle ------------------------------------------------------------

// A hot reload calls register() again; the old <style> must go, not stack.
plugin.register({
  rest: async () => ({ providers: [] }),
  onDispose: fn => disposers.push(fn),
  register: contribution => registered.push(contribution)
})
check('register-is-idempotent-on-styles', appended.filter(s => !s.removed).length === 1,
  `${appended.filter(s => !s.removed).length} live style elements`)

// --- report ---------------------------------------------------------------

if (failures.length > 0) {
  console.log(`renderer: ${failures.length}/${checks} FAILED`)
  for (const line of failures) console.log(`  ✗ ${line}`)
  process.exit(1)
}
console.log(`renderer: ${checks} checks passed`)
""")

result = subprocess.run(["node", "probe.mjs"], cwd=root, capture_output=True, text=True)
print(result.stdout, end="")
if result.stderr:
    print(result.stderr, file=sys.stderr, end="")
shutil.rmtree(root, ignore_errors=True)
sys.exit(result.returncode)
