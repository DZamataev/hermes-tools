#!/usr/bin/env python3
"""Renderer checks: build a stub SDK, load plugin.js, exercise its pure helpers.

`plugin.js` imports `@hermes/plugin-sdk`, which only exists inside the desktop
app, so this stands up a throwaway node_modules with the handful of exports the
plugin uses. The stub `jsx()` returns `{t, p}` rather than real React elements.

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

root = pathlib.Path(tempfile.mkdtemp(prefix="comp-count-probe-"))
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
export const host = { state: { focusedUsage: null, focusedStoredSessionId: null,
                               focusedSessionProfile: 'default' } }
export const useValue = value => (globalThis.__probeValues ?? new Map()).get(value) ?? value
export const useQuery = () => globalThis.__probeQuery ?? { data: null, error: null, isFetching: false }
export const useQueryClient = () => ({ setQueryData() {} })
// The probe drives locale resolution through this global, so the checks can
// exercise the plugin's own bundle rather than the app's live translator.
export const usePluginI18n = () => globalThis.__probeTranslate ?? (key => key)
""")
(react / "package.json").write_text(json.dumps({
    "name": "react", "type": "module",
    "exports": {".": "./index.js", "./jsx-runtime": "./jsx-runtime/index.js"}}))
(react / "index.js").write_text(
    "export const useState = initial => [initial, () => {}]\n"
    "export const useEffect = () => {}\n"
    "export default { useState, useEffect }\n")
(react / "jsx-runtime" / "index.js").write_text(
    "export const jsx = (t, p) => ({ t, p })\nexport const jsxs = (t, p) => ({ t, p })\n")

shutil.copy(PLUGIN, root / "plugin.js")

(root / "probe.mjs").write_text(r"""
globalThis.document ??= { createElement: () => ({ remove() {} }), head: { append() {} } }

import plugin, {
  BLOCKS, formatCost, formatDuration, formatRelative, formatSpan, formatTokens, list, LOCALES,
  markerRow, routeLabel, segmentCompactions, sparkline, toolsLabel
} from './plugin.js'

const failures = []
let checks = 0
const check = (name, ok, detail = '') => {
  checks++
  if (!ok) failures.push(name + (detail ? ` — ${detail}` : ''))
}

// A translator over the bundle, resolving dot-paths and calling function leaves
// — the same resolution the app's plugin i18n performs, minus React.
const translator = locale => (key, ...args) => {
  const leaf = key.split('.').reduce((node, part) => (node ?? {})[part], LOCALES[locale])
  if (leaf === undefined) return `MISSING:${key}`
  return typeof leaf === 'function' ? leaf(...args) : leaf
}
const en = translator('en')

// --- sparkline --------------------------------------------------------------

const twenty = n => Array.from({ length: 20 }, () => n)

check('spark-width', sparkline(twenty(3), 3).length === 20, String(sparkline(twenty(3), 3).length))

// A bucket with real work must NEVER render blank. round(1/480*8) is 0, and a
// blank glyph would claim nothing happened in an hour that had 1 event.
const sparse = sparkline([480, ...Array.from({ length: 19 }, () => 1)], 480)
check('nonempty-bucket-floor', !sparse.slice(1).includes(BLOCKS[0]), JSON.stringify(sparse))
check('empty-bucket-blank', sparkline([5, 0, ...twenty(0).slice(2)], 5)[1] === BLOCKS[0])
check('peak-is-full-block', sparkline([9, 1], 9)[0] === '█', sparkline([9, 1], 9))

// A zero peak must not divide by zero or produce NaN glyphs.
check('zero-peak-safe', sparkline(twenty(0), 0) === BLOCKS[0].repeat(20),
      JSON.stringify(sparkline(twenty(0), 0)))

// --- markers ----------------------------------------------------------------

const seg = {
  start: 1000, end: 2000, compactions: [1000, 1500, 1999],
  buckets: twenty(1), peak: 1, routes: [], topTools: [], prompts: 1, toolCalls: 0
}
const marks = markerRow(seg)
check('marker-width', marks.length === 20, String(marks.length))
check('marker-at-start', marks[0] === '▲', JSON.stringify(marks))
check('marker-at-middle', marks[10] === '▲', JSON.stringify(marks))
check('marker-at-end', marks[19] === '▲', JSON.stringify(marks))
check('no-markers-no-row', markerRow({ ...seg, compactions: [] }).trim() === '')

// A compaction strictly INSIDE a bucket must land in the bucket it fell in.
// Boundary-only cases (start, midpoint, end) cannot tell floor from ceil: both
// agree there, so a rounding bug would ship unseen.
const midBucket = markerRow({ start: 0, end: 2000, compactions: [540] })
check('marker-rounds-down', midBucket.indexOf('▲') === 5, JSON.stringify(midBucket))
check('marker-single-column', midBucket.split('▲').length - 1 === 1, JSON.stringify(midBucket))

// --- duration ---------------------------------------------------------------

check('minutes', formatDuration(45 * 60, en) === '45m', formatDuration(45 * 60, en))
check('hours', formatDuration(72 * 60, en) === '1h12m', formatDuration(72 * 60, en))
check('days', formatDuration(51 * 3600, en) === '2d3h', formatDuration(51 * 3600, en))
// Under a minute is "<1m", not the "0m" a floor produces.
check('sub-minute', formatDuration(30, en) === '<1m', formatDuration(30, en))

// --- span -------------------------------------------------------------------

const sameDay = formatSpan(new Date(2026, 8, 3, 16, 35).getTime() / 1000,
                           new Date(2026, 8, 3, 17, 47).getTime() / 1000)
check('same-day-span', sameDay === '03.09 16:35–17:47', sameDay)
const crossDay = formatSpan(new Date(2026, 8, 3, 23, 10).getTime() / 1000,
                            new Date(2026, 8, 4, 1, 5).getTime() / 1000)
check('cross-day-span', crossDay === '03.09 23:10–04.09 01:05', crossDay)

// --- labels -----------------------------------------------------------------

check('route-single', routeLabel({ routes: [{ model: 'm1', provider: 'p1' }] }, en) === 'm1 · p1')
check('route-multi',
      routeLabel({ routes: [{ model: 'm1', provider: 'p1' }, { model: 'm2', provider: 'p2' }] }, en)
      === 'm1 · p1 → m2 · p2')
check('route-empty', routeLabel({ routes: [] }, en) === 'unknown route',
      routeLabel({ routes: [] }, en))
// A route with no provider must not render a dangling separator.
check('route-no-provider', routeLabel({ routes: [{ model: 'm1', provider: '' }] }, en) === 'm1')

// Tool names are identifiers, not prose: identical in every locale.
check('tools', toolsLabel({ topTools: [{ name: 'patch', count: 9 }, { name: 'terminal', count: 4 }] })
      === 'patch×9, terminal×4')
check('tools-empty', toolsLabel({ topTools: [] }) === '')

// --- locale bundle ----------------------------------------------------------

// Every key the panel renders must exist in the bundle. A typo'd or renamed key
// falls through to the raw path ("counts.prompts") and ships as visible UI.
const paths = (node, prefix = '') => Object.entries(node).flatMap(([key, value]) => {
  const path = prefix ? `${prefix}.${key}` : key
  return value && typeof value === 'object' ? paths(value, path) : [path]
})
check('en-bundle-present', paths(LOCALES.en).length > 0)
check('no-missing-keys', ![
  routeLabel({ routes: [] }, en), formatDuration(600, en), formatDuration(30, en),
  formatDuration(51 * 3600, en), en('title'), en('noSession'), en('noSegments'),
  en('segments', 2), en('chipTitle', 3), en('backendDown', 'x'),
  en('counts.prompts', 1), en('counts.tools', 1)
].some(text => text.startsWith('MISSING:')))

// --- defensive guards -------------------------------------------------------

// The payload crosses a process boundary. One malformed field must degrade a
// row, never throw during render and take the whole status bar down with it.
check('list-guards-null', list(null).length === 0)
check('list-guards-scalar', list(42).length === 0)
check('list-drops-non-objects', list([{ a: 1 }, null, 'x', 7]).length === 1)
check('spark-guards-garbage', sparkline(null, 5).length === 20)
check('markers-guard-garbage', markerRow({}).length === 20)
check('route-guards-garbage', routeLabel({}, en) === 'unknown route')
check('tools-guards-garbage', toolsLabel({}) === '')
check('duration-guards-garbage', typeof formatDuration(NaN, en) === 'string')

// --- chip -------------------------------------------------------------------

const renderChip = () => {
  let contribution
  plugin.register({
    register(value) { contribution = value },
    rest: async () => ({ segments: [] }),
    // register() registers locale bundles and expects a disposer back.
    i18n: { register: () => () => {}, t: key => key },
    onDispose() {}
  })
  return contribution
}

check('plugin-id', plugin.id === 'comp-count', plugin.id)
const contribution = renderChip()
check('chip-area', contribution.area === 'statusBar.right', contribution.area)
check('chip-renders', typeof contribution.render === 'function')

// The chip must never regress: whatever the backend does, it keeps printing the
// live compaction count with the clamp it always had.
const { compactionLabel } = plugin
check('chip-count', compactionLabel({ compressions: 3 }) === '🧳 3', compactionLabel({ compressions: 3 }))
check('chip-clamps-float', compactionLabel({ compressions: 2.9 }) === '🧳 2')
check('chip-clamps-negative', compactionLabel({ compressions: -4 }) === '🧳 0')
check('chip-clamps-garbage', compactionLabel({ compressions: 'nonsense' }) === '🧳 0')
check('chip-clamps-null', compactionLabel(null) === '🧳 0')
check('chip-clamps-missing', compactionLabel({}) === '🧳 0')

// --- relative time ----------------------------------------------------------

// Anchored to the segment's end: "yesterday" answers "when did I last touch
// this", which is what the exact span above it does not say at a glance.
// Segment timestamps are seconds, like every other field the backend sends.
// Fixtures are built in LOCAL time because "yesterday" is a local-calendar
// claim — under UTC a 22:00 timestamp is already today in Moscow.
const local = (y, m, d, h, min = 0) => new Date(y, m, d, h, min, 0).getTime()
const NOW = local(2026, 8, 23, 15)
const rel = (ms, now = NOW) => formatRelative(ms / 1000, en, now)

check('relative-minutes', rel(NOW - 7 * 60 * 1000) === '7 minutes ago', rel(NOW - 7 * 60 * 1000))
check('relative-one-minute', rel(NOW - 60 * 1000) === '1 minute ago', rel(NOW - 60 * 1000))
check('relative-hours', rel(NOW - 7 * 3600 * 1000) === '7 hours ago', rel(NOW - 7 * 3600 * 1000))
check('relative-one-hour', rel(NOW - 3600 * 1000) === '1 hour ago', rel(NOW - 3600 * 1000))

// Under a minute must not render "0 minutes ago", which reads as a stale zero
// rather than as "just now".
check('relative-seconds', rel(NOW - 20 * 1000) === 'just now', rel(NOW - 20 * 1000))

// Calendar-relative, not 24-hour arithmetic: work at 22:00 last night is
// "yesterday" at 15:00 today even though that is only 17 hours.
check('relative-yesterday', rel(local(2026, 8, 22, 22)) === 'yesterday',
      rel(local(2026, 8, 22, 22)))

// ...and 23:30 today is "today" at 00:30 tomorrow only by calendar, so the
// same-day case must not leak into it.
check('relative-today', rel(local(2026, 8, 23, 2)) === '13 hours ago',
      rel(local(2026, 8, 23, 2)))

check('relative-days', rel(local(2026, 8, 19, 12)) === '4 days ago',
      rel(local(2026, 8, 19, 12)))

// A future timestamp comes from a clock skew, not from the future; it must not
// render "-3 minutes ago".
check('relative-future-is-just-now', rel(NOW + 5 * 60 * 1000) === 'just now',
      rel(NOW + 5 * 60 * 1000))
check('relative-guards-garbage', formatRelative(null, en, NOW) === '')

// --- compaction wording -----------------------------------------------------

// "3" alone next to a suitcase forces the reader to guess the unit.
check('segment-compactions-plural', segmentCompactions({ compactions: [1, 2, 3] }, en) === '🧳 3 compactions',
      segmentCompactions({ compactions: [1, 2, 3] }, en))
check('segment-compactions-singular', segmentCompactions({ compactions: [1] }, en) === '🧳 1 compaction',
      segmentCompactions({ compactions: [1] }, en))
check('segment-compactions-none', segmentCompactions({ compactions: [] }, en) === '')
check('segment-compactions-garbage', segmentCompactions({}, en) === '')

// --- token and cost formatting ----------------------------------------------

check('tokens-thousands', formatTokens(75_586) === '75.6k', formatTokens(75_586))
check('tokens-millions', formatTokens(75_586_893) === '75.6M', formatTokens(75_586_893))
check('tokens-small-exact', formatTokens(892) === '892', formatTokens(892))
check('tokens-zero', formatTokens(0) === '0')
check('tokens-guards-garbage', formatTokens(null) === '0')

check('cost-two-decimals', formatCost(79.2537) === '$79.25', formatCost(79.2537))

// A real cost below a cent must not print "$0.00" and read as free.
check('cost-sub-cent', formatCost(0.004) === '<$0.01', formatCost(0.004))

// An exact zero IS free, and says so.
check('cost-zero-is-zero', formatCost(0) === '$0.00', formatCost(0))

// No rate is not a number at all; the caller decides the wording.
check('cost-null-is-empty', formatCost(null) === '')

console.log(`  ${checks - failures.length}/${checks} checks passed`)
for (const failure of failures) console.log(`  ✗ ${failure}`)
process.exit(failures.length ? 1 : 0)
""")

result = subprocess.run(["node", "probe.mjs"], cwd=root, capture_output=True, text=True)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
shutil.rmtree(root, ignore_errors=True)
sys.exit(result.returncode)