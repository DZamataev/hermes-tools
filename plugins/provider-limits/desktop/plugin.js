// Provider limits — remaining quota for custom providers in the status bar.
//
// A compact chip shows the TIGHTEST remaining bucket across every configured
// provider (that is the number that actually stops work); tapping it opens a
// popover with every provider, every window, and the per-account breakdown.
//
// Data comes from this package's own Python half
// (`~/.hermes/plugins/provider-limits/dashboard/plugin_api.py`) through
// `ctx.rest`, so API keys never reach the renderer. That half is imported only
// when `provider-limits` is in `plugins.enabled` in config.yaml — until then
// `ctx.rest` errors and the chip renders a muted dash rather than crashing.
import {
  Button, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS,
  useQuery, useQueryClient
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'provider-limits'
const QUERY_KEY = [ID, 'limits']
// The namespaced REST door, captured in `register`. Components render inside the
// app's own React tree, outside `register`'s closure, so it is module state
// rather than a prop threaded through every node.
let rest = async () => { throw new Error('plugin not registered') }
// The live <style> element, so a second register() replaces it instead of
// stacking another copy in <head>.
let styleElement = null
// The backend caches for 60s; ask a little more often so a fresh cache entry is
// picked up promptly without ever doubling the upstream call rate.
const REFETCH_MS = 45_000

// Disk plugins are not scanned by Tailwind, so layout lives here. Colors are
// theme variables only — never literals, or a theme switch breaks them.
const CSS = `
.pl-chip{display:inline-flex;align-items:center;gap:4px;height:100%;padding:0 6px;font-size:.6875rem;font-variant-numeric:tabular-nums;color:var(--ui-text-tertiary)}
.pl-chip-item{display:inline-flex;align-items:baseline;min-width:0}
.pl-chip-item[data-tone=warn]{color:var(--ui-accent)}
.pl-chip-item[data-tone=low]{color:var(--dt-destructive,var(--ui-accent))}
.pl-chip-item[data-tone=down]{color:var(--dt-destructive,var(--ui-accent))}
.pl-chip-sep{color:var(--ui-stroke-secondary)}
.pl-popover{width:328px;max-width:calc(100vw - 24px)}
.pl-panel{display:flex;flex-direction:column;gap:10px;max-height:min(60dvh,420px);overflow-y:auto;overscroll-behavior:contain}
.pl-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.pl-title{font-size:.75rem;font-weight:600;color:var(--ui-text-primary)}
.pl-sub{font-size:.65rem;color:var(--ui-text-quaternary)}
.pl-legend{font-size:.62rem;line-height:1.3;color:var(--ui-text-quaternary);margin-top:-6px}
.pl-services{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:.62rem;color:var(--ui-text-tertiary);padding-bottom:6px;border-bottom:1px solid var(--ui-stroke-quaternary)}
.pl-service{display:inline-flex;align-items:center;gap:4px;min-width:0}
.pl-service-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;background:var(--ui-success,var(--ui-accent))}
.pl-service[data-state=down] .pl-service-dot{background:var(--dt-destructive,var(--ui-accent))}
.pl-service[data-state=unknown] .pl-service-dot{background:var(--ui-text-quaternary)}
.pl-service[data-state=down]{color:var(--dt-destructive,var(--ui-text-secondary))}
.pl-service-note{color:var(--ui-text-quaternary);white-space:normal;overflow-wrap:anywhere;line-height:1.35}
.pl-incident{flex-basis:100%;color:var(--ui-text-quaternary);line-height:1.35;overflow-wrap:anywhere}
.pl-explain{font-size:.62rem;line-height:1.4;color:var(--ui-text-quaternary);padding-top:6px;border-top:1px solid var(--ui-stroke-quaternary)}
.pl-provider{display:flex;flex-direction:column;gap:4px}
.pl-provider-name{display:flex;align-items:baseline;justify-content:space-between;gap:6px;font-size:.6875rem;font-weight:600;color:var(--ui-text-secondary)}
.pl-provider-error{font-size:.65rem;color:var(--dt-destructive,var(--ui-text-tertiary))}
.pl-row{display:grid;grid-template-columns:72px 1fr 40px;align-items:center;gap:6px;font-size:.65rem;color:var(--ui-text-tertiary)}
.pl-row-label{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.pl-bar{position:relative;height:4px;border-radius:2px;background:var(--ui-bg-quinary,var(--ui-stroke-quaternary));overflow:hidden}
.pl-bar>span{position:absolute;inset:0 auto 0 0;border-radius:2px;background:var(--ui-accent)}
.pl-bar[data-tone=low]>span{background:var(--dt-destructive,var(--ui-accent))}
.pl-value{text-align:right;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary)}
.pl-reset{font-size:.6rem;color:var(--ui-text-quaternary);padding-left:2px}
.pl-pool{font-size:.62rem;color:var(--ui-text-quaternary);padding-left:2px}
.pl-accounts{display:flex;flex-direction:column;gap:2px;margin-left:2px;padding-left:8px;border-left:1px solid var(--ui-stroke-quaternary)}
.pl-account{display:flex;align-items:baseline;justify-content:space-between;gap:6px;font-size:.62rem;color:var(--ui-text-quaternary)}
.pl-account-name{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.pl-account[data-disabled=true]{opacity:.5;text-decoration:line-through}
.pl-account-values{flex-shrink:0;font-variant-numeric:tabular-nums}
.pl-foot{display:flex;align-items:center;justify-content:space-between;gap:8px}
`

// Below this the number is worth reacting to; below LOW it is nearly gone.
const WARN_PCT = 25
const LOW_PCT = 10

function tone(pct) {
  if (pct <= LOW_PCT) return 'low'
  if (pct <= WARN_PCT) return 'warn'
  return 'ok'
}

/** Wall-clock in 24h. `hourCycle: 'h23'` rather than `hour12: false` — the
 *  latter renders midnight as 24:00 under some locales. */
function clock24(ms) {
  return new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', hourCycle: 'h23', minute: '2-digit' })
}

/** When a window resets, as wall-clock. Anything past today gets a date too,
 *  since "resets 11:02" on a weekly bucket would read as "in an hour"; a reset
 *  in another year carries the year, or "26.09" is ambiguous. */
function resetAt24(ms) {
  if (!Number.isFinite(ms)) {
    return ''
  }

  const when = new Date(ms)
  if (Number.isNaN(when.getTime())) {
    return ''
  }

  const now = new Date()
  const time = clock24(ms)
  if (when.toDateString() === now.toDateString()) {
    return time
  }

  const date = when.toLocaleDateString(undefined, when.getFullYear() === now.getFullYear()
    ? { day: '2-digit', month: '2-digit' }
    : { day: '2-digit', month: '2-digit', year: 'numeric' })
  return `${date} ${time}`
}

function relativeReset(resetAt) {
  // Only a real timestamp produces a relative string; `true`, NaN and strings
  // would otherwise render as "in NaNd".
  if (!Number.isFinite(resetAt) || resetAt <= 0) {
    return ''
  }

  const ms = resetAt - Date.now()
  if (ms <= 0) return 'resetting'
  const minutes = Math.round(ms / 60_000)
  // Anything under a minute is "<1m", not the "in 0m" a round() produces.
  if (minutes < 1) return 'in <1m'
  if (minutes < 60) return `in ${minutes}m`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `in ${hours}h`
  return `in ${Math.round(hours / 24)}d`
}

/** Defensive array read: the payload crosses a process boundary, and one
 *  malformed field must degrade a row, never throw during render and take the
 *  whole status bar with it. */
function list(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : []
}

/** A percentage we are willing to draw, or null when the value is not a real
 *  number (null/undefined/NaN all reach here from a partial payload). */
function pct(value) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null
}

function useLimits() {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => rest('/limits'),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS,
    retry: false
  })
}

function Bar({ pct: value }) {
  return jsx('div', {
    className: 'pl-bar',
    // Tone off the ROUNDED value, the same integer the row prints: a bar
    // labelled "10%" must not be coloured as the 10.04% it came from.
    'data-tone': tone(Math.round(value)),
    children: jsx('span', { style: { width: `${Math.max(2, value)}%` } })
  })
}

function BucketRow({ bucket }) {
  const value = pct(bucket.remainingPct)
  if (value === null) {
    return null
  }

  const reset = relativeReset(bucket.resetAt)
  const label = String(bucket.label ?? 'limit')
  return jsxs('div', {
    children: [
      jsxs('div', {
        className: 'pl-row',
        children: [
          jsx('span', { className: 'pl-row-label', title: bucket.detail || label, children: label }),
          jsx(Bar, { pct: value }),
          // Round the BAR's tone off the same displayed integer, so a row
          // reading "10%" is never coloured as if it were 10.04%.
          jsx('span', { className: 'pl-value', children: `${Math.round(value)}%` })
        ]
      }),
      reset && jsx('div', {
        className: 'pl-reset',
        children: bucket.resetAt ? `resets ${reset} · ${resetAt24(bucket.resetAt)}` : `resets ${reset}`
      })
    ]
  })
}

function AccountRow({ account }) {
  const values = list(account.buckets)
    .map(b => (pct(b.remainingPct) === null ? null : `${b.label} ${Math.round(pct(b.remainingPct))}%`))
    .filter(Boolean)
    .join(' · ')
  return jsxs('div', {
    className: 'pl-account',
    'data-disabled': String(Boolean(account.disabled)),
    children: [
      jsx('span', { className: 'pl-account-name', title: account.tier || account.name, children: account.name }),
      jsx('span', { className: 'pl-account-values', children: values || '—' })
    ]
  })
}

function ProviderBlock({ provider }) {
  const poolWindows = list(provider.poolWindows).filter(w => pct(w.remainingPct) !== null)
  const accounts = list(provider.accounts)
  return jsxs('div', {
    className: 'pl-provider',
    children: [
      jsxs('div', {
        className: 'pl-provider-name',
        children: [
          jsx('span', { children: String(provider.label ?? provider.id ?? 'provider') }),
          jsx('span', { className: 'pl-sub', children: String(provider.kind ?? '') })
        ]
      }),
      !provider.ok && jsx('div', { className: 'pl-provider-error', children: String(provider.error ?? 'unavailable') }),
      ...list(provider.buckets).map((bucket, index) => jsx(BucketRow, { bucket }, bucket.key ?? index)),
      poolWindows.length > 0 && jsx('div', {
        className: 'pl-pool',
        children: `account pool · ${poolWindows.map(w => `${w.label} ${Math.round(pct(w.remainingPct))}%`).join(' · ')}`
      }),
      accounts.length > 0 && jsx('div', {
        className: 'pl-accounts',
        children: accounts.map((account, index) => jsx(AccountRow, { account }, `${account.name}-${index}`))
      })
    ]
  })
}

/** Upstream service health, at the very top: quota answers "am I out of
 *  budget", this answers "is the service even up" — and when it is down, no
 *  amount of remaining quota helps. Three states, because "we could not reach
 *  the status page" is NOT the same as "the service is fine". */
function ServiceRow({ service }) {
  const state = service.ok === true ? 'up' : service.ok === false ? 'down' : 'unknown'
  const note = state === 'down'
    ? String(service.description || 'incident')
    : state === 'unknown'
      ? `status unavailable${service.error ? ` (${service.error})` : ''}`
      : ''

  // The component we actually depend on, named in the tooltip: a red dot must
  // be traceable to "Claude API", not to the vendor as a whole.
  const title = [
    `${service.label}: ${service.description || state}`,
    service.component ? `${service.component} — ${service.componentStatus || 'unknown'}` : ''
  ].filter(Boolean).join('\n')

  return jsxs('span', {
    className: 'pl-service',
    'data-state': state,
    title,
    children: [
      jsx('span', { className: 'pl-service-dot' }),
      jsx('span', { children: String(service.label ?? service.id ?? 'service') }),
      note && jsx('span', { className: 'pl-service-note', children: `· ${note}` })
    ]
  })
}

function ServicesBanner({ services }) {
  const rows = list(services)
  if (rows.length === 0) {
    return null
  }

  // An incident name is the single most useful string on the whole panel when
  // something is broken, so it gets its own full-width line — NOT the clipped
  // one-line treatment used for the short note beside a service dot, which cut
  // a 425px incident name down to 190px and hid why the service was degraded.
  const incidents = rows.flatMap(s => (Array.isArray(s.incidents) ? s.incidents : [])
    .map(name => `${s.label}: ${name}`))

  return jsxs('div', {
    className: 'pl-services',
    children: [
      ...rows.map((service, index) => jsx(ServiceRow, { service }, service.id ?? index)),
      ...incidents.map((text, index) => jsx('span', { className: 'pl-incident', children: text }, `inc-${index}`))
    ]
  })
}

/** Explains the status-bar readout. With the chip reduced to bare numbers, this
 *  is the ONLY place that says which number belongs to which provider, so it
 *  lists them in the same left-to-right order the chip prints. */
function ChipExplainer({ providers, services }) {
  const entries = chipEntries(providers, services)
  const down = downServices(services)

  const lines = [entries.length === 0
    ? 'The status bar shows ⛽ — no provider is configured.'
    : `Status bar, left to right — each provider's 5-HOUR window: `
      + `${entries.map(e => `${e.label} ${e.value === null ? '—' : `${e.value}%`}`).join(', ')}.`]

  if (entries.some(e => e.value !== null)) {
    // The chip deliberately shows one window only, so say what it does NOT
    // cover — a weekly limit can be nearly gone while every 5h figure is high.
    lines.push('Weekly and other windows are not in those numbers — see the rows above.')
  }

  if (entries.some(e => e.value === null)) {
    lines.push('“—” means that provider reported no 5-hour limit.')
  }

  if (down.length > 0) {
    lines.push(`⚠ — ${down.map(s => s.label).join(', ')} is reporting an incident, so remaining quota may not be usable.`)
  }

  return jsx('div', { className: 'pl-explain', children: lines.join(' ') })
}

function Panel() {
  const { data, error, isFetching } = useLimits()
  const client = useQueryClient()
  const [refreshError, setRefreshError] = useState(null)
  const providers = list(data?.providers)

  return jsxs('div', {
    className: 'pl-panel',
    children: [
      // First thing in the panel: is the upstream even up. A quota reading is
      // meaningless while the service behind it is down.
      jsx(ServicesBanner, { services: data?.services }),
      jsxs('div', {
        className: 'pl-head',
        children: [
          jsx('span', { className: 'pl-title', children: 'Provider limits' }),
          jsx('span', {
            className: 'pl-sub',
            children: data?.fetchedAt ? clock24(data.fetchedAt) : ''
          })
        ]
      }),
      // Percentages are REMAINING, not used — the one thing a first-time reader
      // gets backwards, and getting it backwards inverts the whole panel.
      // English like every other string here; one Russian line would read as a bug.
      jsx('div', { className: 'pl-legend', children: '% = left · 100% untouched, 0% exhausted' }),
      error && jsx('div', {
        className: 'pl-provider-error',
        children: `Backend unavailable — add "provider-limits" to plugins.enabled and restart the gateway. (${String(error.message ?? error)})`
      }),
      refreshError && jsx('div', {
        className: 'pl-provider-error',
        children: `Refresh failed: ${refreshError}`
      }),
      !error && providers.length === 0 && jsx('div', {
        className: 'pl-sub',
        children: 'No custom providers with a known quota API are configured.'
      }),
      ...providers.map((provider, index) => jsx(ProviderBlock, { provider }, provider.id ?? index)),
      // Last thing in the panel: what the status-bar number means, stated after
      // the rows it was computed from so it can be checked against them.
      !error && providers.length > 0 && jsx(ChipExplainer, { providers, services: data?.services }),
      jsx('div', {
        className: 'pl-foot',
        children: jsx(Button, {
          size: 'xs',
          variant: 'text',
          disabled: isFetching,
          // A rejected refresh must surface, not vanish into an unhandled
          // rejection while the panel keeps showing stale numbers.
          onClick: async () => {
            try {
              const fresh = await rest('/limits?refresh=true')
              client.setQueryData(QUERY_KEY, fresh)
              setRefreshError(null)
            } catch (err) {
              setRefreshError(String(err?.message ?? err))
            }
          },
          children: isFetching ? 'Refreshing…' : 'Refresh'
        })
      })
    ]
  })
}

/** Services the status pages report as broken. A confirmed outage only —
 *  an unreachable status page (ok: null) is not evidence of anything. */
function downServices(services) {
  return list(services).filter(service => service.ok === false)
}

/** The 5h row for one provider, or null when it has none.
 *
 *  Selection is on the backend's `window` field, NEVER on the display label:
 *  labels are prose ("5 hours", and "(cost_usd)" suffixes get appended when two
 *  resources share a window), so matching text would silently stop working the
 *  first time a label is reworded.
 *
 *  A provider can legitimately have several 5h rows — codex-lb reports separate
 *  credit and cost_usd ceilings — so the tightest one wins, same rule as the
 *  popover's own collapsing. */
function shortWindowBucket(provider) {
  let best = null
  for (const bucket of list(provider.buckets)) {
    if (bucket.window !== '5h') {
      continue
    }

    const value = pct(bucket.remainingPct)
    if (value !== null && (best === null || value < best.value)) {
      best = { bucket, value }
    }
  }
  return best
}

/** One entry per configured provider, in config order, carrying whatever the
 *  status bar can say about it. Providers are kept even when they have no 5h
 *  figure: a missing column would silently shrink the chip and read as "that
 *  provider is gone". */
function chipEntries(providers, services) {
  const down = downServices(services).length > 0
  return list(providers).map(provider => {
    const best = shortWindowBucket(provider)
    const value = best === null ? null : Math.round(best.value)
    const label = String(provider.label ?? provider.id ?? 'provider')
    return {
      id: provider.id,
      label,
      value,
      // An upstream outage outranks a healthy percentage: quota is irrelevant
      // while the service is down.
      tone: down ? 'down' : provider.ok === false || value === null ? 'unknown' : tone(value),
      title: provider.ok === false
        ? `${label}: ${provider.error ?? 'did not answer'}`
        : value === null
          ? `${label}: no 5h limit reported`
          : `${label} · ${best.bucket.label}: ${value}% left`
    }
  })
}

function Chip() {
  const { data, error } = useLimits()
  const entries = chipEntries(data?.providers, data?.services)
  const down = downServices(data?.services)

  const title = error
    ? 'Provider limits: backend unavailable'
    : [
        entries.length ? entries.map(e => e.title).join('\n') : 'Provider limits',
        down.length ? `⚠ ${down.map(s => s.label).join(', ')} reporting an incident` : ''
      ].filter(Boolean).join('\n')

  // Numbers only, in config order, separated by "|". The names live in the
  // tooltip and the popover: three labels do not fit a status bar, and the
  // order is stable, so the columns stay identifiable without them.
  const children = error || entries.length === 0
    ? [jsx('span', { className: 'pl-chip-item', children: '—' })]
    : entries.flatMap((entry, index) => [
        index > 0 && jsx('span', { className: 'pl-chip-sep', children: '|' }, `sep-${entry.id ?? index}`),
        jsx('span', {
          className: 'pl-chip-item',
          'data-tone': entry.tone,
          children: entry.value === null ? '—' : `${entry.value}%`
        }, entry.id ?? index)
      ].filter(Boolean))

  return jsxs(Popover, {
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx(Button, {
          variant: 'ghost',
          size: 'micro',
          'aria-label': title,
          title,
          children: jsxs('span', {
            className: 'pl-chip',
            children: [down.length ? '⚠' : '⛽', ...children]
          })
        })
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'end',
        className: 'pl-popover',
        'aria-label': 'Provider limits',
        children: jsx(Panel, {})
      })
    ]
  })
}

// Exported for tests; the app only consumes the default export.
export {
  ChipExplainer, chipEntries, clock24, downServices, list, pct,
  relativeReset, resetAt24, ServiceRow, ServicesBanner,
  shortWindowBucket, tone
}

export default {
  id: ID,
  name: 'Provider limits',
  description: 'Remaining quota for custom providers (TeamClaude, Codex LB) in the status bar.',
  register(ctx) {
    rest = path => ctx.rest(path)

    // Idempotent: a hot reload can call register again on a module instance
    // whose previous style element is still in the document. Dropping the old
    // one first keeps a single stylesheet instead of stacking duplicates.
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
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 115,
      render: () => jsx(Chip, {})
    })
  }
}
