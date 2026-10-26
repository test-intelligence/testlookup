/**
 * W17b: a one-time link is shown on THIS page's origin with only the expected path and
 * its fragment token — a server-supplied origin, query token or script URL never renders.
 */
import { describe, expect, it } from 'vitest'

import { buildOneTimeLink, roleLabel } from './oneTimeLink'

const ORIGIN = window.location.origin

describe('buildOneTimeLink', () => {
  it('re-bases an absolute server link on this origin', () => {
    expect(buildOneTimeLink('https://other.host/accept-invite#token=abc_DEF-1', '/accept-invite')).toBe(
      `${ORIGIN}/accept-invite#token=abc_DEF-1`,
    )
    expect(buildOneTimeLink('/reset-password/confirm#token=x1', '/reset-password/confirm')).toBe(
      `${ORIGIN}/reset-password/confirm#token=x1`,
    )
  })

  it.each([
    ['', '/accept-invite'],
    ['javascript:alert(1)#token=a', '/accept-invite'],
    ['https://evil.example/phish#token=a', '/accept-invite'],
    ['/accept-invite?token=abc', '/accept-invite'],
    ['/accept-invite#token=a"><img', '/accept-invite'],
    ['/reset-password/confirm#token=a', '/accept-invite'],
  ] as const)('refuses %s', (raw, path) => {
    expect(buildOneTimeLink(raw, path)).toBe('')
  })
})

describe('roleLabel', () => {
  it('reads like a person wrote it', () => {
    expect(roleLabel('QA_ENGINEER')).toBe('QA Engineer')
    expect(roleLabel('ADMIN')).toBe('Admin')
    expect(roleLabel('QA_LEAD')).toBe('QA Lead')
  })
})
