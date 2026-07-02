import { describe, expect, it } from 'vitest'

import { extractErrorMessage, shouldToastError } from './apiErrors'

describe('shouldToastError', () => {
  it('surfaces 422 validation errors (the silent "empty page" footgun)', () => {
    // Regression: 422 used to be suppressed alongside 401/404, so a strict
    // Pydantic enum over a String column failed with no toast and a blank page.
    expect(shouldToastError(422)).toBe(true)
  })

  it('stays quiet for 401 (handled by the refresh flow) and 404 (expected no-data)', () => {
    expect(shouldToastError(401)).toBe(false)
    expect(shouldToastError(404)).toBe(false)
  })

  it('surfaces other error statuses and network errors (undefined status)', () => {
    expect(shouldToastError(500)).toBe(true)
    expect(shouldToastError(400)).toBe(true)
    expect(shouldToastError(undefined)).toBe(true)
  })
})

describe('extractErrorMessage', () => {
  it('uses a plain string detail as-is', () => {
    expect(extractErrorMessage('Project not found', 'fallback')).toBe('Project not found')
  })

  it('summarizes a FastAPI 422 array detail with field paths', () => {
    const detail = [
      { loc: ['body', 'status'], msg: 'value is not a valid enumeration member', type: 'enum' },
      { loc: ['body', 'days'], msg: 'ensure this value is greater than 0', type: 'value_error' },
    ]
    const out = extractErrorMessage(detail, 'fallback')
    expect(out).toContain('Validation error:')
    // Field path (minus the 'body' prefix) + message are both surfaced.
    expect(out).toContain('status — value is not a valid enumeration member')
    expect(out).toContain('days — ensure this value is greater than 0')
    // Never the raw "[object Object]".
    expect(out).not.toContain('[object Object]')
  })

  it('handles a 422 item without a loc', () => {
    expect(extractErrorMessage([{ msg: 'bad request' }], 'fallback')).toBe(
      'Validation error: bad request',
    )
  })

  it('falls back when detail is empty, missing, or unusable', () => {
    expect(extractErrorMessage(undefined, 'Request failed')).toBe('Request failed')
    expect(extractErrorMessage('', 'Request failed')).toBe('Request failed')
    expect(extractErrorMessage([], 'Request failed')).toBe('Request failed')
    expect(extractErrorMessage([{ type: 'x' }], 'Request failed')).toBe('Request failed')
  })

  it('uses the generic default when even the fallback is empty', () => {
    expect(extractErrorMessage(undefined, '')).toBe('Request failed')
  })
})
