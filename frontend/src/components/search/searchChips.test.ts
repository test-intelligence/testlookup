import { describe, expect, it } from 'vitest'
import { removeFilter } from './searchChips'

// W12 SRCH-04: a chip's × cuts its token out by the server's offsets (into the
// stripped query) and joins the rest with one space.
describe('removeFilter', () => {
  const q = 'status:failed suite:"API Regression" checkout since:7d'

  it('removes a filter by its offsets, whitespace collapsed at the seam', () => {
    expect(removeFilter(q, { start: 46, end: 54, raw: 'since:7d' })).toBe('status:failed suite:"API Regression" checkout')
    expect(removeFilter(q, { start: 0, end: 13, raw: 'status:failed' })).toBe('suite:"API Regression" checkout since:7d')
    expect(removeFilter('checkout   status:failed    login', { start: 11, end: 24, raw: 'status:failed' })).toBe('checkout login')
  })

  it('removes a quoted value whole and keeps the other quoted values as typed', () => {
    expect(removeFilter(q, { start: 14, end: 36, raw: 'suite:"API Regression"' })).toBe('status:failed checkout since:7d')
    expect(removeFilter('suite:"A  B" env:"x y"', { start: 13, end: 22, raw: 'env:"x y"' })).toBe('suite:"A  B"')
  })

  it('reads offsets into the STRIPPED query', () => {
    expect(removeFilter('   status:failed checkout  ', { start: 0, end: 13, raw: 'status:failed' })).toBe('checkout')
  })

  it('falls back to the token when the offsets do not point at it (the box was edited)', () => {
    expect(removeFilter('login status:failed', { start: 0, end: 13, raw: 'status:failed' })).toBe('login')
    expect(removeFilter('login', { start: 0, end: 13, raw: 'status:failed' })).toBe('login')
    expect(removeFilter('status:failed', { start: -1, end: 99, raw: 'status:failed' })).toBe('')
  })
})
