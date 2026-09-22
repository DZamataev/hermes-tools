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
  BLOCKS, formatDuration, formatSpan, list, LOCALES, markerRow, num, routeLabel,
  sparkline, toolsLabel
}
