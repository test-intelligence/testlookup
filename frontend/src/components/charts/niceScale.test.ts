/**
 * Nice axis scales. The first Linux baselines showed value axes ending at the
 * data maximum ("0 15 30 41") and a zoomed rate axis ticked at 90, 93, 96,
 * 100; these pin the rule both now follow.
 */
import { describe, expect, it } from 'vitest'
import {
  alignedZeroBasedScale,
  niceScale,
  niceStep,
  symmetricScale,
  tickDecimals,
  zeroBasedScale,
} from './niceScale'

/** 1, 2, 2.5 or 5 times a power of ten (1.5, 3, 4, 6, 8 too for `fine`). */
function onLadder(step: number, fine = false): boolean {
  const mantissa = step / 10 ** Math.floor(Math.log10(step))
  const ladder = fine ? [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10] : [1, 2, 2.5, 5, 10]
  return ladder.some((nice) => Math.abs(mantissa - nice) < 1e-9)
}

/** Evenly spaced, first = domain start, last = domain end. */
function expectEven(scale: { domain: [number, number]; ticks: number[]; step: number }) {
  const { domain, ticks, step } = scale
  expect(ticks[0]).toBe(domain[0])
  expect(ticks[ticks.length - 1]).toBe(domain[1])
  for (let i = 1; i < ticks.length; i++) expect(ticks[i] - ticks[i - 1]).toBeCloseTo(step, 9)
}

describe('niceStep', () => {
  it('rounds UP to the 1-2-2.5-5 ladder', () => {
    expect(niceStep(8.2)).toBe(10)
    expect(niceStep(2.4)).toBe(2.5)
    expect(niceStep(2)).toBe(2)
    expect(niceStep(0.3)).toBe(0.5)
    expect(niceStep(44)).toBe(50)
  })

  it('takes whole-number steps only, for counts', () => {
    expect(niceStep(0.3, { integer: true })).toBe(1)
    expect(niceStep(2.2, { integer: true })).toBe(5)
    expect(niceStep(22, { integer: true })).toBe(25)
  })
})

describe('tickDecimals', () => {
  it('gives the places a step needs to print exactly', () => {
    expect(tickDecimals(2.5)).toBe(1)
    expect(tickDecimals(0.25)).toBe(2)
    expect(tickDecimals(50)).toBe(0)
  })
})

describe('zeroBasedScale — a bar value axis', () => {
  it.each([
    [41, 50],
    [31, 40],
    [46, 50],
    [220, 250],
    [180, 200],
    [300, 300],
    [9, 10],
    [80, 80],
    [100, 100],
  ])('ends %s on a nice %s, never at the data maximum', (max, end) => {
    const scale = zeroBasedScale(max, { integer: true })
    expect(scale.domain).toEqual([0, end])
    expect(scale.domain[1]).toBeGreaterThanOrEqual(max)
    expect(onLadder(scale.step)).toBe(true)
    expectEven(scale)
  })

  it('draws an empty or all-zero set on [0, 1]', () => {
    expect(zeroBasedScale(0).domain).toEqual([0, 1])
    expect(zeroBasedScale(Number.NaN).domain).toEqual([0, 1])
  })

  it('never returns a fractional tick for whole-number data', () => {
    for (const max of [1, 2, 3, 7, 13]) {
      for (const tick of zeroBasedScale(max, { integer: true }).ticks) expect(Number.isInteger(tick), `${max}`).toBe(true)
    }
  })
})

describe('symmetricScale — a change axis', () => {
  it('stays symmetric about zero, with zero a tick', () => {
    for (const extreme of [3, 8, 9, 12, 37]) {
      const scale = symmetricScale(extreme, { integer: true })
      expect(scale.domain[0]).toBe(-scale.domain[1])
      expect(scale.domain[1]).toBeGreaterThanOrEqual(extreme)
      expect(scale.ticks).toContain(0)
      expectEven(scale)
    }
    expect(symmetricScale(12, { integer: true }).ticks).toEqual([-15, -10, -5, 0, 5, 10, 15])
  })
})

describe('niceScale — a zoomed rate axis', () => {
  it('ticks 90-100 at 2.5, not at 90, 93, 96, 100', () => {
    const scale = niceScale(90, 100, { intervals: 4 })
    expect(scale.ticks).toEqual([90, 92.5, 95, 97.5, 100])
    // Every label printed at the step's precision IS its tick's value.
    for (const tick of scale.ticks) expect(Number(tick.toFixed(tickDecimals(scale.step)))).toBe(tick)
  })

  it('widens the domain OUTWARD to the step, so no value falls off the axis', () => {
    const scale = niceScale(85, 100, { intervals: 4 })
    expect(scale.domain[0]).toBeLessThanOrEqual(85)
    expect(scale.domain[1]).toBeGreaterThanOrEqual(100)
    expectEven(scale)
  })

  it('keeps 0-100 at 25', () => {
    expect(niceScale(0, 100, { intervals: 4 }).ticks).toEqual([0, 25, 50, 75, 100])
  })
})

describe('alignedZeroBasedScale — a secondary axis on the primary grid', () => {
  it('uses exactly the interval count it is given, and reaches the largest value', () => {
    for (const [max, intervals] of [
      [200, 4],
      [45, 4],
      [7, 4],
      [1234, 3],
    ] as const) {
      const scale = alignedZeroBasedScale(max, intervals, { integer: true })
      expect(scale.ticks.length - 1).toBe(intervals)
      expect(scale.domain[1]).toBeGreaterThanOrEqual(max)
      expect(onLadder(scale.step, true)).toBe(true)
      expectEven(scale)
    }
    expect(alignedZeroBasedScale(200, 4, { integer: true }).domain).toEqual([0, 200])
    expect(alignedZeroBasedScale(45, 4, { integer: true }).domain).toEqual([0, 60])
  })
})
