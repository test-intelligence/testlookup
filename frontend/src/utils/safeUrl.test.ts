import { describe, it, expect, afterEach, vi } from 'vitest'
import { isSafeExternalUrl, safeExternalUrl } from './safeUrl'

// safeUrl is the only thing standing between a compromised backend (or a
// malicious SAML IdP metadata payload) and `window.location.href = ...` on the
// login page. It shipped with 0% branch coverage; these tests pin every branch.

const NUL = String.fromCharCode(0)
const DEL = String.fromCharCode(127)

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('isSafeExternalUrl', () => {
  describe('non-string and empty input', () => {
    it.each([
      ['undefined', undefined],
      ['null', null],
      ['a number', 42],
      ['an object', { href: 'https://example.com' }],
      ['an array', ['https://example.com']],
      ['a boolean', true],
    ])('rejects %s', (_label, value) => {
      expect(isSafeExternalUrl(value)).toBe(false)
    })

    it('rejects the empty string', () => {
      expect(isSafeExternalUrl('')).toBe(false)
    })
  })

  describe('dangerous schemes', () => {
    it.each([
      'javascript:alert(1)',
      'JavaScript:alert(1)',
      'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
      'vbscript:msgbox(1)',
      'file:///etc/passwd',
      'about:blank',
      'blob:https://example.com/uuid',
      'ftp://example.com/x',
      'mailto:a@b.com',
    ])('rejects %s', (url) => {
      expect(isSafeExternalUrl(url)).toBe(false)
    })
  })

  describe('control characters and whitespace', () => {
    // URL() tolerates these but browsers can be tricked by them, so the
    // control-character screen must run *before* parsing.
    it.each([
      ['a leading newline', '\nhttps://example.com'],
      ['an embedded newline', 'https://exa\nmple.com'],
      ['a carriage return', 'https://example.com\r'],
      ['a tab', 'https://exa\tmple.com'],
      ['a leading space', ' https://example.com'],
      ['a trailing space', 'https://example.com '],
      ['a NUL byte', `https://example.com${NUL}`],
      ['a DEL byte', `https://example.com${DEL}`],
    ])('rejects a URL with %s', (_label, url) => {
      expect(isSafeExternalUrl(url)).toBe(false)
    })

    it('rejects a scheme smuggled past a naive check with an embedded newline', () => {
      expect(isSafeExternalUrl('java\nscript:alert(1)')).toBe(false)
    })
  })

  describe('unparseable input', () => {
    it.each([
      'not-a-url',
      'https://',
      '://example.com',
      'example.com',
      '/relative/path',
      '//protocol-relative.example.com',
    ])('rejects %s', (url) => {
      expect(isSafeExternalUrl(url)).toBe(false)
    })
  })

  describe('https is always allowed', () => {
    it.each([
      'https://example.com',
      'https://example.com/sso/login?RelayState=abc',
      'https://sub.domain.example.com:8443/path#frag',
      'https://user:pass@example.com/',
    ])('accepts %s', (url) => {
      expect(isSafeExternalUrl(url)).toBe(true)
    })

    it('accepts https in production', () => {
      vi.stubEnv('DEV', false)
      expect(isSafeExternalUrl('https://idp.example.com/saml')).toBe(true)
    })
  })

  describe('http is dev-only', () => {
    it('accepts http when DEV is true (local IdPs, homelab Jira)', () => {
      vi.stubEnv('DEV', true)
      expect(isSafeExternalUrl('http://localhost:8080/sso')).toBe(true)
    })

    it('rejects http when DEV is false — the production downgrade guard', () => {
      vi.stubEnv('DEV', false)
      expect(isSafeExternalUrl('http://localhost:8080/sso')).toBe(false)
      expect(isSafeExternalUrl('http://evil.example.com')).toBe(false)
    })
  })

  it('narrows the type to string', () => {
    const value: unknown = 'https://example.com'
    if (isSafeExternalUrl(value)) {
      // Compiles only because the predicate narrows `unknown` to `string`.
      expect(value.startsWith('https://')).toBe(true)
    } else {
      throw new Error('expected the URL to be accepted')
    }
  })
})

describe('safeExternalUrl', () => {
  it('returns the URL unchanged when it is safe', () => {
    expect(safeExternalUrl('https://example.com/x')).toBe('https://example.com/x')
  })

  it('defaults to "#" for an unsafe URL', () => {
    expect(safeExternalUrl('javascript:alert(1)')).toBe('#')
  })

  it('defaults to "#" for a missing URL', () => {
    expect(safeExternalUrl(undefined)).toBe('#')
    expect(safeExternalUrl(null)).toBe('#')
  })

  it('honours an explicit fallback', () => {
    expect(safeExternalUrl('javascript:alert(1)', '/defects')).toBe('/defects')
    expect(safeExternalUrl(undefined, '')).toBe('')
  })

  it('does not fall back for a safe URL even when a fallback is given', () => {
    expect(safeExternalUrl('https://ok.example.com', '/defects')).toBe('https://ok.example.com')
  })
})
