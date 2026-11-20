// The rule: the SDK follows a redirect only when the target has the same
// scheme, host and port as the configured endpoint. A redirect anywhere else
// is an error that names the other host and says to configure the final URL,
// and no request (so no API key, session token or login form) is sent there.
// Before this rule, fetch followed the redirect and re-sent X-API-Key,
// X-Session-Token and, on a 307 / 308, the body.
import assert from 'node:assert/strict'
import test from 'node:test'

import { LiveSession, TestLookupReporter } from '../dist/testlookup-reporter.js'

const HOME = 'http://testlookup.invalid'
const OTHER = 'http://other.invalid'
/** Shaped like a minted key; not a key. */
const FAKE_KEY = 'qai_' + 'x'.repeat(43)

function reply(status, body, location = null) {
  return {
    status,
    ok: status >= 200 && status < 300,
    type: 'basic',
    headers: { get: (name) => (name.toLowerCase() === 'location' ? location : null) },
    json: async () => body,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  }
}

/** A fake network: `route(call)` answers each request; every request is recorded. */
function installNetwork(route) {
  const calls = []
  const original = globalThis.fetch
  globalThis.fetch = async (url, init = {}) => {
    const parsed = new URL(url)
    const call = {
      origin: parsed.origin,
      path: parsed.pathname,
      method: init.method ?? 'GET',
      headers: { ...(init.headers ?? {}) },
      body: init.body,
      redirect: init.redirect,
    }
    calls.push(call)
    return route(call)
  }
  return {
    calls,
    toOther: () => calls.filter((c) => c.origin !== new URL(HOME).origin),
    restore: () => { globalThis.fetch = original },
  }
}

const SESSION = { session_id: 's-1', session_token: 'st-1', run_id: 'r-1', project_id: 'p', expires_in: 3600, created_at: '' }

/** The other host answers everything with a success, as a credential collector would. */
const collector = () => reply(200, { ...SESSION, access_token: 'stolen', accepted: 1 })

function reporter(baseUrl = HOME) {
  return new TestLookupReporter({ baseUrl, projectId: 'p', apiKey: FAKE_KEY })
}

/** The rejection of a call that must fail; a session that was opened anyway is closed so its timer cannot keep the run alive. */
async function rejection(promise) {
  return promise.then(async (value) => { await value?.close?.(); return null }, (err) => err)
}

async function refusal(promise) {
  const error = await rejection(promise)
  assert.ok(error, 'a redirect to another host must reject')
  assert.match(error.message, /other\.invalid/, 'the error names the redirect host')
  assert.match(error.message, /final URL/, 'the error says to configure the final URL')
  return error
}

for (const status of [301, 302, 303, 307, 308]) {
  test(`session create answered with a ${status} to another host is refused and nothing is sent there`, async () => {
    const net = installNetwork((call) => (
      call.origin === OTHER ? collector() : reply(status, undefined, `${OTHER}${call.path}`)
    ))
    try {
      await refusal(reporter().startSession({ buildNumber: 'b-1' }))
      assert.equal(net.calls.length, 1)
      assert.deepEqual(net.toOther(), [], 'the other host received a request')
      assert.equal(net.calls[0].redirect, 'manual', 'fetch must not follow redirects by itself')
    } finally {
      net.restore()
    }
  })
}

test('login answered with a redirect to another host is refused and the form is not sent there', async () => {
  const net = installNetwork((call) => (
    call.origin === OTHER ? collector() : reply(307, undefined, `${OTHER}/api/v1/auth/login`)
  ))
  try {
    await refusal(TestLookupReporter.login(HOME, 'dana', 'ci-password-test-only'))
    assert.deepEqual(net.toOther(), [])
    assert.equal(net.calls[0].redirect, 'manual')
  } finally {
    net.restore()
  }
})

test('a batch answered with a redirect to another host is not sent there and not retried', async () => {
  const net = installNetwork((call) => (
    call.origin === OTHER ? collector() : reply(308, undefined, `${OTHER}${call.path}`)
  ))
  const session = new LiveSession({
    sessionId: 's-1', sessionToken: 'st-1', runId: 'r-1', baseUrl: HOME,
    batchSize: 50, batchIntervalMs: 60_000, timeoutMs: 1_000,
  })
  const errors = []
  const originalError = console.error
  console.error = (...args) => { errors.push(args.map(String).join(' ')) }
  try {
    await session.record('release_pass', 'PASSED', 1)
    await session.close()
    assert.deepEqual(net.toOther(), [], 'X-Session-Token left the configured host')
    assert.equal(net.calls.length, 1, 'a refused redirect is not retried')
    assert.equal(net.calls[0].headers['X-Session-Token'], 'st-1')
    assert.deepEqual(session.stats, { sent: 0, failed: 1 })
    assert.ok(errors.some((line) => /other\.invalid/.test(line)), 'the refusal is logged with the redirect host')
  } finally {
    console.error = originalError
    net.restore()
  }
})

test('https to http on the same host is another origin and is refused', async () => {
  const secure = 'https://testlookup.invalid'
  const net = installNetwork((call) => (
    call.origin === secure ? reply(307, undefined, `http://testlookup.invalid${call.path}`) : collector()
  ))
  try {
    const error = await rejection(reporter(secure).startSession({}))
    assert.ok(error)
    assert.match(error.message, /http:\/\/testlookup\.invalid/)
    assert.equal(net.calls.length, 1)
    assert.equal(net.calls[0].origin, secure)
  } finally {
    net.restore()
  }
})

test('a redirect on the same origin is still followed, with the method, credential and body', async () => {
  const net = installNetwork((call) => {
    if (call.path === '/api/v1/stream/sessions') return reply(307, undefined, '/api/v1/stream/sessions/')
    if (call.path === '/api/v1/stream/sessions/') return reply(201, SESSION)
    return reply(204)
  })
  try {
    const session = await reporter().startSession({ buildNumber: 'b-1' })
    assert.equal(session.sessionId, 's-1')
    await session.close()
    const [first, second] = net.calls
    assert.equal(second.origin, new URL(HOME).origin)
    assert.equal(second.path, '/api/v1/stream/sessions/')
    assert.equal(second.method, 'POST')
    assert.equal(second.headers['X-API-Key'], FAKE_KEY)
    assert.equal(second.body, first.body)
    assert.equal(JSON.parse(second.body).build_number, 'b-1')
  } finally {
    net.restore()
  }
})

test('a same-origin 303 to a POST is followed as a GET without the body, as fetch does', async () => {
  const net = installNetwork((call) => (
    call.path === '/api/v1/auth/login' ? reply(303, undefined, `${HOME}/api/v1/auth/done`) : reply(200, { access_token: 'jwt-test-only' })
  ))
  try {
    assert.equal(await TestLookupReporter.login(HOME, 'dana', 'ci-password-test-only'), 'jwt-test-only')
    const second = net.calls[1]
    assert.equal(second.method, 'GET')
    assert.equal(second.body, undefined)
    assert.equal(second.headers['Content-Type'], undefined)
  } finally {
    net.restore()
  }
})

test('an endless same-origin redirect stops with an error', async () => {
  const net = installNetwork((call) => reply(307, undefined, call.path))
  try {
    const error = await rejection(reporter().startSession({}))
    assert.ok(error)
    assert.match(error.message, /redirect/)
    assert.ok(net.calls.length <= 10, `followed ${net.calls.length} redirects`)
  } finally {
    net.restore()
  }
})

test('a redirect the runtime hides (a browser opaque redirect) is an error, never followed', async () => {
  const net = installNetwork(() => ({
    status: 0, ok: false, type: 'opaqueredirect', headers: { get: () => null }, json: async () => ({}), text: async () => '',
  }))
  try {
    const error = await rejection(reporter().startSession({}))
    assert.ok(error)
    assert.match(error.message, /redirect/)
    assert.match(error.message, /final URL/)
    assert.equal(net.calls.length, 1)
  } finally {
    net.restore()
  }
})

test('a direct answer is unaffected', async () => {
  const net = installNetwork((call) => {
    if (call.method === 'POST' && call.path === '/api/v1/stream/sessions') return reply(201, SESSION)
    if (call.path === '/api/v1/stream/events/batch') return reply(202, { accepted: 1 })
    return reply(204)
  })
  try {
    const session = await reporter().startSession({})
    await session.record('release_pass', 'PASSED', 1)
    await session.close()
    assert.deepEqual(session.stats, { sent: 1, failed: 0 })
    assert.deepEqual(net.toOther(), [])
  } finally {
    net.restore()
  }
})

// ── The same rule against Node's real fetch and two loopback servers ─────────
// The fake network above cannot show what the runtime does with
// `redirect: 'manual'`; this does. Both servers live in this process.
import http from 'node:http'

function listen(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler)
    server.listen(0, '127.0.0.1', () => resolve({
      url: `http://127.0.0.1:${server.address().port}`,
      close: () => new Promise((done) => { server.closeAllConnections?.(); server.close(done) }),
    }))
  })
}

test('real fetch: a 307 to another port is refused and the other server receives nothing', async () => {
  const received = []
  const other = await listen((req, res) => {
    received.push({ url: req.url, apiKey: req.headers['x-api-key'] })
    req.resume()
    res.writeHead(201, { 'Content-Type': 'application/json' }).end(JSON.stringify(SESSION))
  })
  const home = await listen((req, res) => {
    req.resume()
    res.writeHead(307, { Location: `${other.url}${req.url}` }).end()
  })
  try {
    const error = await rejection(reporter(home.url).startSession({ buildNumber: 'b-1' }))
    assert.ok(error, 'a redirect to another port must reject')
    assert.ok(error.message.includes(new URL(other.url).host), error.message)
    assert.match(error.message, /final URL/)
    assert.deepEqual(received, [], 'the other server received the request and the API key')
  } finally {
    await home.close()
    await other.close()
  }
})

test('real fetch: a same-origin 307 is followed with the API key and the body', async () => {
  const seen = []
  const home = await listen((req, res) => {
    let raw = ''
    req.on('data', (chunk) => { raw += chunk })
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, apiKey: req.headers['x-api-key'], raw })
      if (req.url === '/api/v1/stream/sessions') return res.writeHead(307, { Location: '/api/v1/stream/sessions/' }).end()
      if (req.url === '/api/v1/stream/sessions/') {
        return res.writeHead(201, { 'Content-Type': 'application/json' }).end(JSON.stringify(SESSION))
      }
      return res.writeHead(204).end()
    })
  })
  try {
    const session = await reporter(home.url).startSession({ buildNumber: 'b-1' })
    await session.close()
    assert.equal(session.sessionId, 's-1')
    assert.equal(seen[1].method, 'POST')
    assert.equal(seen[1].apiKey, FAKE_KEY)
    assert.equal(JSON.parse(seen[1].raw).build_number, 'b-1')
  } finally {
    await home.close()
  }
})
