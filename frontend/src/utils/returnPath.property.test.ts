/**
 * Property-based (fuzz) tests for the post-sign-in return path.
 *
 * returnPath.test.ts pins the cases someone thought of; these throw thousands
 * of generated inputs at both entry points and check the one rule that matters
 * for security: whatever comes in, the result is the default or a path that
 * stays on this origin. The SSO callback's `next` round-trips through the
 * identity provider (attacker-reachable), and an off-origin result would be an
 * open redirect.
 *
 * fast-check is also what OpenSSF Scorecard's Fuzzing check looks for in a
 * TypeScript project.
 */
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import { DEFAULT_RETURN_PATH, returnPathFrom, returnPathFromString } from './returnPath'

const ORIGIN = 'https://app.invalid'
const RUNS = { numRuns: 2000 }

/** Characters that change how a URL parser reads a path. */
const TRICKY = fc.constantFrom(
  '/', '\\', '\t', '\n', '\r', ' ', '\u0000', '\u00a0', '\u3000', '\u2028', '\ufeff',
  ':', '.', '@', '?', '#', '%', '%2f', '%5c', '%09', 'evil.example', 'javascript:', 'http:', 'https:',
  'reset-password', 'a',
)

/** Mostly-tricky strings (often app-path shaped), plus arbitrary Unicode. */
const adversarial = fc.oneof(
  fc.array(TRICKY, { maxLength: 12 }).map((parts) => parts.join('')),
  fc.array(TRICKY, { maxLength: 4 }).map((parts) => `/${parts.join('')}`),
  fc.string({ unit: 'binary', maxLength: 40 }),
  fc.webUrl().map((url) => url.replace(/^https?:/, '')),
)

/**
 * The result is the default, or an absolute path that a URL parser resolves on
 * this origin. (A backslash in the query or fragment is harmless; one in the
 * path, or a tab the parser deletes, can make `//host` — the parse catches it.)
 */
function staysOnOrigin(path: string): boolean {
  if (path === DEFAULT_RETURN_PATH) return true
  if (!path.startsWith('/')) return false
  try {
    return new URL(path, ORIGIN).origin === ORIGIN
  } catch {
    return false
  }
}

describe('returnPathFromString (property)', () => {
  it('never returns an off-origin path, whatever `next` holds', () => {
    fc.assert(
      fc.property(fc.option(adversarial, { nil: undefined }), (raw) => {
        expect(staysOnOrigin(returnPathFromString(raw))).toBe(true)
      }),
      RUNS,
    )
  })

  it('never throws on arbitrary Unicode', () => {
    fc.assert(
      fc.property(fc.string({ unit: 'binary' }), (raw) => {
        expect(typeof returnPathFromString(raw)).toBe('string')
      }),
      RUNS,
    )
  })
})

describe('returnPathFrom (property)', () => {
  const field = fc.oneof(adversarial, fc.constant(undefined), fc.integer(), fc.constant(null))

  it('never returns an off-origin path, whatever the saved location holds', () => {
    fc.assert(
      fc.property(fc.record({ pathname: field, search: field, hash: field }), (from) => {
        expect(staysOnOrigin(returnPathFrom(from))).toBe(true)
      }),
      RUNS,
    )
  })

  it.each(['/\t/evil.example', '/\n/evil.example', '/\r/evil.example', '/\t/'])(
    'refuses %j, which a URL parser turns into a protocol-relative path (found by this suite)',
    (pathname) => {
      expect(returnPathFrom({ pathname })).toBe(DEFAULT_RETURN_PATH)
    },
  )

  it('keeps an ordinary in-app location exactly, query and fragment included', () => {
    const segment = fc.stringMatching(/^[a-z0-9-]{1,12}$/)
    const pathname = fc
      .array(segment, { minLength: 1, maxLength: 5 })
      .map((parts) => `/${parts.join('/')}`)
      .filter((p) => p !== '/reset-password')
    const search = fc.option(fc.webQueryParameters().map((q) => `?${q}`), { nil: '' })
    const hash = fc.option(fc.webFragments().map((f) => `#${f}`), { nil: '' })

    fc.assert(
      fc.property(pathname, search, hash, (p, s, h) => {
        expect(returnPathFrom({ pathname: p, search: s, hash: h })).toBe(`${p}${s}${h}`)
      }),
      RUNS,
    )
  })
})
