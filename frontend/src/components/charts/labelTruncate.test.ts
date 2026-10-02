/**
 * `middleTruncate` at the smallest lengths, where the cut keeps a head and NO
 * tail. `slice(-0)` is the whole array, so a tail of 0 characters taken as
 * `slice(-tail)` would append the entire name after the ellipsis.
 */
import { describe, expect, it } from 'vitest'
import { ELLIPSIS, middleTruncate } from './labelTruncate'

describe('middleTruncate — no tail left to keep', () => {
  it('at 2 characters: the first character and the ellipsis, nothing after it', () => {
    expect(middleTruncate('checkout_retries_once', 2)).toBe(`c${ELLIPSIS}`)
  })

  it('at 1 character: the ellipsis alone', () => {
    expect(middleTruncate('checkout_retries_once', 1)).toBe(ELLIPSIS)
  })

  it('at 3 characters: head, ellipsis, tail — one each', () => {
    expect(middleTruncate('checkout_retries_once', 3)).toBe(`c${ELLIPSIS}e`)
  })

  it('counts code points: an astral head is kept whole', () => {
    expect(middleTruncate('😀abcdef', 2)).toBe(`😀${ELLIPSIS}`)
  })
})
