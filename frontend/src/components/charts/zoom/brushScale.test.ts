import { describe, expect, it } from 'vitest'
import { dayAtFraction, dayCentre, edgeAtFraction, edgePosition, tickDays } from './brushScale'

describe('brushScale — the chart\'s own day placement', () => {
  it('band: each day is a slot, centred; the first half a slot in from the edge', () => {
    expect(dayCentre('band', 0, 4)).toBe(0.125)
    expect(dayCentre('band', 3, 4)).toBe(0.875)
    expect([0, 1, 2, 3, 4].map((k) => edgePosition('band', k, 4))).toEqual([0, 0.25, 0.5, 0.75, 1])
  })

  it('point: the first and last days sit ON the edges; inner edges fall halfway between days', () => {
    expect(dayCentre('point', 0, 5)).toBe(0)
    expect(dayCentre('point', 4, 5)).toBe(1)
    expect(dayCentre('point', 2, 5)).toBe(0.5)
    // The two end days own half a slot each.
    expect([0, 1, 2, 3, 4, 5].map((k) => edgePosition('point', k, 5))).toEqual([0, 0.125, 0.375, 0.625, 0.875, 1])
  })

  it('the day under a fraction is the day whose slot holds it, on either scale', () => {
    expect(dayAtFraction('band', 0, 4)).toBe(0)
    expect(dayAtFraction('band', 0.26, 4)).toBe(1)
    expect(dayAtFraction('band', 1, 4)).toBe(3)
    expect(dayAtFraction('point', 0.1, 5)).toBe(0)
    expect(dayAtFraction('point', 0.13, 5)).toBe(1)
    expect(dayAtFraction('point', 1, 5)).toBe(4)
    // Every centre maps back to its own day.
    for (const scale of ['band', 'point'] as const) {
      for (let i = 0; i < 9; i++) expect(dayAtFraction(scale, dayCentre(scale, i, 9), 9), `${scale} ${i}`).toBe(i)
    }
  })

  it('the edge nearest a fraction is the nearest edge, including the uneven point-scale ends', () => {
    expect(edgeAtFraction('band', 0.49, 4)).toBe(2)
    expect(edgeAtFraction('point', 0, 5)).toBe(0)
    expect(edgeAtFraction('point', 0.06, 5)).toBe(0)
    expect(edgeAtFraction('point', 0.07, 5)).toBe(1)
    expect(edgeAtFraction('point', 0.95, 5)).toBe(5)
    for (const scale of ['band', 'point'] as const) {
      for (let k = 0; k <= 9; k++) expect(edgeAtFraction(scale, edgePosition(scale, k, 9), 9), `${scale} ${k}`).toBe(k)
    }
  })

  it('ticks every day while they have room, then every week counted back from the last day; both ends always', () => {
    expect(tickDays(6, 500)).toEqual([0, 1, 2, 3, 4, 5])
    const weekly = tickDays(90, 300)
    expect(weekly[0]).toBe(0)
    expect(weekly[weekly.length - 1]).toBe(89)
    expect(weekly.slice(1).every((day) => (89 - day) % 7 === 0)).toBe(true)
    // None crowds the first day's tick.
    expect(weekly[1]).toBeGreaterThanOrEqual(4)
    expect(tickDays(0, 300)).toEqual([])
  })
})
