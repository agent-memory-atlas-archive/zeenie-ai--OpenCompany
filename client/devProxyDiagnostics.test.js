import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'
import { createDevProxyDiagnostics } from './devProxyDiagnostics.js'

const harness = () => {
  const entries = []
  let time = 0
  const logger = Object.fromEntries(['error', 'warn', 'info'].map((level) => [
    level, (message, options) => entries.push({ level, message, options }),
  ]))
  const diagnostics = createDevProxyDiagnostics(logger, { now: () => time })
  const proxy = new EventEmitter()
  diagnostics.configureWebSocketProxy(proxy)
  const request = (url = '/ws') => {
    const upstream = new EventEmitter()
    const socket = new EventEmitter()
    proxy.emit('proxyReqWs', upstream, { url }, socket)
    return { upstream, socket }
  }
  return { ...diagnostics, entries, proxy, request, advance: (ms) => { time += ms } }
}

test('reports the failed HTTP handshake without leaking credentials or response content', () => {
  const { request, entries } = harness()
  request('http://user:password@localhost/ws?token=secret#private').upstream.emit('response', {
    statusCode: 403,
    statusMessage: 'sensitive upstream message',
    headers: { 'set-cookie': 'session=private' },
  })
  assert.equal(entries.length, 1)
  assert.equal(entries[0].level, 'warn')
  assert.equal(entries[0].message, 'WebSocket upgrade failed for /ws: backend returned HTTP 403')
})

test('successful upgrades are quiet while distinct failed statuses remain visible', () => {
  const { request, entries } = harness()
  request().upstream.emit('upgrade', { statusCode: 101 })
  request().upstream.emit('response', { statusCode: 101 })
  assert.equal(entries.length, 0)
  request().upstream.emit('response', { statusCode: 403 })
  request().upstream.emit('response', { statusCode: 502 })
  assert.equal(entries.length, 2)
  assert.match(entries[0].message, /HTTP 403/)
  assert.match(entries[1].message, /HTTP 502/)
})

test('duplicate socket and proxy notifications produce one concise error with a safe path', () => {
  const { request, proxy, logger, entries } = harness()
  const { socket } = request('/ws?token=secret')
  const error = Object.assign(new Error('write ECONNABORTED'), { code: 'ECONNABORTED' })
  socket.emit('error', error)
  logger.error(`ws proxy socket error:\n${error.stack}`, { error })
  proxy.emit('error', error, { url: '/ws?token=secret' })
  logger.error(`ws proxy error:\n${error.stack}`, { error })
  assert.equal(entries.length, 1)
  assert.equal(entries[0].message, 'WebSocket proxy transport error for /ws: ECONNABORTED')
  assert.equal(entries[0].level, 'warn')
})

test('repeated transport failures are rate limited and counted without counting duplicate reports', () => {
  const { request, logger, entries, advance } = harness()
  const fail = () => {
    const { socket } = request()
    const error = Object.assign(new Error('read ECONNRESET'), { code: 'ECONNRESET' })
    socket.emit('error', error)
    logger.error(`ws proxy socket error:\n${error.stack}`, { error })
    logger.error(`ws proxy error:\n${error.stack}`, { error })
  }
  fail()
  fail()
  fail()
  assert.equal(entries.length, 1)
  advance(10_000)
  fail()
  assert.equal(entries.length, 2)
  assert.match(entries[1].message, /2 similar failures since the last notice/)
})

test('an aborted socket cannot hide the upstream rejection or flood repeated handshake notices', () => {
  const { request, logger, entries, advance } = harness()
  const { upstream, socket } = request()
  upstream.emit('response', { statusCode: 401 })
  const error = Object.assign(new Error('write ECONNRESET'), { code: 'ECONNRESET' })
  socket.emit('error', error)
  logger.error(`ws proxy socket error:\n${error.stack}`, { error })
  request().upstream.emit('response', { statusCode: 401 })
  assert.equal(entries.length, 2)
  assert.match(entries[0].message, /HTTP 401/)
  advance(10_000)
  request().upstream.emit('response', { statusCode: 401 })
  assert.equal(entries.length, 3)
  assert.match(entries[2].message, /HTTP 401 \(1 similar failures since the last notice\)/)
})

test('backend startup connection refusals are concise and rate limited from the first failure', () => {
  const { logger, entries, advance } = harness()
  // Node may report ECONNREFUSED only in an AggregateError stack.
  logger.error('ws proxy error:\nAggregateError [ECONNREFUSED]', { error: new AggregateError([]) })
  logger.error('http proxy error: /api?token=secret\nAggregateError [ECONNREFUSED]')
  assert.equal(entries.length, 1)
  assert.equal(entries[0].level, 'info')
  assert.match(entries[0].message, /backend unavailable \(ECONNREFUSED\)/)
  assert.doesNotMatch(entries[0].message, /secret/)
  advance(10_000)
  logger.error('ws proxy error:\nAggregateError [ECONNREFUSED]')
  assert.equal(entries.length, 2)
})

test('unexpected errors and HTTP resets retain their full diagnostic evidence', () => {
  const { logger, entries } = harness()
  for (const message of [
    'ws proxy error:\nError: certificate has expired',
    'http proxy error: /api\nError: ECONNRESET',
    'unrelated plugin failed with ECONNREFUSED',
  ]) {
    const options = { error: new Error(message) }
    logger.error(message, options)
    assert.deepEqual(entries.at(-1), { level: 'error', message, options })
  }
  assert.equal(entries.length, 3)
})

test('untrusted malformed and long paths produce bounded, printable diagnostics', () => {
  const { request, entries } = harness()
  request('http://[').upstream.emit('response', {})
  assert.equal(entries[0].message,
    'WebSocket upgrade failed for <invalid path>: backend returned HTTP unknown')
  request('/ws/\u001b[31m' + 'x'.repeat(1000) + '?token=secret')
    .upstream.emit('response', { statusCode: 404 })
  assert.ok(entries[1].message.length < 300)
  assert.doesNotMatch(entries[1].message, /[\u0000-\u001f\u007f-\u009f]|secret/)
})
