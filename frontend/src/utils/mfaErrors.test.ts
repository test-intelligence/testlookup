import { describe, expect, it } from 'vitest'
import { describeMfaError, formatRetryAfter } from './mfaErrors'
import { formatSecret, normalizeRecoveryInput, normalizeTotpInput } from './mfaFormat'

function err(status: number, detail = '', headers: Record<string, string> = {}) {
  return { response: { status, data: { detail }, headers } }
}

describe('describeMfaError', () => {
  it('reads a 429 with Retry-After as a lockout, carrying the wait', () => {
    const info = describeMfaError(err(429, 'Account temporarily locked.', { 'retry-after': '900' }))
    expect(info.kind).toBe('locked_out')
    expect(info.retryAfterSeconds).toBe(900)
  })

  it('reads a 429 without Retry-After as an IP throttle, not a lockout', () => {
    expect(describeMfaError(err(429, 'Too many login attempts.')).kind).toBe('rate_limited')
  })

  it('never blames the credentials on a 503', () => {
    const info = describeMfaError(err(503, 'MFA seed unreadable'))
    expect(info.kind).toBe('unavailable')
    expect(info.message).toMatch(/problem on our side/i)
    expect(info.message).not.toMatch(/password|wrong code|did not match/i)
  })

  it('separates a dead challenge token from a wrong code, both of which are 401', () => {
    expect(
      describeMfaError(err(401, 'Invalid or expired MFA token. Start over from the login screen.'))
        .kind,
    ).toBe('challenge_expired')
    expect(describeMfaError(err(401, 'Invalid verification code.')).kind).toBe('invalid_code')
  })

  it('distinguishes the two 409s', () => {
    expect(describeMfaError(err(409, 'MFA is already enabled.')).kind).toBe('already_enrolled')
    expect(describeMfaError(err(409, 'MFA is not enabled for this account.')).kind).toBe(
      'not_enrolled',
    )
  })

  it('passes a 403 detail through verbatim (policy forbids self-disable)', () => {
    const info = describeMfaError(err(403, 'Workspace policy requires MFA for your role.'))
    expect(info.kind).toBe('forbidden')
    expect(info.message).toBe('Workspace policy requires MFA for your role.')
  })
})

describe('formatRetryAfter', () => {
  it.each([
    [30, '30 seconds'],
    [1, '1 second'],
    [900, '15 minutes'],
    [61, '61 seconds'],
  ])('%i → %s', (seconds, expected) => {
    expect(formatRetryAfter(seconds)).toBe(expected)
  })
})

describe('code field normalizers', () => {
  it('accepts a pasted TOTP code with spaces or hyphens', () => {
    expect(normalizeTotpInput('123 456')).toBe('123456')
    expect(normalizeTotpInput('123-456')).toBe('123456')
    expect(normalizeTotpInput('  123456\n')).toBe('123456')
  })

  it('caps TOTP at six digits and drops letters', () => {
    expect(normalizeTotpInput('12345678')).toBe('123456')
    expect(normalizeTotpInput('abc123')).toBe('123')
  })

  it('uppercases recovery codes and keeps the display grouping', () => {
    expect(normalizeRecoveryInput('abcd-efgh-jklm-npqr')).toBe('ABCD-EFGH-JKLM-NPQR')
    expect(normalizeRecoveryInput('abcd efgh')).toBe('ABCDEFGH')
  })

  it('chunks the manual-entry secret into readable groups', () => {
    expect(formatSecret('JBSWY3DPEHPK3PXP')).toBe('JBSW Y3DP EHPK 3PXP')
    expect(formatSecret('ABC')).toBe('ABC')
  })
})
