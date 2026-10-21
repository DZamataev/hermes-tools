// Voice assistant — the microphone switch of a headless voice Hermes, in the status bar,
// a music block (the player on the same host) and a field that types a prompt into the
// assistant's own session, all inside its popover.
//
// The chip shows whether the assistant is listening (🎙), switched off (🔇) or
// unknown (—, host unreachable or backend off). Tapping it opens a popover with
// the host, the state and the one button that flips it, a prompt field, then what is
// playing and the pause / next / stop / volume controls.
//
// All work goes through this package's Python half
// (`~/.hermes/plugins/voice-control/dashboard/plugin_api.py`) via `ctx.rest`; it
// runs ssh to the host from the plugin settings. That half is imported only when
// `voice-control` is in `plugins.enabled` — until then the chip shows a muted dash.
import {
  Button, Input, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS,
  useQuery, useQueryClient
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'voice-control'
const QUERY_KEY = [ID, 'state']
const MUSIC_KEY = [ID, 'music']
// Captured in `register`: components render in the app's tree, outside its closure.
let rest = async () => { throw new Error('plugin not registered') }
let styleElement = null
// One ssh round trip per poll (~1 s); the state only changes when someone flips it.
const REFETCH_MS = 30_000
// The backend may wait up to 20 s for a running turn before giving up.
const TURN_TIMEOUT_MS = 75_000
// The backend gives a music call 15 s.
const MUSIC_TIMEOUT_MS = 20_000
// Volume buttons move mpv by this much, like `music volume +10`.
const VOLUME_STEP = 10

const CSS = `
.vc-chip{display:inline-flex;align-items:center;gap:3px;font-variant-numeric:tabular-nums}
.vc-chip[data-tone="off"],.vc-chip[data-tone="unknown"]{color:var(--ui-text-tertiary)}
.vc-popover{width:260px;display:flex;flex-direction:column;gap:8px;font-size:.72rem}
.vc-title{font-weight:600}
.vc-row{display:flex;justify-content:space-between;gap:8px;color:var(--ui-text-tertiary)}
.vc-row>b{color:var(--ui-text-primary,inherit);font-weight:500}
.vc-error{color:var(--dt-destructive);line-height:1.35}
.vc-note{font-size:.62rem;line-height:1.4;color:var(--ui-text-quaternary);padding-top:6px;border-top:1px solid var(--ui-stroke-quaternary)}
.vc-music{display:flex;flex-direction:column;gap:6px;padding-top:8px;border-top:1px solid var(--ui-stroke-quaternary)}
.vc-now{line-height:1.35}
.vc-controls{display:flex;align-items:center;gap:2px}
.vc-spacer{flex:1}
.vc-volume{min-width:2em;text-align:center;font-variant-numeric:tabular-nums}
.vc-prompt{display:flex;flex-direction:column;gap:6px;padding-top:8px;border-top:1px solid var(--ui-stroke-quaternary)}
.vc-prompt-row{display:flex;align-items:center;gap:4px}
`

/** What the chip draws for one moment: the query result, its error, a toggle in flight. */
function chipView(data, error, pending) {
  if (pending) return { glyph: '…', tone: 'unknown', title: 'Voice assistant: switching…' }
  if (error) return { glyph: '—', tone: 'unknown', title: 'Voice assistant: plugin backend unavailable' }
  if (!data || data.listening === null || data.listening === undefined) {
    return { glyph: '—', tone: 'unknown', title: `Voice assistant: ${data?.message || 'state unknown'}` }
  }
  return data.listening
    ? { glyph: '🎙', tone: 'on', title: `Voice assistant on ${data.host}: listening` }
    : { glyph: '🔇', tone: 'off', title: `Voice assistant on ${data.host}: microphone off` }
}

/** The state line of the popover. */
function stateLabel(data) {
  if (!data || data.listening === null || data.listening === undefined) return 'unknown'
  if (data.listening) return 'listening'
  return 'microphone off'
}

function useVoiceState() {
  return useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => rest('/state'),
    refetchInterval: REFETCH_MS,
    staleTime: REFETCH_MS,
    retry: false
  })
}

/** What the player says it is doing, as one line. */
function musicLine(data) {
  if (!data || !data.state) return '—'
  if (data.state === 'stopped') return 'ничего не играет'
  const name = data.kind === 'radio'
    ? [data.station, data.icy_title].filter(Boolean).join(' · ')
    : [data.artist, data.title].filter(Boolean).join(' — ')
  return (data.state === 'paused' ? '⏸ ' : '') + (name || '—')
}

/**
 * The music block: one status line and the transport buttons. Mounted with the popover,
 * so the status is fetched on open; afterwards the POST reply is the fresh status, and
 * there is no polling.
 */
function MusicSection() {
  const client = useQueryClient()
  const { data } = useQuery({ queryKey: MUSIC_KEY, queryFn: () => rest('/music'), retry: false })
  const [pending, setPending] = useState(false)
  // Nothing to drive when the player is not playing (or has not answered yet).
  const off = pending || !data || !data.state || data.state === 'stopped'

  const act = async (cmd, arg) => {
    if (off) return
    setPending(true)
    try {
      const next = await rest('/music', { method: 'POST', body: { cmd, arg }, timeoutMs: MUSIC_TIMEOUT_MS })
      // A failed command answers with no state: keep the last good one, add the error.
      client.setQueryData(MUSIC_KEY, next?.state ? next : { ...data, error: next?.error, message: next?.message })
    } catch (err) {
      client.setQueryData(MUSIC_KEY, { ...data, error: 'request_failed', message: String(err?.message || err) })
    } finally {
      setPending(false)
    }
  }

  const button = (label, glyph, onClick, disabled = off) => jsx(Button, {
    size: 'micro', variant: 'ghost', disabled, 'aria-label': label, title: label, onClick, children: glyph
  })

  return jsxs('div', {
    className: 'vc-music',
    children: [
      jsx('div', { className: 'vc-title', children: 'Музыка' }),
      jsx('div', { className: 'vc-now', children: pending ? '…' : musicLine(data) }),
      data?.error ? jsx('div', { className: 'vc-error', children: data.message || data.error }) : null,
      jsxs('div', {
        className: 'vc-controls',
        children: [
          button('Пауза или продолжить', '⏯', () => act('toggle')),
          button('Следующий трек', '⏭', () => act('next')),
          button('Стоп', '⏹', () => act('stop')),
          jsx('span', { className: 'vc-spacer' }),
          button('Тише', '−', () => act('volume', `-${VOLUME_STEP}`)),
          jsx('span', { className: 'vc-volume', children: String(data?.volume ?? '—') }),
          button('Громче', '+', () => act('volume', `+${VOLUME_STEP}`))
        ]
      })
    ].filter(Boolean)
  })
}

/**
 * Send a typed prompt into the CLI's session: the assistant answers and speaks the answer,
 * so nothing of the reply comes back here. One state object, because the popover re-renders
 * on every keystroke.
 */
function PromptBox() {
  const [draft, setDraft] = useState({ text: '', status: null, pending: false })

  const submit = async () => {
    const text = (draft.text || '').trim()
    if (!text || draft.pending) return
    setDraft({ ...draft, pending: true, status: null })
    let status
    try {
      const reply = await rest('/prompt', { method: 'POST', body: { text }, timeoutMs: TURN_TIMEOUT_MS })
      status = reply?.error
        ? { error: reply.error, message: reply.message || reply.error }
        : { message: 'Отправлено — ответ прозвучит голосом' }
    } catch (err) {
      status = { error: 'request_failed', message: String(err?.message || err) }
    }
    // The text stays in the field when it did not go through, so it is not lost.
    setDraft({ text: status.error ? draft.text : '', pending: false, status })
  }

  return jsxs('div', {
    className: 'vc-prompt',
    children: [
      jsxs('div', {
        className: 'vc-prompt-row',
        children: [
          jsx(Input, {
            size: 'xs',
            value: draft.text,
            disabled: !!draft.pending,
            placeholder: 'Спросить ассистента',
            'aria-label': 'Текст для голосового ассистента',
            onChange: e => setDraft({ ...draft, text: e.target.value, status: null }),
            onKeyDown: e => {
              if (e.key !== 'Enter') return
              e.preventDefault()
              submit()
            }
          }),
          jsx(Button, {
            size: 'micro', variant: 'ghost', disabled: !!draft.pending, onClick: submit, children: 'Отправить'
          })
        ]
      }),
      draft.status
        ? jsx('div', { className: draft.status.error ? 'vc-error' : 'vc-row', children: draft.status.message })
        : null
    ].filter(Boolean)
  })
}

function Panel({ data, error, pending, onToggle }) {
  const known = data && typeof data.listening === 'boolean'
  return jsxs('div', {
    className: 'vc-popover',
    children: [
      jsx('div', { className: 'vc-title', children: 'Voice assistant' }),
      jsxs('div', { className: 'vc-row', children: ['Host', jsx('b', { children: data?.host ?? '—' })] }),
      jsxs('div', { className: 'vc-row', children: ['State', jsx('b', { children: pending ? 'switching…' : stateLabel(data) })] }),
      error
        ? jsx('div', { className: 'vc-error', children: 'Plugin backend unavailable: enable voice-control (hermes plugins enable voice-control) and restart Hermes.' })
        : data?.error
          ? jsx('div', { className: 'vc-error', children: data.message || data.error })
          : null,
      jsx(Button, {
        size: 'sm',
        variant: known && data.listening ? 'outline' : 'default',
        disabled: pending || !known,
        onClick: onToggle,
        children: known && data.listening ? 'Turn microphone off' : 'Turn microphone on'
      }),
      jsx('div', {
        className: 'vc-note',
        children: 'Off stops the "Hey Hermes" listener and voice mode and is saved in the assistant\'s config, so it stays off after a restart.'
      }),
      jsx(PromptBox, {}),
      jsx(MusicSection, {})
    ].filter(Boolean)
  })
}

function Chip() {
  const { data, error } = useVoiceState()
  const client = useQueryClient()
  const [pending, setPending] = useState(false)
  const view = chipView(data, error, pending)

  const onToggle = async () => {
    if (pending || !data || typeof data.listening !== 'boolean') return
    setPending(true)
    try {
      const next = await rest('/state', {
        method: 'POST', body: { listening: !data.listening }, timeoutMs: TURN_TIMEOUT_MS
      })
      client.setQueryData(QUERY_KEY, next)
    } catch (err) {
      client.setQueryData(QUERY_KEY, { ...data, error: 'request_failed', message: String(err?.message || err) })
    } finally {
      setPending(false)
    }
  }

  return jsxs(Popover, {
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx(Button, {
          variant: 'ghost',
          size: 'micro',
          'aria-label': view.title,
          title: view.title,
          children: jsx('span', { className: 'vc-chip', 'data-tone': view.tone, children: view.glyph })
        })
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'end',
        'aria-label': 'Voice assistant',
        children: jsx(Panel, { data, error, pending, onToggle })
      })
    ]
  })
}

// Exported for tests; the app only consumes the default export.
export { Chip, chipView, Panel, stateLabel, MusicSection, musicLine, PromptBox }

export default {
  id: ID,
  name: 'Voice assistant',
  description: 'Turn the voice Hermes microphone on and off over ssh, type a prompt into its session, and steer the music player on the same host.',
  register(ctx) {
    rest = (path, opts) => ctx.rest(path, opts)

    styleElement?.remove()
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    styleElement = style
    ctx.onDispose(() => {
      style.remove()
      if (styleElement === style) styleElement = null
    })

    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 112,
      render: () => jsx(Chip, {})
    })
  }
}
