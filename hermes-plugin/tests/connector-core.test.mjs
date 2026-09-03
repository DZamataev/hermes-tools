import assert from 'node:assert/strict'
import { createHmac, webcrypto } from 'node:crypto'
import test from 'node:test'

import { createConnector } from '../src/connector-core.js'

const SDK_ROUTE = {
  connectionId: 'local',
  mode: 'local',
  profile: 'default',
  targetProfile: 'backend-default'
}

const WIRE_ROUTE = {
  connection_id: 'local',
  profile: 'default',
  target_profile: 'backend-default'
}

const SECRET = 's'.repeat(32)

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

class FakeSocket {
  constructor(url = '') {
    this.url = url
    this.readyState = 0
    this.sent = []
    this.listeners = new Map()
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? new Set()
    listeners.add(listener)
    this.listeners.set(type, listeners)
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener)
  }

  async emit(type, event = {}) {
    const results = [...(this.listeners.get(type) ?? [])].map(listener => listener(event))
    await Promise.all(results)
    await Promise.resolve()
  }

  async open() {
    this.readyState = 1
    await this.emit('open')
  }

  async receive(frame) {
    await this.emit('message', { data: JSON.stringify(frame) })
  }

  send(raw) {
    if (this.readyState !== 1) {
      throw new Error('WebSocket is closed')
    }
    this.sent.push(JSON.parse(raw))
  }

  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    void this.emit('close')
  }
}

function fakeHost(overrides = {}) {
  const host = {
    calls: [],
    releases: 0,
    disposed: 0,
    eventListener: null,
    async profileRoutes() {
      return [SDK_ROUTE]
    },
    async retainProfile(route) {
      host.calls.push(['retainProfile', route])
      let released = false
      return () => {
        if (!released) {
          released = true
          host.releases += 1
        }
      }
    },
    async requestProfile(route, method, params, timeout) {
      host.calls.push([method, route, params, timeout])
      if (method === 'session.resume') return { session_id: 'runtime-1' }
      if (method === 'prompt.submit') return { status: 'streaming' }
      if (method === 'session.events.since') return { events: [], truncated: false }
      throw new Error(`unexpected RPC: ${method}`)
    },
    onEvent(type, listener) {
      assert.equal(type, '*')
      host.eventListener = listener
      return () => {
        host.disposed += 1
        host.eventListener = null
      }
    },
    emit(event) {
      host.eventListener?.(event)
    },
    ...overrides
  }
  return host
}

function fakeTimers() {
  let nextId = 0
  const state = {
    timeouts: new Map(),
    intervals: new Map(),
    scheduledDelays: [],
    clearedTimeouts: [],
    clearedIntervals: [],
    now: () => 1_788_400_000_000,
    setTimeout(callback, delay) {
      const id = ++nextId
      state.timeouts.set(id, { callback, delay })
      state.scheduledDelays.push(delay)
      return id
    },
    clearTimeout(id) {
      state.clearedTimeouts.push(id)
      state.timeouts.delete(id)
    },
    setInterval(callback, delay) {
      const id = ++nextId
      state.intervals.set(id, { callback, delay })
      return id
    },
    clearInterval(id) {
      state.clearedIntervals.push(id)
      state.intervals.delete(id)
    }
  }
  return state
}

function dependencies({ host = fakeHost(), sockets = [new FakeSocket()], timers = fakeTimers() } = {}) {
  let socketIndex = 0
  function WebSocketImpl(url) {
    const socket = sockets[socketIndex++]
    if (!socket) throw new Error('unexpected reconnect')
    socket.url = url
    return socket
  }
  WebSocketImpl.OPEN = 1
  return {
    connector: createConnector({
      host,
      WebSocketImpl,
      cryptoImpl: webcrypto,
      url: 'ws://127.0.0.1:8787/connector',
      secret: SECRET,
      timers
    }),
    host,
    sockets,
    timers
  }
}

function command(kind, payload, correlationId = 'correlation-1') {
  return {
    protocol: 1,
    kind,
    id: `command-${correlationId}`,
    correlation_id: correlationId,
    sent_at: new Date().toISOString(),
    payload
  }
}

function submit(overrides = {}) {
  return command('submit', {
    operation_id: 'op-1',
    route: WIRE_ROUTE,
    stored_session_id: 'stored-1',
    text: 'hello',
    queued: true,
    ...overrides
  })
}

async function connect(options = {}) {
  const fixture = dependencies(options)
  fixture.connector.start()
  const socket = fixture.sockets[0]
  await socket.open()
  await socket.receive(command('challenge', { nonce: 'nonce-1' }, 'challenge-1'))
  return { ...fixture, socket }
}

function frames(socket, kind) {
  return socket.sent.filter(frame => frame.kind === kind)
}

test('valid challenge response signs nonce and advertises snake-case routes', async () => {
  const { connector, socket, timers } = await connect()
  const hello = frames(socket, 'hello')[0]
  assert.ok(hello)
  assert.equal(hello.protocol, 1)
  assert.equal(hello.correlation_id, 'command-challenge-1')
  assert.deepEqual(hello.payload.routes, [WIRE_ROUTE])
  const expected = createHmac('sha256', SECRET)
    .update(`nonce-1\n${hello.payload.timestamp}`)
    .digest('base64url')
  assert.equal(hello.payload.mac, expected)
  const heartbeatTimer = [...timers.intervals.values()][0]
  assert.equal(heartbeatTimer.delay, 5_000)
  heartbeatTimer.callback()
  const heartbeat = frames(socket, 'heartbeat')[0]
  assert.equal(heartbeat.payload.epoch, hello.id)
  assert.equal(heartbeat.correlation_id, hello.id)
  connector.stop()
})

test('submit resumes and queues on the exact routed session', async () => {
  const { connector, host, socket } = await connect()
  await socket.receive(submit())
  assert.deepEqual(host.calls[0], ['retainProfile', SDK_ROUTE])
  assert.deepEqual(host.calls[1], [
    'session.resume',
    SDK_ROUTE,
    { session_id: 'stored-1', source: 'desktop', omit_messages: true },
    60_000
  ])
  assert.deepEqual(host.calls[2], [
    'prompt.submit',
    SDK_ROUTE,
    { session_id: 'runtime-1', text: 'hello', queued: true },
    60_000
  ])
  assert.deepEqual(frames(socket, 'accepted')[0].payload, {
    operation_id: 'op-1',
    runtime_session_id: 'runtime-1'
  })
  connector.stop()
})

test('wrong route is rejected before retaining or issuing an RPC', async () => {
  const { connector, host, socket } = await connect()
  await socket.receive(submit({ route: { ...WIRE_ROUTE, target_profile: 'other' } }))
  assert.equal(host.calls.length, 0)
  assert.deepEqual(frames(socket, 'command_error')[0].payload, {
    operation_id: 'op-1',
    code: 'route_unavailable',
    message: 'Hermes profile route is unavailable',
    acceptance_unknown: false
  })
  connector.stop()
})

test('resume failure releases the route and reports known non-acceptance', async () => {
  const host = fakeHost({
    async requestProfile(route, method, params, timeout) {
      host.calls.push([method, route, params, timeout])
      throw new Error('stored session not found')
    }
  })
  const { connector, socket } = await connect({ host })
  await socket.receive(submit())
  assert.equal(host.releases, 1)
  assert.equal(frames(socket, 'command_error')[0].payload.acceptance_unknown, false)
  connector.stop()
})

test('explicit prompt rejection is known while transport loss after offer is uncertain', async () => {
  for (const [error, expected] of [
    [new Error('Hermes RPC rejected the prompt'), false],
    [new DOMException('WebSocket closed after request write', 'NetworkError'), true]
  ]) {
    const host = fakeHost({
      async requestProfile(route, method, params, timeout) {
        host.calls.push([method, route, params, timeout])
        if (method === 'session.resume') return { session_id: 'runtime-1' }
        throw error
      }
    })
    const { connector, socket } = await connect({ host })
    await socket.receive(submit())
    assert.equal(frames(socket, 'command_error')[0].payload.acceptance_unknown, expected)
    assert.equal(host.releases, 1)
    connector.stop()
  }
})

test('matching events include sequence and stored tip; wrong-route events are filtered', async () => {
  const { connector, host, socket } = await connect()
  await socket.receive(submit())
  host.emit({
    profile: 'default', session_id: 'runtime-1', seq: 1,
    type: 'message.start', payload: {}
  })
  host.emit({
    connectionId: 'other', profile: 'default', session_id: 'runtime-1', seq: 2,
    type: 'message.delta', payload: { text: 'wrong' }
  })
  host.emit({
    profile: 'default', session_id: 'runtime-1', seq: 3,
    type: 'session.info', payload: { stored_session_id: 'stored-tip-2', running: true }
  })
  await Promise.resolve()
  const events = frames(socket, 'hermes_event')
  assert.equal(events.length, 2)
  assert.deepEqual(events[1].payload, {
    operation_id: 'op-1',
    connection_id: 'local',
    profile: 'default',
    session_id: 'runtime-1',
    seq: 3,
    event_type: 'session.info',
    data: { stored_session_id: 'stored-tip-2', running: true }
  })
  connector.stop()
})

test('terminal event releases once even when Hermes emits it twice', async () => {
  const { connector, host, socket } = await connect()
  await socket.receive(submit())
  host.emit({ profile: 'default', session_id: 'runtime-1', seq: 2, type: 'message.start', payload: {} })
  const terminal = {
    profile: 'default', session_id: 'runtime-1', seq: 3,
    type: 'message.complete', payload: { text: 'done' }
  }
  host.emit(terminal)
  host.emit(terminal)
  await Promise.resolve()
  assert.equal(host.releases, 1)
  connector.stop()
  assert.equal(host.releases, 1)
})

test('queued submit ignores the previous turn terminal and orders early events after acceptance', async () => {
  const prompt = deferred()
  const host = fakeHost({
    async requestProfile(route, method, params, timeout) {
      host.calls.push([method, route, params, timeout])
      if (method === 'session.resume') return { session_id: 'runtime-1' }
      return prompt.promise
    }
  })
  const { connector, socket } = await connect({ host })
  const handling = socket.receive(submit())
  await Promise.resolve()
  await Promise.resolve()
  host.emit({
    profile: 'default', session_id: 'runtime-1', seq: 10,
    type: 'message.complete', payload: { text: 'previous turn' }
  })
  host.emit({
    profile: 'default', session_id: 'runtime-1', seq: 11,
    type: 'message.start', payload: {}
  })
  host.emit({
    profile: 'default', session_id: 'runtime-1', seq: 12,
    type: 'message.delta', payload: { text: 'new turn' }
  })
  assert.equal(host.releases, 0)
  assert.equal(frames(socket, 'accepted').length, 0)
  assert.equal(frames(socket, 'hermes_event').filter(frame => frame.payload.operation_id === 'op-1').length, 0)
  prompt.resolve({ status: 'queued' })
  await handling
  const ordered = socket.sent
    .filter(frame => frame.kind === 'accepted' || frame.payload?.operation_id === 'op-1')
    .map(frame => frame.kind)
  assert.deepEqual(ordered, ['accepted', 'hermes_event', 'hermes_event'])
  assert.equal(host.releases, 0)
  host.emit({
    profile: 'default', session_id: 'runtime-1', seq: 13,
    type: 'message.complete', payload: { text: 'new turn done' }
  })
  assert.equal(host.releases, 1)
  connector.stop()
})

test('replay invokes only session.events.since and forwards returned events', async () => {
  const host = fakeHost({
    async requestProfile(route, method, params, timeout) {
      host.calls.push([method, route, params, timeout])
      return {
        truncated: false,
        events: [{ type: 'message.delta', session_id: 'runtime-1', seq: 8, payload: { text: 'replayed' } }]
      }
    }
  })
  const { connector, socket } = await connect({ host })
  await socket.receive(command('replay', {
    operation_id: 'op-1', route: WIRE_ROUTE, runtime_session_id: 'runtime-1', after_seq: 7
  }))
  assert.deepEqual(host.calls, [[
    'session.events.since', SDK_ROUTE, { session_id: 'runtime-1', last_seen: 7 }, 60_000
  ]])
  assert.equal(frames(socket, 'hermes_event')[0].payload.data.text, 'replayed')
  assert.deepEqual(frames(socket, 'replay_complete')[0].payload, {
    operation_id: 'op-1', after_seq: 7
  })
  connector.stop()
})

test('empty replay emits an explicit terminal replay completion', async () => {
  const host = fakeHost({ async requestProfile() { return { truncated: false, events: [] } } })
  const { connector, socket } = await connect({ host })
  await socket.receive(command('replay', {
    operation_id: 'op-1', route: WIRE_ROUTE, runtime_session_id: 'runtime-1', after_seq: 7
  }))
  assert.deepEqual(frames(socket, 'replay_complete')[0].payload, {
    operation_id: 'op-1', after_seq: 7
  })
  connector.stop()
})

test('truncated replay reports a replay gap', async () => {
  const host = fakeHost({
    async requestProfile() {
      return { truncated: true, latest_seq: 25, events: [{ seq: 20 }] }
    }
  })
  const { connector, socket } = await connect({ host })
  await socket.receive(command('replay', {
    operation_id: 'op-1', route: WIRE_ROUTE, runtime_session_id: 'runtime-1', after_seq: 7
  }))
  assert.deepEqual(frames(socket, 'replay_gap')[0].payload, {
    operation_id: 'op-1', after_seq: 7, oldest_available: 20
  })
  connector.stop()
})

test('stop disposes event subscription, active retain, heartbeat and reconnect timers', async () => {
  const timers = fakeTimers()
  const sockets = [new FakeSocket(), new FakeSocket()]
  const fixture = await connect({ sockets, timers })
  await fixture.socket.receive(submit())
  fixture.socket.close()
  await Promise.resolve()
  const reconnect = [...timers.timeouts.values()][0]
  assert.equal(reconnect.delay, 250)
  fixture.connector.stop()
  assert.equal(fixture.host.disposed, 1)
  assert.equal(fixture.host.releases, 1)
  assert.equal(timers.timeouts.size, 0)
  assert.equal(timers.intervals.size, 0)
})

test('reconnect delays are capped and reset after a successful hello', async () => {
  const timers = fakeTimers()
  const sockets = Array.from({ length: 7 }, () => new FakeSocket())
  const { connector } = dependencies({ sockets, timers })
  connector.start()
  for (let index = 0; index < 5; index += 1) {
    const socket = sockets[index]
    await socket.open()
    socket.close()
    await Promise.resolve()
    const [timerId, timer] = [...timers.timeouts.entries()][0]
    timers.timeouts.delete(timerId)
    timer.callback()
  }
  const delays = [250, 500, 1_000, 2_000, 5_000]
  assert.deepEqual(timers.scheduledDelays, delays)
  const authenticated = sockets[5]
  await authenticated.open()
  await authenticated.receive(command('challenge', { nonce: 'nonce-reset' }, 'challenge-reset'))
  authenticated.close()
  await Promise.resolve()
  assert.equal([...timers.timeouts.values()][0].delay, 250)
  assert.deepEqual(timers.scheduledDelays, [...delays, 250])
  connector.stop()
})

test('an unknown bridge command cannot select an arbitrary Hermes RPC', async () => {
  const { connector, host, socket } = await connect()
  await socket.receive(command('rpc', { method: 'config.set', params: {} }))
  assert.equal(host.calls.length, 0)
  connector.stop()
})
