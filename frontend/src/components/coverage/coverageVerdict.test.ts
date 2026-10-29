import { describe, expect, it } from 'vitest'

import { dimensionsBelowTarget, healthyAction, healthyLede, type VerdictDimension } from './coverageVerdict'

// W23a demo: a Healthy composite (69) said "all signals nominal … all above target" over "Run cadence 0 of 1".
describe('the Healthy verdict names what is below target', () => {
  const dims = (tones: VerdictDimension['tone'][]): VerdictDimension[] =>
    ['Pass rate', 'Tag quality', 'Run cadence', 'Suite breadth'].map((label, i) => ({ label, tone: tones[i] }))

  it('every dimension at target keeps the "all above target" sentence', () => {
    const below = dimensionsBelowTarget(dims(['good', 'good', 'good', 'good']))
    expect(below).toEqual([])
    expect(healthyAction(below)).toBe('all signals nominal')
    expect(healthyLede(6, below)).toBe(
      'Coverage signal is solid across 6 suites. Pass rate, tagging, cadence, and breadth are all above target.',
    )
  })

  it('one dimension below target is named, never claimed above it', () => {
    const below = dimensionsBelowTarget(dims(['good', 'good', 'bad', 'good']))
    expect(below).toEqual(['Run cadence'])
    expect(healthyAction(below)).toBe('1 dimension below target')
    expect(healthyLede(6, below)).toBe('Coverage signal is solid across 6 suites. Run cadence is below target.')
  })

  it('a "warn" dimension counts as below target too, and several are listed in order', () => {
    const below = dimensionsBelowTarget(dims(['warn', 'good', 'bad', 'warn']))
    expect(below).toEqual(['Pass rate', 'Run cadence', 'Suite breadth'])
    expect(healthyAction(below)).toBe('3 dimensions below target')
    expect(healthyLede(1, below)).toBe(
      'Coverage signal is solid across 1 suite. Pass rate, Run cadence and Suite breadth are below target.',
    )
  })
})
