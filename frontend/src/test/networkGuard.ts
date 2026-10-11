/**
 * Unit tests never reach the network.
 *
 * jsdom's origin is http://localhost:3000 and the API client's base URL is
 * relative, so an unmocked hook makes a REAL request there. Where nothing
 * listens it fails quietly; where something does, the test meets a stranger's
 * answer. A container published on :3000 answered 401, the client's
 * refresh-then-logout path cleared the signed-in user mid-test, and
 * FailureAnalysisPage failed 1-2 tests per run on that machine only, while CI
 * stayed green (2026-10-10).
 *
 * The guard blocks every http(s) and ws(s) request made through
 * XMLHttpRequest (axios), fetch or WebSocket. The request fails at once, the way
 * an unreachable server fails, and the test that made it fails in `afterEach`,
 * naming the method, the URL and the test. Mock the hook or service instead.
 * data: and blob: URLs never leave the process and pass through.
 */
import { expect } from 'vitest'

export interface NetworkViolation {
  method: string
  url: string
  test: string
}

const violations: NetworkViolation[] = []

function currentTest(): string {
  try {
    return expect.getState().currentTestName ?? '(outside a test)'
  } catch {
    return '(outside a test)'
  }
}

function resolve(url: string | URL): string {
  try {
    // A node-environment file has no window: only absolute URLs resolve there.
    return new URL(String(url), typeof window === 'undefined' ? undefined : window.location.href).href
  } catch {
    return String(url)
  }
}

function leavesTheProcess(href: string): boolean {
  return /^(https?|wss?):/i.test(href)
}

function record(method: string, href: string): void {
  violations.push({ method: method.toUpperCase(), url: href, test: currentTest() })
}

/** Take (and clear) the requests blocked since the last call. */
export function drainNetworkViolations(): NetworkViolation[] {
  return violations.splice(0, violations.length)
}

/** The failure a test gets for the requests it made. */
export function describeNetworkViolations(found: NetworkViolation[]): string {
  const lines = found.map((v) => `  ${v.method} ${v.url}  (during: ${v.test})`)
  return [
    `Unit tests must not reach the network; blocked ${found.length} request(s):`,
    ...lines,
    'Mock the hook or service that makes it (vi.mock), or stub fetch / the axios adapter for this test.',
  ].join('\n')
}

/** Fail with every request blocked since the last check. */
export function assertNoNetworkRequests(): void {
  const found = drainNetworkViolations()
  if (found.length > 0) throw new Error(describeNetworkViolations(found))
}

/** A WebSocket that never connects: it reports an error and closes, as an unreachable server would. */
class BlockedWebSocket extends EventTarget {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  readonly CONNECTING = 0
  readonly OPEN = 1
  readonly CLOSING = 2
  readonly CLOSED = 3
  readonly url: string
  readonly protocol = ''
  readonly extensions = ''
  readonly bufferedAmount = 0
  binaryType: BinaryType = 'blob'
  readyState = 0
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(url: string | URL) {
    super()
    this.url = resolve(url)
    record('GET', this.url)
    queueMicrotask(() => {
      this.readyState = 3
      const error = new Event('error')
      this.onerror?.(error)
      this.dispatchEvent(error)
      const close = typeof CloseEvent === 'undefined'
        ? new Event('close') as CloseEvent
        : new CloseEvent('close', { code: 1006, wasClean: false })
      this.onclose?.(close)
      this.dispatchEvent(close)
    })
  }

  send(): void {}
  close(): void {
    this.readyState = 3
  }
}

let installed = false

export function installNetworkGuard(): void {
  if (installed) return
  installed = true

  // jsdom files have XMLHttpRequest (axios's adapter there); node-environment files only fetch.
  if (typeof XMLHttpRequest !== 'undefined') guardXhr()
  if (typeof globalThis.fetch === 'function') guardFetch()
  if (typeof globalThis.WebSocket !== 'undefined') {
    globalThis.WebSocket = BlockedWebSocket as unknown as typeof WebSocket
    if (typeof window !== 'undefined') window.WebSocket = globalThis.WebSocket
  }
}

function guardXhr(): void {
  const target = new WeakMap<XMLHttpRequest, { method: string; href: string }>()
  const proto = XMLHttpRequest.prototype
  const realOpen = proto.open as (this: XMLHttpRequest, ...args: unknown[]) => void
  const realSend = proto.send

  proto.open = function open(this: XMLHttpRequest, method: string, url: string | URL, ...rest: unknown[]) {
    const href = resolve(url)
    if (leavesTheProcess(href)) target.set(this, { method, href })
    else target.delete(this)
    return realOpen.call(this, method, url, ...rest)
  } as typeof proto.open

  proto.send = function send(this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null) {
    const blocked = target.get(this)
    if (!blocked) return realSend.call(this, body)
    record(blocked.method, blocked.href)
    // An unreachable server's answer: error, then loadend (axios rejects with "Network Error").
    queueMicrotask(() => {
      this.dispatchEvent(new ProgressEvent('error'))
      this.dispatchEvent(new ProgressEvent('loadend'))
    })
  }
}

function guardFetch(): void {
  const realFetch = globalThis.fetch
  globalThis.fetch = function fetch(input: RequestInfo | URL, init?: RequestInit) {
    const href = resolve(input instanceof Request ? input.url : input)
    if (!leavesTheProcess(href)) return realFetch(input, init)
    record(init?.method ?? (input instanceof Request ? input.method : 'GET'), href)
    return Promise.reject(new TypeError(`Failed to fetch ${href}: unit tests do not reach the network`))
  } as typeof globalThis.fetch
}
