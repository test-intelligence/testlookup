/**
 * The return path after sign-in is an app path or the default -- never an open
 * redirect. `returnPathFromString` applies the rule to the SSO callback's
 * `next`, which round-tripped through the identity provider.
 */
import { describe, expect, it } from 'vitest'

import { DEFAULT_RETURN_PATH, returnPathFrom, returnPathFromString } from './returnPath'

describe('returnPathFromString', () => {
  it.each([
    ['/runs/r1', '/runs/r1'],
    ['/runs/r1?tab=analysis#cluster-2', '/runs/r1?tab=analysis#cluster-2'],
    ['/failures?days=7&suite=Checkout', '/failures?days=7&suite=Checkout'],
  ])('keeps the app path %s', (raw, expected) => {
    expect(returnPathFromString(raw)).toBe(expected)
  })

  it.each([
    'https://evil.example/runs',
    '//evil.example/runs',
    '/\\evil.example/runs',
    'javascript:alert(1)',
    'runs/r1',
    '',
    null,
    undefined,
    '/reset-password',
  ])('falls back to the default for %s', (raw) => {
    expect(returnPathFromString(raw as string | null | undefined)).toBe(DEFAULT_RETURN_PATH)
  })

  it('agrees with the router-state rule for the same path', () => {
    expect(returnPathFromString('/suites/s1?x=1')).toBe(returnPathFrom({ pathname: '/suites/s1', search: '?x=1' }))
  })
})
