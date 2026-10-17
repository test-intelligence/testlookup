/**
 * W27 PAN-01: the id shapes a detail key in the URL is checked against before
 * any request. A value that fails never reaches a request path.
 */
import { describe, expect, it } from 'vitest'
import { DETAIL_TOKEN_RE, isDetailToken, isUuid, UUID_RE } from './ids'

const UUID = '3f2b8c1e-9a4d-4e6f-8b2a-1c0d9e8f7a65'

describe('isUuid', () => {
  it.each([
    [UUID, true],
    [UUID.toUpperCase(), true],
    ['../../admin', false],
    ['a/b', false],
    ['..', false],
    ['', false],
    ['not-a-uuid', false],
    [`${UUID}/../../admin`, false],
    [` ${UUID}`, false],
    [`${UUID}0`, false],
    ['3f2b8c1e9a4d4e6f8b2a1c0d9e8f7a65', false],
  ])('%j -> %s', (value, expected) => {
    expect(isUuid(value)).toBe(expected)
  })

  it('is false for null and undefined', () => {
    expect(isUuid(null)).toBe(false)
    expect(isUuid(undefined)).toBe(false)
  })

  it('anchors the whole value', () => {
    expect(UUID_RE.source.startsWith('^')).toBe(true)
    expect(UUID_RE.source.endsWith('$')).toBe(true)
  })
})

describe('isDetailToken', () => {
  it.each([
    ['fp_3f2b:checkout-flow', true],
    ['m-2', true],
    [UUID, true],
    ['a'.repeat(128), true],
    ['a'.repeat(129), false],
    ['../../admin', false],
    ['a/b', false],
    ['..', false],
    ['.', false],
    ['', false],
    ['a?b=1', false],
    ['a#b', false],
    ['a%2F', false],
    ['a&b', false],
    ['a b', false],
  ])('%j -> %s', (value, expected) => {
    expect(isDetailToken(value)).toBe(expected)
  })

  it('is false for null and undefined', () => {
    expect(isDetailToken(null)).toBe(false)
    expect(isDetailToken(undefined)).toBe(false)
  })

  it('anchors the whole value', () => {
    expect(DETAIL_TOKEN_RE.source).toBe('^[A-Za-z0-9_:-]{1,128}$')
  })
})
