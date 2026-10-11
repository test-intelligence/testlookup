/**
 * The setup file's network guard (src/test/networkGuard.ts), as every test meets
 * it: nothing here installs it.
 *
 * FailureAnalysisPage's unmocked Jira probe made a real request to jsdom's origin;
 * a container answering 401 on localhost:3000 logged the test user out mid-test
 * on one machine while CI stayed green (2026-10-10). A request a test makes is
 * now refused before it leaves the process, and recorded against the test.
 */
import { AxiosError } from 'axios'
import { describe, expect, it, vi } from 'vitest'

import { api } from '@/services/api'
import { assertNoNetworkRequests, describeNetworkViolations, drainNetworkViolations } from './networkGuard'

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const THIS_TEST = () => expect.getState().currentTestName ?? ''

// node:http through a variable specifier, as in readSourceFile.ts: `tsc` checks
// src/ with no node types, and this runs in vitest's Node process only.
interface LoopbackServer {
  listen(port: number, host: string, ready: () => void): void
  address(): { port: number }
  close(done: () => void): void
  on(event: 'upgrade', listener: (req: { url?: string }, socket: { destroy(): void }) => void): void
}
interface NodeHttp {
  createServer(handler: (req: { method?: string; url?: string }, res: { end(body: string): void }) => void): LoopbackServer
}

/** A real server on a loopback port that records every request reaching it. */
async function listening() {
  const specifier = 'node:http'
  const { createServer } = (await import(/* @vite-ignore */ specifier)) as NodeHttp
  const hits: string[] = []
  const server = createServer((req, res) => {
    hits.push(`${req.method} ${req.url}`)
    res.end('ok')
  })
  server.on('upgrade', (req, socket) => {
    hits.push(`UPGRADE ${req.url}`)
    socket.destroy()
  })
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done))
  const origin = `127.0.0.1:${server.address().port}`
  const close = () => new Promise<void>((done) => server.close(() => done()))
  return { hits, origin, close }
}

describe('the network guard', () => {
  it('refuses a request through the real API client and records it against the test', async () => {
    const failure = await api.get('/api/v1/runs', { params: { limit: 1 } }).catch((e: unknown) => e)

    expect(failure).toBeInstanceOf(AxiosError)
    expect((failure as AxiosError).message).toBe('Network Error')
    expect((failure as AxiosError).response).toBeUndefined()
    expect(drainNetworkViolations()).toEqual([
      { method: 'GET', url: 'http://localhost:3000/api/v1/runs?limit=1', test: THIS_TEST() },
    ])
  })

  it('refuses fetch to any http(s) origin, and lets a data: URL through', async () => {
    await expect(fetch('https://example.com/x', { method: 'POST' })).rejects.toThrow(/do not reach the network/)
    expect(drainNetworkViolations()).toEqual([{ method: 'POST', url: 'https://example.com/x', test: THIS_TEST() }])

    const local = await fetch('data:text/plain,hello')
    expect(await local.text()).toBe('hello')
    expect(drainNetworkViolations()).toEqual([])
  })

  it('refuses a WebSocket: it errors and closes, as an unreachable server would', async () => {
    const socket = new WebSocket('ws://localhost:3000/api/v1/stream/ws')
    const closed = new Promise<CloseEvent>((done) => socket.addEventListener('close', (e) => done(e as CloseEvent)))
    const errored = vi.fn()
    socket.onerror = errored

    const close = await closed
    expect(errored).toHaveBeenCalledTimes(1)
    expect(close.code).toBe(1006)
    expect(socket.readyState).toBe(WebSocket.CLOSED)
    expect(drainNetworkViolations()).toEqual([
      { method: 'GET', url: 'ws://localhost:3000/api/v1/stream/ws', test: THIS_TEST() },
    ])
  })

  it('fails the check with the method, the URL and the test that made the request', async () => {
    await fetch('http://localhost:3000/health/details').catch(() => undefined)

    expect(() => assertNoNetworkRequests()).toThrow(
      describeNetworkViolations([{ method: 'GET', url: 'http://localhost:3000/health/details', test: THIS_TEST() }]),
    )
    // The check took what it reported: the next one is clean.
    expect(() => assertNoNetworkRequests()).not.toThrow()
  })

  it('nothing reaches a server that IS listening: not through axios, fetch or a WebSocket', async () => {
    const { hits, origin, close } = await listening()
    try {
      await api.get(`http://${origin}/via-axios`).catch(() => undefined)
      await fetch(`http://${origin}/via-fetch`).catch(() => undefined)
      const socket = new WebSocket(`ws://${origin}/via-ws`)
      await new Promise((done) => socket.addEventListener('close', done))

      expect(hits).toEqual([])
      expect(drainNetworkViolations().map((v) => v.url)).toEqual([
        `http://${origin}/via-axios`,
        `http://${origin}/via-fetch`,
        `ws://${origin}/via-ws`,
      ])
    } finally {
      await close()
    }
  })

  // The body passes; only the setup file's afterEach check can fail it.
  it.fails('a test that leaves a request behind fails, from the setup file', async () => {
    await fetch('http://localhost:3000/api/v1/projects').catch(() => undefined)
  })
})
