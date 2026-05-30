import { describe, it, expect } from 'vitest'
import { REFRESH_INTERVALS } from './refreshIntervals'

describe('REFRESH_INTERVALS', () => {
  it('has all four tiers', () => {
    expect(REFRESH_INTERVALS).toHaveProperty('REALTIME')
    expect(REFRESH_INTERVALS).toHaveProperty('ACTIVE')
    expect(REFRESH_INTERVALS).toHaveProperty('POLLING')
    expect(REFRESH_INTERVALS).toHaveProperty('BACKGROUND')
  })

  it('tiers are ordered from fastest to slowest', () => {
    expect(REFRESH_INTERVALS.REALTIME).toBeLessThan(REFRESH_INTERVALS.ACTIVE)
    expect(REFRESH_INTERVALS.ACTIVE).toBeLessThan(REFRESH_INTERVALS.POLLING)
    expect(REFRESH_INTERVALS.POLLING).toBeLessThanOrEqual(REFRESH_INTERVALS.BACKGROUND)
  })

  it('all intervals are positive numbers', () => {
    for (const value of Object.values(REFRESH_INTERVALS)) {
      expect(value).toBeGreaterThan(0)
    }
  })

  it('REALTIME is 5 seconds', () => {
    expect(REFRESH_INTERVALS.REALTIME).toBe(5_000)
  })

  it('BACKGROUND is 60 seconds', () => {
    expect(REFRESH_INTERVALS.BACKGROUND).toBe(60_000)
  })
})
