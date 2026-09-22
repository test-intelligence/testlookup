import { AxiosHeaders } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: { get } }))

const { chartGet, headerValue, requestIdOf } = await import('./chartApi')

describe('chartApi', () => {
  beforeEach(() => get.mockReset())

  it('chartGet opts out of the global toast, forwards the signal and returns the request id', async () => {
    get.mockResolvedValue({ data: { ok: 1 }, headers: new AxiosHeaders({ 'X-Request-ID': 'req-7' }) })
    const controller = new AbortController()
    const result = await chartGet('/api/v1/analytics/pass-rate', { params: { days: 7 }, signal: controller.signal })
    expect(get).toHaveBeenCalledWith('/api/v1/analytics/pass-rate', {
      params: { days: 7 },
      signal: controller.signal,
      suppressToast: true,
    })
    expect(result).toEqual({ data: { ok: 1 }, requestId: 'req-7' })
  })

  it('chartGet with no options still suppresses the toast', async () => {
    get.mockResolvedValue({ data: [], headers: {} })
    await expect(chartGet('/x')).resolves.toEqual({ data: [], requestId: null })
    expect(get.mock.calls[0][1]).toMatchObject({ suppressToast: true })
  })

  it('reads a header case-insensitively from AxiosHeaders or a plain object', () => {
    expect(headerValue(new AxiosHeaders({ 'x-request-id': 'a' }), 'X-Request-ID')).toBe('a')
    expect(headerValue({ 'X-Request-Id': ' b ' }, 'x-request-id')).toBe('b')
    expect(headerValue({ other: 'c' }, 'x-request-id')).toBeNull()
    expect(headerValue(null, 'x-request-id')).toBeNull()
    expect(headerValue({ 'x-request-id': 'x'.repeat(200) }, 'x-request-id')).toBeNull()
  })

  it('requestIdOf prefers the header, then the error body', () => {
    expect(requestIdOf({ headers: { 'x-request-id': 'h' }, data: { request_id: 'b' } })).toBe('h')
    expect(requestIdOf({ headers: {}, data: { request_id: 'b' } })).toBe('b')
    expect(requestIdOf({ headers: {}, data: 'text' })).toBeNull()
    expect(requestIdOf(undefined)).toBeNull()
  })

  it('requestIdOf accepts only a plain id token — no bidi override, control character or space', () => {
    // Built from code points so no invisible character sits in this source file.
    const RLO = String.fromCharCode(0x202e) // RIGHT-TO-LEFT OVERRIDE: 'req-<RLO>exe.gnp' reads as 'req-png.exe'
    const LRI = String.fromCharCode(0x2066)
    const PDI = String.fromCharCode(0x2069)
    const RLM = String.fromCharCode(0x200f)
    const BEL = String.fromCharCode(0x07)
    expect(requestIdOf({ headers: { 'x-request-id': 'req-1:a.b_C9' } })).toBe('req-1:a.b_C9')
    expect(requestIdOf({ headers: { 'x-request-id': 'x'.repeat(128) } })).toBe('x'.repeat(128))
    expect(requestIdOf({ headers: { 'x-request-id': `req-${RLO}exe.gnp` } })).toBeNull()
    expect(requestIdOf({ headers: { 'x-request-id': `req-${BEL}bell` } })).toBeNull()
    expect(requestIdOf({ headers: { 'x-request-id': 'two words' } })).toBeNull()
    expect(requestIdOf({ headers: {}, data: { request_id: `${LRI}isolate${PDI}` } })).toBeNull()
    // A bad header falls back to a good body id, never to the bad one.
    expect(requestIdOf({ headers: { 'x-request-id': `bad${RLM}id` }, data: { request_id: 'good-1' } })).toBe('good-1')
  })
})
