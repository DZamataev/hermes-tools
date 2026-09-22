// comp-count — the focused session's working timeline in the status bar.
//
// The chip keeps showing the live compaction count. Tapping it opens a popover
// listing the session as working segments: where the work happened inside each
// stretch (a density sparkline), when it was compacted (markers positioned in
// the same 20 columns), which model and provider ran, and what the stretch was
// made of.
//
// Data comes from this package's own Python half
// (`~/.hermes/plugins/comp-count/dashboard/plugin_api.py`) through `ctx.rest`.
// That half is imported only when `comp-count` is in `plugins.enabled` in
// config.yaml — until then `ctx.rest` errors and the panel says so while the
// chip keeps working off live state.
import {
  Button, host, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS,
  useQuery, usePluginI18n, useValue
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'comp-count'
// Sparkline glyphs. Index 0 is the EMPTY bucket; a bucket with any activity
// never uses it (see `sparkline`).
const BLOCKS = ' ▁▂▃▄▅▆▇█'
const SPARK_BUCKETS = 20

// Locale bundle, registered under this plugin's id at load. The panel follows
// the app's `display.language`; it does not pick a language of its own. English
// only for now — `en` is the resolver's fallback, so this renders correctly
// under any app language, and adding a locale is one more key here with no
// change at any call site.
const LOCALES = {
  en: {
    title: 'Session timeline',
    segments: n => `${n} segments`,
    noSession: 'No session yet.',
    noSegments: 'No working segments in this session yet.',
    backendDown: error =>
      `Backend unavailable — add "comp-count" to plugins.enabled and restart the gateway. (${error})`,
    chipTitle: n => `Compactions in this session: ${n}`,
    route: { unknown: 'unknown route' },
    counts: {
      prompts: n => `${n} prompts`,
      tools: n => `${n} tool calls`
    },
    duration: {
      lessThanMinute: '<1m',
      minutes: m => `${m}m`,
      hoursMinutes: (h, m) => `${h}h${m}m`,
      daysHours: (d, h) => `${d}d${h}h`
    }
  }
}

/** Defensive array read: the payload crosses a process boundary, and one
 *  malformed field must degrade a row, never throw during render and take the
 *  whole status bar with it. */
function list(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : []
}

/** Finite number, or null. null/undefined/NaN all reach here from a partial payload. */
function num(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** One glyph row, scaled to this segment's own peak.
 *
 *  The floor matters: `round(1 / 480 * 8)` is 0, so a quiet bucket beside a tall
 *  peak would render blank and claim nothing happened in an hour that had work
 *  in it. Any non-zero bucket gets at least `▁`. */
function sparkline(buckets, peak) {
  const values = Array.isArray(buckets) ? buckets : []
  const top = Math.max(1, num(peak) ?? 1)
  let out = ''
  for (let i = 0; i < SPARK_BUCKETS; i += 1) {
    const count = num(values[i]) ?? 0
    out += count <= 0 ? BLOCKS[0] : BLOCKS[Math.max(1, Math.min(8, Math.round((count / top) * 8)))]
  }
  return out
}

/** Compaction markers in the SAME 20 columns as the sparkline, so a `▲` sits
 *  under the activity it interrupted. Position is the whole point: a list of
 *  times cannot show that two compactions hit the densest stretch. */
function markerRow(segment) {
  const start = num(segment?.start) ?? 0
  const end = num(segment?.end) ?? 0
  const span = Math.max(end - start, 1)
  const row = new Array(SPARK_BUCKETS).fill(' ')
  for (const at of Array.isArray(segment?.compactions) ? segment.compactions : []) {
    const ts = num(at)
    if (ts === null) {
      continue
    }

    const index = Math.floor(((ts - start) / span) * SPARK_BUCKETS)
    row[Math.min(SPARK_BUCKETS - 1, Math.max(0, index))] = '▲'
  }
  return row.join('')
}

/** How long the stretch ran, in the active locale's units. Under a minute is
 *  the `lessThanMinute` string, not the `0m` a floor produces — a two-event
 *  segment is short, not instantaneous. */
function formatDuration(seconds, t) {
  const total = num(seconds)
  if (total === null || total < 0) {
    return ''
  }

  const minutes = Math.floor(total / 60)
  if (minutes < 1) return t('duration.lessThanMinute')
  if (minutes < 60) return t('duration.minutes', minutes)
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('duration.hoursMinutes', hours, String(minutes % 60).padStart(2, '0'))
  return t('duration.daysHours', Math.floor(hours / 24), hours % 24)
}

function pad(value) {
  return String(value).padStart(2, '0')
}

function clock(date) {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function dayMonth(date) {
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`
}

/** `03.09 16:35–17:47`, carrying the end date only when the stretch crosses
 *  midnight — a segment that ends "01:05" without a date reads as the same day. */
function formatSpan(startSeconds, endSeconds) {
  const start = num(startSeconds)
  const end = num(endSeconds)
  if (start === null || end === null) {
    return ''
  }

  const from = new Date(start * 1000)
  const to = new Date(end * 1000)
  const tail = from.toDateString() === to.toDateString() ? clock(to) : `${dayMonth(to)} ${clock(to)}`
  return `${dayMonth(from)} ${clock(from)}–${tail}`
}

/** `m1 · p1 → m2 · p2`. Several routes is the honest reading of an aggregate
 *  that cannot order them; see the backend's `_segment`. */
function routeLabel(segment, t) {
  const names = list(segment?.routes)
    .map(route => [route.model, route.provider].filter(Boolean).join(' · '))
    .filter(Boolean)
  return names.length ? names.join(' → ') : t('route.unknown')
}

/** Tool names are identifiers, not prose — never translated. */
function toolsLabel(segment) {
  return list(segment?.topTools)
    .map(tool => (tool.name && num(tool.count) !== null ? `${tool.name}×${tool.count}` : ''))
    .filter(Boolean)
    .join(', ')
}

// Exported for tests; the app only consumes the default export.
export {
  BLOCKS, compactionLabel, formatDuration, formatSpan, list, LOCALES, markerRow,
  num, routeLabel, sparkline, toolsLabel
}

// The namespaced REST door, captured in `register`. Components render inside
// the app's own React tree, outside register's closure, so it is module state
// rather than a prop threaded through every node.
let rest = async () => { throw new Error('plugin not registered') }
// The live <style> element, so a second register() replaces it instead of
// stacking another copy in <head>.
let styleElement = null
// Segments only change when a turn ends; this is not a live counter.
const REFETCH_MS = 60_000

// Disk plugins are not scanned by Tailwind, so layout lives here. Colors are
// theme variables only — never literals, or a theme switch breaks them.
const CSS = `
.cc-chip{display:inline-flex;align-items:center;gap:4px;height:100%;padding:0 6px;font-size:.6875rem;font-variant-numeric:tabular-nums;color:var(--ui-text-tertiary)}
.cc-popover{width:360px;max-width:calc(100vw - 24px)}
.cc-panel{display:flex;flex-direction:column;gap:10px;max-height:min(60dvh,460px);overflow-y:auto;overscroll-behavior:contain}
.cc-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.cc-title{font-size:.75rem;font-weight:600;color:var(--ui-text-primary)}
.cc-sub{font-size:.65rem;color:var(--ui-text-quaternary)}
.cc-error{font-size:.65rem;color:var(--dt-destructive,var(--ui-text-tertiary))}
.cc-seg{display:flex;flex-direction:column;gap:1px;padding-bottom:8px;border-bottom:1px solid var(--ui-stroke-quaternary)}
.cc-seg-top{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.cc-span{font-size:.65rem;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.cc-dur{font-size:.62rem;color:var(--ui-text-quaternary);font-variant-numeric:tabular-nums}
/* The sparkline and its markers must line up column for column, so both rows
   are monospace with identical tracking. A proportional font shifts the ▲ off
   the block it belongs to. */
.cc-spark,.cc-marks{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.7rem;line-height:1.05;letter-spacing:0;white-space:pre}
.cc-spark{color:var(--ui-accent)}
.cc-marks{color:var(--dt-destructive,var(--ui-accent))}
.cc-route{font-size:.65rem;font-weight:600;color:var(--ui-text-secondary);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.cc-counts{font-size:.62rem;color:var(--ui-text-tertiary)}
.cc-tools{font-size:.62rem;color:var(--ui-text-quaternary);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
`

/** The chip's text: the live compaction count, clamped. The host can report a
 *  count before session state settles, and "🧳 NaN" or a negative count in the
 *  status bar is worse than showing zero. */
function compactionLabel(usage) {
  return `🧳 ${Math.max(0, Math.trunc(Number(usage?.compressions) || 0))}`
}

function Segment({ segment, t }) {
  const marks = markerRow(segment)
  const counts = [
    t('counts.prompts', num(segment.prompts) ?? 0),
    t('counts.tools', num(segment.toolCalls) ?? 0),
    (Array.isArray(segment.compactions) ? segment.compactions.length : 0) > 0
      ? `🧳${segment.compactions.length}`
      : ''
  ].filter(Boolean).join(' · ')
  const tools = toolsLabel(segment)

  return jsxs('div', {
    className: 'cc-seg',
    children: [
      jsxs('div', {
        className: 'cc-seg-top',
        children: [
          jsx('span', { className: 'cc-span', children: formatSpan(segment.start, segment.end) }),
          jsx('span', {
            className: 'cc-dur',
            children: formatDuration((num(segment.end) ?? 0) - (num(segment.start) ?? 0), t)
          })
        ]
      }),
      jsx('div', { className: 'cc-spark', children: sparkline(segment.buckets, segment.peak) }),
      // The marker row is dropped entirely when nothing was compacted, rather
      // than rendering 20 blanks that push every segment a line taller.
      marks.trim() && jsx('div', { className: 'cc-marks', children: marks }),
      jsx('div', {
        className: 'cc-route', title: routeLabel(segment, t), children: routeLabel(segment, t)
      }),
      jsx('div', { className: 'cc-counts', children: counts }),
      tools && jsx('div', { className: 'cc-tools', title: tools, children: tools })
    ]
  })
}

function Panel({ sessionId, profile }) {
  const t = usePluginI18n(ID)
  const { data, error } = useQuery({
    queryKey: [ID, 'timeline', sessionId, profile],
    queryFn: () => rest(`/timeline?session=${encodeURIComponent(sessionId)}`
      + `&profile=${encodeURIComponent(profile)}`),
    enabled: Boolean(sessionId),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS,
    retry: false
  })
  const segments = list(data?.segments)

  return jsxs('div', {
    className: 'cc-panel',
    children: [
      jsxs('div', {
        className: 'cc-head',
        children: [
          jsx('span', { className: 'cc-title', children: t('title') }),
          jsx('span', {
            className: 'cc-sub',
            children: segments.length ? t('segments', segments.length) : ''
          })
        ]
      }),
      !sessionId && jsx('div', { className: 'cc-sub', children: t('noSession') }),
      error && jsx('div', {
        className: 'cc-error',
        children: t('backendDown', String(error.message ?? error))
      }),
      sessionId && !error && segments.length === 0 && jsx('div', {
        className: 'cc-sub',
        children: t('noSegments')
      }),
      ...segments.map((segment, index) => jsx(Segment, { segment, t }, `${segment.start}-${index}`))
    ]
  })
}

function Chip() {
  const t = usePluginI18n(ID)
  const usage = useValue(host.state.focusedUsage)
  // The STORED id, never focusedSessionId: runtime ids do not survive a reload
  // and do not key state.db, which is what the backend reads.
  const sessionId = useValue(host.state.focusedStoredSessionId) || ''
  const profile = useValue(host.state.focusedSessionProfile) || 'default'
  const label = compactionLabel(usage)
  const title = t('chipTitle', label.replace('🧳 ', ''))

  return jsxs(Popover, {
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx(Button, {
          variant: 'ghost',
          size: 'micro',
          'aria-label': title,
          title,
          children: jsx('span', { className: 'cc-chip', children: label })
        })
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'end',
        className: 'cc-popover',
        'aria-label': t('title'),
        children: jsx(Panel, { sessionId, profile })
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Comp Count',
  description: 'Session timeline — models, providers, working segments and compactions.',
  compactionLabel,
  register(ctx) {
    rest = path => ctx.rest(path)

    // Locale bundles land under this plugin's id, scoped like ctx.storage —
    // core's en.ts is never touched. The disposer drops them on unload, so a
    // hot reload cannot stack duplicate registrations.
    ctx.onDispose(ctx.i18n.register(LOCALES))

    // Idempotent: a hot reload can call register again on a module instance
    // whose previous style element is still in the document.
    styleElement?.remove()
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    styleElement = style
    ctx.onDispose(() => {
      style.remove()
      if (styleElement === style) {
        styleElement = null
      }
    })

    ctx.register({
      id: 'status',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(Chip, {})
    })
  }
}
