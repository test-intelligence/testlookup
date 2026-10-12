// A fake TestLookup server behind globalThis.fetch, recording what the SDK sent.

function response(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => null },
    json: async () => body,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  }
}

export function installFakeServer() {
  const calls = []
  const original = globalThis.fetch
  globalThis.fetch = async (url, init = {}) => {
    const { pathname } = new URL(url)
    const method = init.method ?? 'GET'
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ method, path: pathname, headers: { ...(init.headers ?? {}) }, body })
    if (method === 'POST' && pathname === '/api/v1/stream/sessions') {
      return response(201, {
        session_id: 's-1', session_token: 'st-1', run_id: 'r-1',
        project_id: 'p', expires_in: 3600, created_at: '',
      })
    }
    if (method === 'POST' && pathname === '/api/v1/stream/events/batch') {
      return response(202, { accepted: body.events.length, run_id: 'r-1' })
    }
    if (method === 'DELETE') return response(204)
    return response(404, {})
  }
  return {
    calls,
    sessionCreate: () => calls.find((c) => c.method === 'POST' && c.path === '/api/v1/stream/sessions'),
    results: () => calls
      .filter((c) => c.path === '/api/v1/stream/events/batch')
      .flatMap((c) => c.body.events)
      .filter((e) => e.event_type === 'test_result')
      .map((e) => `${e.test_name}:${e.status}`),
    restore: () => { globalThis.fetch = original },
  }
}

/** Remove every TESTLOOKUP_* variable so the developer's shell cannot leak in. */
export function clearTestLookupEnv() {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith('TESTLOOKUP_')) delete process.env[key]
  }
}
