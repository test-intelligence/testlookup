/**
 * The vocabulary the pages use to tell "we could not look" from "there is
 * nothing there". The distinction these cases pin: an absent HTTP response is
 * NOT a fact about the user's data, and must never be described as one.
 */
import { describe, expect, it } from 'vitest'

import { describeLoadError } from './loadError'

describe('describeLoadError', () => {
  it('treats a response-less failure as offline, and says so explicitly', () => {
    const info = describeLoadError(Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' }))
    expect(info.kind).toBe('offline')
    expect(info.status).toBeUndefined()
    expect(info.retryable).toBe(true)
    // The sentence that keeps a page from being read as a data claim.
    expect(info.message).toMatch(/nothing below is a statement about your test results/i)
  })

  it('does not blame the user for a 5xx', () => {
    const info = describeLoadError({ response: { status: 503, data: {} } })
    expect(info.kind).toBe('server')
    expect(info.status).toBe(503)
    expect(info.retryable).toBe(true)
    expect(info.message).toMatch(/not a gap in your data/i)
  })

  it('reports 401/403 as an access problem that retrying will not fix', () => {
    for (const status of [401, 403]) {
      const info = describeLoadError({ response: { status, data: {} } })
      expect(info.kind).toBe('unauthorized')
      expect(info.retryable).toBe(false)
    }
  })

  it('reports 404 as gone, and does not offer a retry', () => {
    const info = describeLoadError({ response: { status: 404, data: {} } })
    expect(info.kind).toBe('not_found')
    expect(info.retryable).toBe(false)
  })

  it('prefers the backend detail string when it sent one', () => {
    const info = describeLoadError({
      response: { status: 500, data: { detail: 'aggregate query timed out' } },
    })
    expect(info.message).toBe('aggregate query timed out')
  })

  it('ignores a non-string detail rather than rendering [object Object]', () => {
    const info = describeLoadError({ response: { status: 500, data: { detail: { msg: 'x' } } } })
    expect(info.message).toMatch(/returned 500/)
    expect(info.message).not.toMatch(/object Object/)
  })

  it('handles a thrown non-error without inventing a status', () => {
    const info = describeLoadError(undefined)
    expect(info.kind).toBe('offline')
    expect(info.status).toBeUndefined()
  })
})
