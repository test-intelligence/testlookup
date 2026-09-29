import { describe, expect, it } from 'vitest'
import {
  buildGaugeBarModel,
  clamp,
  formatGaugeNumber,
  GAUGE_TONE_COLOR,
  gaugeValueText,
  layoutTickLabels,
  NOT_MEASURED,
  positionPct,
  resolveDomain,
  tickAnchor,
  toneForValue,
  type GaugeBands,
} from './gaugeBar.model'

const RISK: GaugeBands = { direction: 'lower-is-better', thresholds: [40, 70] }
const HEALTH: GaugeBands = { direction: 'higher-is-better', thresholds: [33, 70] }

describe('gaugeBar model — not measured', () => {
  it('null is not measured: no position, no segments, no target, and never 0 in words', () => {
    const m = buildGaugeBarModel({
      value: null,
      segments: [{ value: 3, label: 'P0' }],
      target: { value: 70, label: 'target' },
    })
    expect(m.measured).toBe(false)
    expect(m.value).toBeNull()
    expect(m.valueNow).toBeNull()
    expect(m.segments).toEqual([])
    expect(m.target).toBeNull()
    expect(gaugeValueText(m)).toBe(NOT_MEASURED)
    expect(gaugeValueText(m)).not.toMatch(/\b0\b/)
  })

  it('NaN is not measured either (a division by a zero count upstream)', () => {
    const m = buildGaugeBarModel({ value: Number.NaN })
    expect(m.measured).toBe(false)
    expect(m.valueNow).toBeNull()
  })

  it('a real 0 IS measured, and says 0', () => {
    const m = buildGaugeBarModel({ value: 0 })
    expect(m.measured).toBe(true)
    expect(m.valueNow).toBe(0)
    expect(gaugeValueText(m)).toBe('0 of 100')
  })
})

describe('gaugeBar model — clamping', () => {
  it('clamps the position at both ends but keeps the true value for the text', () => {
    const over = buildGaugeBarModel({ value: 130 })
    expect(over.pct).toBe(100)
    expect(over.valueNow).toBe(100)
    expect(over.value).toBe(130)
    expect(gaugeValueText(over)).toBe('130 of 100')

    const under = buildGaugeBarModel({ value: -12 })
    expect(under.pct).toBe(0)
    expect(under.valueNow).toBe(0)
    expect(under.value).toBe(-12)
  })

  it('clamps on a non-zero-based domain', () => {
    expect(buildGaugeBarModel({ value: 5, domain: [10, 20] }).pct).toBe(0)
    expect(buildGaugeBarModel({ value: 15, domain: [10, 20] }).pct).toBe(50)
    expect(buildGaugeBarModel({ value: 25, domain: [10, 20] }).pct).toBe(100)
    expect(buildGaugeBarModel({ value: 25, domain: [10, 20] }).valueNow).toBe(20)
  })

  it('Infinity pins to an end, never NaN', () => {
    expect(buildGaugeBarModel({ value: Infinity }).pct).toBe(100)
    expect(buildGaugeBarModel({ value: -Infinity }).pct).toBe(0)
  })

  it('a zero-width domain draws nothing instead of dividing by zero', () => {
    const m = buildGaugeBarModel({ value: 0, domain: [0, 0], segments: [{ value: 0, label: 'P0' }] })
    expect(m.pct).toBe(0)
    expect(m.segments[0].widthPct).toBe(0)
  })

  it('an unusable domain falls back to 0-100', () => {
    expect(resolveDomain([5, 1])).toEqual([0, 100])
    expect(resolveDomain([0, Number.NaN])).toEqual([0, 100])
    expect(resolveDomain(undefined)).toEqual([0, 100])
    expect(resolveDomain([0, 7])).toEqual([0, 7])
  })

  it('clamp and positionPct', () => {
    expect(clamp(5, 0, 3)).toBe(3)
    expect(clamp(-1, 0, 3)).toBe(0)
    expect(clamp(Number.NaN, 0, 3)).toBe(0)
    expect(positionPct(33, [0, 100])).toBe(33)
    expect(positionPct(3.5, [0, 7])).toBe(50)
  })
})

describe('gaugeBar model — segments', () => {
  const sum = (xs: { widthPct: number }[]) => xs.reduce((a, s) => a + s.widthPct, 0)

  it('lays segments end to end, and their widths sum to the total', () => {
    const m = buildGaugeBarModel({
      value: 10,
      domain: [0, 10],
      segments: [
        { value: 1, label: 'P0' },
        { value: 2, label: 'P1' },
        { value: 3, label: 'P2' },
        { value: 4, label: 'P3' },
      ],
    })
    expect(m.segments.map((s) => [s.startPct, s.widthPct])).toEqual([
      [0, 10],
      [10, 20],
      [30, 30],
      [60, 40],
    ])
    expect(sum(m.segments)).toBe(100)
  })

  it('a partial stack sums to its share of the domain', () => {
    const m = buildGaugeBarModel({ value: 5, domain: [0, 20], segments: [{ value: 2, label: 'a' }, { value: 3, label: 'b' }] })
    expect(sum(m.segments)).toBe(25)
  })

  it('a stack bigger than the domain is cut at the end of the track, never past it', () => {
    const m = buildGaugeBarModel({
      value: 15,
      domain: [0, 10],
      segments: [{ value: 6, label: 'a' }, { value: 6, label: 'b' }, { value: 3, label: 'c' }],
    })
    expect(sum(m.segments)).toBe(100)
    expect(m.segments.map((s) => s.widthPct)).toEqual([60, 40, 0])
    for (const s of m.segments) expect(s.startPct + s.widthPct).toBeLessThanOrEqual(100)
  })

  it('negative and non-finite segments draw as nothing, and do not shift the rest', () => {
    const m = buildGaugeBarModel({
      value: 4,
      domain: [0, 4],
      segments: [{ value: -2, label: 'neg' }, { value: Number.NaN, label: 'nan' }, { value: 4, label: 'real' }],
    })
    expect(m.segments.map((s) => s.widthPct)).toEqual([0, 0, 100])
    expect(m.segments[2].startPct).toBe(0)
  })

  it('names every segment in the value text', () => {
    const m = buildGaugeBarModel({ value: 3, domain: [0, 3], segments: [{ value: 1, label: 'P0' }, { value: 2, label: 'P1' }] })
    expect(gaugeValueText(m)).toBe('3 of 3; P0 1, P1 2')
  })
})

describe('gaugeBar model — target and ticks', () => {
  it('puts the target at its true position (clamped), and names it', () => {
    const m = buildGaugeBarModel({ value: 55, target: { value: 70, label: 'target' } })
    expect(m.target?.pct).toBe(70)
    expect(gaugeValueText(m)).toBe('55 of 100; target 70')
    expect(buildGaugeBarModel({ value: 1, domain: [0, 7], target: { value: 3.5, label: 't' } }).target?.pct).toBe(50)
    expect(buildGaugeBarModel({ value: 1, target: { value: 140, label: 't' } }).target?.pct).toBe(100)
  })

  it('places ticks at their real values, sorted, and notches only the interior ones', () => {
    const m = buildGaugeBarModel({
      value: 50,
      ticks: [{ value: 100 }, { value: 0, label: 'Blocked' }, { value: 70, label: 'Healthy' }, { value: 33, label: 'At risk' }],
    })
    expect(m.ticks.map((t) => [t.value, t.pct, t.notch])).toEqual([
      [0, 0, false],
      [33, 33, true],
      [70, 70, true],
      [100, 100, false],
    ])
  })
})

describe('gaugeBar model — tone', () => {
  it('higher is better', () => {
    expect(toneForValue(80, HEALTH)).toBe('good')
    expect(toneForValue(70, HEALTH)).toBe('good')
    expect(toneForValue(69.9, HEALTH)).toBe('warn')
    expect(toneForValue(33, HEALTH)).toBe('warn')
    expect(toneForValue(10, HEALTH)).toBe('bad')
  })

  it('lower is better (risk): a LOW score is good', () => {
    expect(toneForValue(10, RISK)).toBe('good')
    expect(toneForValue(39.9, RISK)).toBe('good')
    expect(toneForValue(40, RISK)).toBe('warn')
    expect(toneForValue(70, RISK)).toBe('bad')
    expect(toneForValue(95, RISK)).toBe('bad')
  })

  it('not measured is neutral, whatever the direction', () => {
    expect(toneForValue(null, RISK)).toBe('neutral')
    expect(toneForValue(null, HEALTH)).toBe('neutral')
    expect(toneForValue(Number.NaN, HEALTH)).toBe('neutral')
  })

  it('every tone is a theme token', () => {
    for (const c of Object.values(GAUGE_TONE_COLOR)) expect(c).toMatch(/^var\(--/)
    expect(GAUGE_TONE_COLOR.good).toBe('var(--status-passed)')
    expect(GAUGE_TONE_COLOR.bad).toBe('var(--status-failed)')
    expect(GAUGE_TONE_COLOR.accent).toBe('var(--color-accent)')
  })
})

describe('gaugeBar model — the scale labels, measured', () => {
  const ticks = buildGaugeBarModel({
    value: 50,
    ticks: [{ value: 0, label: 'Blocked' }, { value: 33, label: 'At risk' }, { value: 70, label: 'Healthy' }, { value: 100 }],
  }).ticks
  // A stand-in font: every character 6 px (the real one is measured in the browser).
  const mono = (px: number) => (text: string) => text.length * px

  it('anchors the first at the left edge, the last at the right, the rest on their notch', () => {
    expect([0, 33, 100].map(tickAnchor)).toEqual(['start', 'middle', 'end'])
  })

  it('without a layout (jsdom, before paint) shows the full text', () => {
    const l = layoutTickLabels(ticks, formatGaugeNumber, null, null)
    expect(l.level).toBe('full')
    expect(l.labels.map((x) => x.text)).toEqual(['Blocked · 0', 'At risk · 33', 'Healthy · 70', '100'])
  })

  it('keeps the full text when it fits', () => {
    expect(layoutTickLabels(ticks, formatGaugeNumber, 400, mono(6)).level).toBe('full')
  })

  it('drops the band names, not the numbers, when a wider font no longer fits them', () => {
    // The same 200 px scale: fits at 3 px a character, collides at 6 (DejaVu vs Segoe).
    expect(layoutTickLabels(ticks, formatGaugeNumber, 200, mono(3)).level).toBe('full')
    const wide = layoutTickLabels(ticks, formatGaugeNumber, 200, mono(6))
    expect(wide.level).toBe('values')
    expect(wide.labels.map((x) => x.text)).toEqual(['0', '33', '70', '100'])
  })

  it('keeps only the two ends when not even the numbers fit', () => {
    const l = layoutTickLabels(ticks, formatGaugeNumber, 40, mono(6))
    expect(l.level).toBe('ends')
    expect(l.labels.map((x) => x.text)).toEqual(['0', '100'])
  })

  it('places every measured label inside the track, clear of its neighbours', () => {
    const l = layoutTickLabels(ticks, formatGaugeNumber, 400, mono(6))
    let previousRight = -Infinity
    for (const x of l.labels) {
      const w = mono(6)(x.text)
      expect(x.leftPx).not.toBeNull()
      const left = x.leftPx ?? Number.NaN
      expect(left).toBeGreaterThanOrEqual(0)
      expect(left + w).toBeLessThanOrEqual(400)
      expect(left).toBeGreaterThanOrEqual(previousRight + 6)
      previousRight = left + w
    }
  })

  it('pushes a centred label that would hang off an end back inside the track', () => {
    const nearEnd = buildGaugeBarModel({ value: 1, ticks: [{ value: 99, label: 'Nearly' }] }).ticks
    const l = layoutTickLabels(nearEnd, formatGaugeNumber, 400, mono(6))
    expect(l.labels[0].anchor).toBe('middle')
    // "Nearly · 99" is 66 px; centred on x = 396 it would end at 429.
    expect(l.labels[0].leftPx).toBe(400 - 66)
  })

  it('without a measure nothing is placed in px: the anchor alone places it', () => {
    for (const x of layoutTickLabels(ticks, formatGaugeNumber, null, null).labels) expect(x.leftPx).toBeNull()
  })
})
