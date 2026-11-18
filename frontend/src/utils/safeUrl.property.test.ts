/**
 * Property-based (fuzz) tests for the external-URL guard.
 *
 * safeUrl.test.ts pins every branch with hand-picked cases; these generate
 * thousands of inputs (arbitrary values, arbitrary Unicode, URL-shaped strings
 * with dangerous schemes, case tricks and control characters) and check the
 * contract the login page and every outbound link rely on: an accepted value
 * is a string that parses as http(s) — https only in a production build — and
 * carries no whitespace or control character. Nothing else ever gets through,
 * and nothing makes the guard throw.
 *
 * fast-check is also what OpenSSF Scorecard's Fuzzing check looks for in a
 * TypeScript project.
 */
import fc from 'fast-check'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { isSafeExternalUrl, safeExternalUrl } from './safeUrl'

const RUNS = { numRuns: 2000 }

afterEach(() => {
  vi.unstubAllEnvs()
})

const scheme = fc.constantFrom(
  'https:', 'http:', 'HTTPS:', 'HtTp:', 'javascript:', 'JavaScript:', 'data:', 'vbscript:', 'file:', 'blob:',
  'about:', 'ftp:', 'ws:', '', ' https:', 'java\tscript:', 'https\n:',
)
const noise = fc.constantFrom(
  '', ' ', '\t', '\n', '\r', '\u0000', '\u007f', '\u00a0', '\u2028', '\ufeff', '//', '\\\\', '%0a', '@',
)
const urlShaped = fc
  .tuple(noise, scheme, noise, fc.webAuthority(), fc.webSegment(), noise)
  .map(([a, s, b, host, path, c]) => `${a}${s}${b}//${host}/${path}${c}`)

const anyInput = fc.oneof(fc.anything(), fc.string({ unit: 'binary' }), urlShaped)

// eslint-disable-next-line no-control-regex
const WHITESPACE_OR_CONTROL = /[\s\u0000-\u001F\u007F]/

function assertAcceptedIsSafe(value: unknown, allowed: ReadonlySet<string>) {
  if (!isSafeExternalUrl(value)) return
  expect(typeof value).toBe('string')
  const url = value as string
  expect(WHITESPACE_OR_CONTROL.test(url)).toBe(false)
  expect(allowed.has(new URL(url).protocol)).toBe(true)
}

describe('isSafeExternalUrl (property)', () => {
  it('accepts only whitespace-free http(s) strings in a development build', () => {
    vi.stubEnv('DEV', true)
    fc.assert(
      fc.property(anyInput, (value) => assertAcceptedIsSafe(value, new Set(['https:', 'http:']))),
      RUNS,
    )
  })

  it('accepts only https in a production build', () => {
    vi.stubEnv('DEV', false)
    fc.assert(
      fc.property(anyInput, (value) => assertAcceptedIsSafe(value, new Set(['https:']))),
      RUNS,
    )
  })

  it('accepts every well-formed https URL', () => {
    vi.stubEnv('DEV', false)
    fc.assert(
      fc.property(fc.webUrl({ validSchemes: ['https'], withQueryParameters: true, withFragments: true }), (url) => {
        expect(isSafeExternalUrl(url)).toBe(true)
      }),
      RUNS,
    )
  })
})

describe('safeExternalUrl (property)', () => {
  it('returns the input when it is safe and the fallback otherwise', () => {
    fc.assert(
      fc.property(anyInput, fc.string(), (value, fallback) => {
        const out = safeExternalUrl(value, fallback)
        expect(out).toBe(isSafeExternalUrl(value) ? value : fallback)
      }),
      RUNS,
    )
  })
})
