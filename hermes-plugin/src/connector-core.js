const PROTOCOL_VERSION = 1
const CONNECTOR_VERSION = '1.0.0'
const HEARTBEAT_INTERVAL_MS = 5_000
const REQUEST_TIMEOUT_MS = 60_000
const MAX_FRAME_BYTES = 1024 * 1024
const RECONNECT_DELAYS_MS = [250, 500, 1_000, 2_000, 5_000]

const FORWARDED_EVENTS = new Set([
  'message.start',
  'message.delta',
  'reasoning.delta',
  'tool.start',
  'tool.progress',
  'tool.complete',
  'message.complete',
  'session.info',
  'error'
])

const TERMINAL_EVENTS = new Set(['message.complete', 'error'])

function defaultTimers() {
  return {
    clearInterval: globalThis.clearInterval.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    now: Date.now,
    setInterval: globalThis.setInterval.bind(globalThis),
    setTimeout: globalThis.setTimeout.bind(globalThis)
  }
}

function sdkRouteToWire(route) {
  return {
    connection_id: String(route.connectionId || ''),
    profile: String(route.profile || ''),
    target_profile: String(route.targetProfile || '')
  }
}

function routesEqual(left, right) {
  return Boolean(
    left &&
      right &&
      left.connection_id === right.connection_id &&
      left.profile === right.profile &&
      left.target_profile === right.target_profile
  )
}

function eventData(payload) {
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    return payload
  }
  return payload === undefined ? {} : { value: payload }
}

function safeString(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function isTransportFailure(error) {
  if (error?.acceptanceUnknown === true || error?.name === 'NetworkError') {
    return true
  }
  const message = String(error?.message || error || '').toLowerCase()
  return /\b(closed|disconnect(?:ed)?|network|socket|timed?\s*out|timeout)\b/.test(message)
}

async function signChallenge(cryptoImpl, secret, nonce, timestamp) {
  const encoder = new TextEncoder()
  const key = await cryptoImpl.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { hash: 'SHA-256', name: 'HMAC' },
    false,
    ['sign']
  )
  const signature = new Uint8Array(
    await cryptoImpl.subtle.sign('HMAC', key, encoder.encode(`${nonce}\n${timestamp}`))
  )
  let binary = ''
  for (const byte of signature) binary += String.fromCharCode(byte)
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
}

/**
 * Connect the supported Hermes Desktop plugin host to bridge protocol v1.
 * All bridge commands are handled by fixed methods below; no input frame can
 * select an arbitrary Hermes RPC method.
 */
export function createConnector({
  host,
  WebSocketImpl,
  cryptoImpl,
  url,
  secret,
  timers: suppliedTimers
}) {
  if (!host || !WebSocketImpl || !cryptoImpl?.subtle) {
    throw new Error('Hermes bridge connector dependencies are unavailable')
  }
  if (!safeString(url) || safeString(secret).length < 32) {
    throw new Error('Hermes bridge connector configuration is invalid')
  }

  const timers = { ...defaultTimers(), ...(suppliedTimers || {}) }
  let socket = null
  let stopped = true
  let started = false
  let epoch = null
  let reconnectAttempt = 0
  let reconnectTimer = null
  let heartbeatTimer = null
  let disposeEvents = null
  let sequence = 0
  let routes = []
  const activeBySession = new Map()

  function frame(kind, correlationId, payload) {
    return {
      protocol: PROTOCOL_VERSION,
      kind,
      id: cryptoImpl.randomUUID?.() || `bridge-${timers.now()}-${++sequence}`,
      correlation_id: correlationId,
      sent_at: new Date(timers.now()).toISOString(),
      payload
    }
  }

  function socketOpen(target = socket) {
    return Boolean(target && target.readyState === (WebSocketImpl.OPEN ?? 1))
  }

  function send(target, value) {
    if (!socketOpen(target)) return false
    try {
      target.send(JSON.stringify(value))
      return true
    } catch {
      return false
    }
  }

  function clearHeartbeat() {
    if (heartbeatTimer !== null) {
      timers.clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function clearReconnect() {
    if (reconnectTimer !== null) {
      timers.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function releaseOperation(operation) {
    if (!operation || operation.released) return
    operation.released = true
    if (activeBySession.get(operation.runtimeSessionId) === operation) {
      activeBySession.delete(operation.runtimeSessionId)
    }
    operation.release()
  }

  function routeForWire(wireRoute) {
    return routes.find(candidate => routesEqual(candidate.wire, wireRoute)) || null
  }

  function routeForEvent(event) {
    const profile = safeString(event?.profile)
    const connectionId = safeString(event?.connectionId)
    if (!profile) return null
    const matches = routes.filter(candidate => {
      if (candidate.sdk.profile !== profile) return false
      if (connectionId) return candidate.sdk.connectionId === connectionId
      return candidate.sdk.mode === 'local' && candidate.sdk.connectionId === 'local'
    })
    return matches.length === 1 ? matches[0] : null
  }

  function emitHermesEvent(event, { correlationId, operationId = null, route, target = socket }) {
    const eventType = safeString(event?.type)
    const sessionId = safeString(event?.session_id)
    if (!FORWARDED_EVENTS.has(eventType) || !sessionId || !route) return false
    const seq = Number.isInteger(event.seq) && event.seq >= 0 ? event.seq : null
    return send(
      target,
      frame('hermes_event', correlationId, {
        operation_id: operationId,
        connection_id: route.wire.connection_id,
        profile: route.wire.profile,
        session_id: sessionId,
        seq,
        event_type: eventType,
        data: eventData(event.payload)
      })
    )
  }

  function handleHostEvent(event) {
    const route = routeForEvent(event)
    if (!route) return
    const sessionId = safeString(event?.session_id)
    const operation = activeBySession.get(sessionId)
    if (operation && operation.route !== route) return
    const eventId = cryptoImpl.randomUUID?.() || `event-${timers.now()}-${++sequence}`
    if (!operation || (!operation.started && event.type !== 'message.start')) {
      emitHermesEvent(event, { correlationId: eventId, route })
      return
    }
    if (event.type === 'message.start') operation.started = true
    if (!operation.acknowledged) {
      operation.pendingEvents.push(event)
      if (TERMINAL_EVENTS.has(event.type)) operation.terminalPending = true
      return
    }
    emitHermesEvent(event, {
      correlationId: operation.correlationId,
      operationId: operation.operationId,
      route
    })
    if (TERMINAL_EVENTS.has(event.type)) {
      releaseOperation(operation)
    }
  }

  async function refreshRoutes() {
    const discovered = await host.profileRoutes()
    if (!Array.isArray(discovered)) throw new Error('Hermes profile route inventory is invalid')
    routes = discovered
      .filter(
        route =>
          safeString(route?.connectionId) &&
          safeString(route?.profile) &&
          safeString(route?.targetProfile)
      )
      .map(route => ({ sdk: { ...route }, wire: sdkRouteToWire(route) }))
    return routes
  }

  function commandError(target, command, code, message, acceptanceUnknown) {
    const operationId = safeString(command?.payload?.operation_id) || 'unknown'
    send(
      target,
      frame('command_error', safeString(command?.correlation_id) || operationId, {
        operation_id: operationId,
        code,
        message,
        acceptance_unknown: acceptanceUnknown
      })
    )
  }

  async function handleSubmit(command, target) {
    const payload = command?.payload || {}
    const operationId = safeString(payload.operation_id)
    if (!operationId || payload.queued !== true || !safeString(payload.stored_session_id) || !safeString(payload.text)) {
      commandError(target, command, 'invalid_submit', 'Invalid submit command', false)
      return
    }
    const route = routeForWire(payload.route)
    if (!route) {
      commandError(target, command, 'route_unavailable', 'Hermes profile route is unavailable', false)
      return
    }

    let release = null
    let operation = null
    let stage = 'retain'
    try {
      release = await host.retainProfile(route.sdk)
      if (typeof release !== 'function') throw new Error('Hermes route retain did not return a disposer')
      stage = 'resume'
      const resumed = await host.requestProfile(
        route.sdk,
        'session.resume',
        { session_id: payload.stored_session_id, source: 'desktop', omit_messages: true },
        REQUEST_TIMEOUT_MS
      )
      const runtimeSessionId = safeString(resumed?.session_id)
      if (!runtimeSessionId) throw new Error('Hermes session resume returned no runtime session')
      if (activeBySession.has(runtimeSessionId)) {
        throw new Error('Hermes runtime session already has an active bridge operation')
      }
      operation = {
        correlationId: command.correlation_id,
        operationId,
        release,
        released: false,
        route,
        runtimeSessionId,
        acknowledged: false,
        pendingEvents: [],
        started: false,
        terminalPending: false
      }
      activeBySession.set(runtimeSessionId, operation)
      stage = 'prompt'
      const acknowledged = await host.requestProfile(
        route.sdk,
        'prompt.submit',
        { session_id: runtimeSessionId, text: payload.text, queued: true },
        REQUEST_TIMEOUT_MS
      )
      if (acknowledged?.status === 'rejected') {
        throw new Error('Hermes rejected the prompt')
      }
      send(
        target,
        frame('accepted', command.correlation_id, {
          operation_id: operationId,
          runtime_session_id: runtimeSessionId
        })
      )
      operation.acknowledged = true
      for (const event of operation.pendingEvents) {
        emitHermesEvent(event, {
          correlationId: operation.correlationId,
          operationId: operation.operationId,
          route,
          target
        })
      }
      operation.pendingEvents = []
      if (operation.terminalPending) releaseOperation(operation)
    } catch (error) {
      if (operation) releaseOperation(operation)
      else if (release) release()
      const acceptanceUnknown = stage === 'prompt' && isTransportFailure(error)
      commandError(
        target,
        command,
        stage === 'resume' ? 'resume_failed' : 'submit_failed',
        stage === 'resume' ? 'Hermes session resume failed' : 'Hermes prompt submit failed',
        acceptanceUnknown
      )
    }
  }

  async function handleReplay(command, target) {
    const payload = command?.payload || {}
    const operationId = safeString(payload.operation_id)
    const runtimeSessionId = safeString(payload.runtime_session_id)
    const afterSeq = payload.after_seq
    const route = routeForWire(payload.route)
    if (!operationId || !runtimeSessionId || !Number.isInteger(afterSeq) || afterSeq < 0) {
      commandError(target, command, 'invalid_replay', 'Invalid replay command', false)
      return
    }
    if (!route) {
      commandError(target, command, 'route_unavailable', 'Hermes profile route is unavailable', false)
      return
    }
    try {
      const replay = await host.requestProfile(
        route.sdk,
        'session.events.since',
        { session_id: runtimeSessionId, last_seen: afterSeq },
        REQUEST_TIMEOUT_MS
      )
      const events = Array.isArray(replay?.events) ? replay.events : []
      if (replay?.truncated === true) {
        const firstSeq = events.find(event => Number.isInteger(event?.seq) && event.seq >= 0)?.seq
        const oldestAvailable = firstSeq ?? Math.max(afterSeq, Number(replay?.latest_seq) || afterSeq)
        send(
          target,
          frame('replay_gap', command.correlation_id, {
            operation_id: operationId,
            after_seq: afterSeq,
            oldest_available: oldestAvailable
          })
        )
        return
      }
      for (const event of events) {
        emitHermesEvent(event, {
          correlationId: command.correlation_id,
          operationId,
          route,
          target
        })
      }
      send(
        target,
        frame('replay_complete', command.correlation_id, {
          operation_id: operationId,
          after_seq: afterSeq
        })
      )
    } catch {
      commandError(target, command, 'replay_failed', 'Hermes event replay failed', false)
    }
  }

  async function handleCommand(command, target = socket) {
    if (!command || command.protocol !== PROTOCOL_VERSION) return
    if (command.kind === 'submit') {
      await handleSubmit(command, target)
    } else if (command.kind === 'replay') {
      await handleReplay(command, target)
    }
  }

  function startHeartbeat(target) {
    clearHeartbeat()
    heartbeatTimer = timers.setInterval(() => {
      if (target !== socket || !socketOpen(target) || !epoch) return
      send(target, frame('heartbeat', epoch, { epoch }))
    }, HEARTBEAT_INTERVAL_MS)
  }

  async function handleChallenge(challenge, target) {
    if (
      challenge?.protocol !== PROTOCOL_VERSION ||
      challenge?.kind !== 'challenge' ||
      !safeString(challenge?.id) ||
      !safeString(challenge?.payload?.nonce)
    ) {
      target.close()
      return
    }
    try {
      const currentRoutes = await refreshRoutes()
      const timestamp = Math.floor(timers.now() / 1_000)
      const mac = await signChallenge(cryptoImpl, secret, challenge.payload.nonce, timestamp)
      if (target !== socket || stopped || !socketOpen(target)) return
      const hello = frame('hello', challenge.id, {
        timestamp,
        mac,
        connector_version: CONNECTOR_VERSION,
        routes: currentRoutes.map(route => route.wire)
      })
      epoch = hello.id
      send(target, hello)
      reconnectAttempt = 0
      startHeartbeat(target)
    } catch {
      target.close()
    }
  }

  async function handleMessage(event, target) {
    const raw = typeof event?.data === 'string' ? event.data : ''
    if (!raw || new TextEncoder().encode(raw).byteLength > MAX_FRAME_BYTES) {
      target.close()
      return
    }
    let incoming
    try {
      incoming = JSON.parse(raw)
    } catch {
      target.close()
      return
    }
    if (incoming.kind === 'challenge') {
      await handleChallenge(incoming, target)
    } else {
      await handleCommand(incoming, target)
    }
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer !== null) return
    const delay = RECONNECT_DELAYS_MS[Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)]
    reconnectAttempt += 1
    reconnectTimer = timers.setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function connect() {
    if (stopped) return
    clearHeartbeat()
    let target
    try {
      target = new WebSocketImpl(url)
    } catch {
      scheduleReconnect()
      return
    }
    socket = target
    let settled = false
    const onMessage = event => handleMessage(event, target)
    const onClose = () => {
      if (settled || target !== socket) return
      settled = true
      clearHeartbeat()
      socket = null
      scheduleReconnect()
    }
    const onError = () => {
      if (target === socket) target.close()
    }
    target.addEventListener('message', onMessage)
    target.addEventListener('close', onClose)
    target.addEventListener('error', onError)
  }

  function start() {
    if (started && !stopped) return
    started = true
    stopped = false
    disposeEvents = host.onEvent('*', handleHostEvent)
    connect()
  }

  function stop() {
    if (stopped) return
    stopped = true
    clearHeartbeat()
    clearReconnect()
    disposeEvents?.()
    disposeEvents = null
    for (const operation of [...activeBySession.values()]) releaseOperation(operation)
    const target = socket
    socket = null
    target?.close()
  }

  return { handleCommand, start, stop }
}
