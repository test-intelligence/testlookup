/**
 * VIZ-404 fix round B — what the Linux baselines showed (the model half):
 *
 *   1. On the twelve-suite chart every leader ran steeply down from its line's
 *      end, in the line's own colour and dash, and read as every suite
 *      crashing on the last day. The labels now settle in CENTRED clusters (no
 *      label travels far), and the row geometry puts the connector only in the
 *      gutter, clear of the line, in a pattern no series uses.
 *   2. "2 values are not measured" on release-over-release named the two days
 *      the shorter release simply does not have yet. Past-range days are now
 *      their own count and their own sentence.
 *   3. The gallery's not-comparable banner named suites; the API sends counts
 *      only. The fixture is now the API's own wording and reason_code.
 */
import { describe, expect, it } from 'vitest'
import { COMPARABILITY_REASON_CODES, type SeriesPoint } from '@/lib/viz/contracts'
import { GALLERY_BRANCHES, GALLERY_NOT_COMPARABLE, GALLERY_RELEASES } from '@/pages/dev/chartGalleryFixtures'
import { addUtcDays } from './seriesAlignment'
import {
  DIRECT_LABEL_GUTTER,
  DIRECT_LABEL_HEIGHT,
  LABEL_ROW,
  LABEL_SWATCH_LENGTH,
  LABEL_TEXT_ROOM,
  LEADER_DASH,
  OTHER_KEY,
  SERIES_DASHES,
  buildMultiSeriesModel,
  comparabilityBanner,
  comparabilityCaveat,
  placeDirectLabels,
  readComparability,
  type MultiSeriesInputSeries,
} from './multiSeriesModel'

const RATE = { kind: 'rate', title: 'Pass rate %' } as const
const H = DIRECT_LABEL_HEIGHT

function release(key: string, from: string, ys: (number | null)[], n = 100): MultiSeriesInputSeries {
  return {
    key,
    label: key,
    points: ys.map((y, i): SeriesPoint => {
      const x = addUtcDays(from, i)
      return y === null ? { x, y: null, n: 3, measured: false, reason: 'every test was skipped' } : { x, y, n }
    }),
  }
}

// ── 1. Direct labels: centred clusters, and a connector that is never data ────

describe('placeDirectLabels settles crowded labels in CENTRED clusters', () => {
  it('three lines ending at one height: one label above, one on it, one below', () => {
    const placed = placeDirectLabels(
      ['a', 'b', 'c'].map((key) => ({ key, y: 100 })),
      { top: 0, bottom: 300 },
    )
    expect(placed.labels.map((l) => l.y)).toEqual([100 - H, 100, 100 + H])
  })

  it('a cluster that grows into its neighbour merges with it, centred on all of them', () => {
    // [50, 50] settles at 42/58; 70 overlaps it (58 + 16 > 70), so the three
    // centre on their mean 56.67; the second 70 overlaps again: all four
    // centre on their mean 60 → 36, 52, 68, 84.
    const placed = placeDirectLabels(
      [50, 50, 70, 70].map((y, i) => ({ key: `k${i}`, y })),
      { top: 0, bottom: 300 },
    )
    expect(placed.labels.map((l) => l.y)).toEqual([36, 52, 68, 84])
  })

  it('moves no label further than it must: the twelve-suite ends, as the 640 px gallery draws them', () => {
    // The seven kept suites end within 52 px of each other; "Other" far above.
    const targets = [119.4, 131.1, 136.6, 148.4, 153.9, 165.6, 171.1]
    const requests = [{ key: 'other', y: 31.4 }, ...targets.map((y, i) => ({ key: `s${i}`, y }))]
    const placed = placeDirectLabels(requests, { top: 16, bottom: 236 })
    expect(placed.fits).toBe(true)
    const mean = targets.reduce((a, b) => a + b, 0) / targets.length
    const kept = placed.labels.filter((l) => l.key !== 'other')
    kept.forEach((label, i) => expect(label.y).toBeCloseTo(mean + (i - 3) * H, 9))
    expect(placed.labels.find((l) => l.key === 'other')?.y).toBe(31.4)
    // Pushed only DOWNWARDS (the old rule), the last would travel 44.3 px; centred, 23.5.
    const travel = Math.max(...placed.labels.map((l) => Math.abs(l.y - l.target)))
    expect(travel).toBeLessThan(24)
  })

  it('a cluster at an edge is slid back inside, still without overlap', () => {
    const placed = placeDirectLabels(
      ['a', 'b', 'c'].map((key) => ({ key, y: 2 })),
      { top: 0, bottom: 300 },
    )
    expect(placed.labels.map((l) => l.y)).toEqual([H / 2, H / 2 + H, H / 2 + 2 * H])
  })
})

describe('the direct label row: connector, swatch, name', () => {
  it('lays its parts out left to right, the connector starting clear of the line', () => {
    expect(LABEL_ROW.leaderStart).toBeGreaterThanOrEqual(3)
    expect(LABEL_ROW.leaderTurn).toBeGreaterThan(LABEL_ROW.leaderStart)
    expect(LABEL_ROW.leaderEnd).toBeGreaterThan(LABEL_ROW.leaderTurn)
    expect(LABEL_ROW.swatchStart).toBeGreaterThan(LABEL_ROW.leaderEnd)
    expect(LABEL_ROW.textStart).toBeGreaterThan(LABEL_ROW.swatchStart + LABEL_SWATCH_LENGTH)
    expect(DIRECT_LABEL_GUTTER).toBe(LABEL_ROW.textStart + LABEL_TEXT_ROOM)
  })

  it('the connector has a pattern no series has, the solid line included', () => {
    expect(LEADER_DASH).toBeDefined()
    expect(SERIES_DASHES).not.toContain(LEADER_DASH)
  })

  it('the swatch is long enough to show every series pattern whole', () => {
    for (const dash of SERIES_DASHES) {
      const period = (dash ?? '').split(' ').filter(Boolean).map(Number).reduce((a, b) => a + b, 0)
      expect(LABEL_SWATCH_LENGTH, `dash "${dash}"`).toBeGreaterThanOrEqual(period)
    }
  })
})

// ── 2. Past a release's range is not "not measured" ──────────────────────────

describe('release over release: a day past a release is not a missed measurement', () => {
  const r1 = release('R1', '2026-02-02', [70, 75, 80, 85])

  it('the shorter release has no gap, and a sentence saying where its range ends', () => {
    const model = buildMultiSeriesModel({ series: [r1, release('R2', '2026-02-20', [80, 85])], metric: RATE, alignment: 'release-start' })
    const r2 = model.lines[1]
    expect(r2.points.map((p) => p.pastRange === true)).toEqual([false, false, true, true])
    expect(r2.gaps).toBe(0)
    expect(r2.pastRange).toBe(2)
    expect(model.gaps).toBe(0)
    expect(model.rangeNote).toBe('R2 has 2 days; days 2–3 are past its range.')
  })

  it('one day past: singular', () => {
    const model = buildMultiSeriesModel({ series: [r1, release('R2', '2026-02-20', [80, 85, 90])], metric: RATE, alignment: 'release-start' })
    expect(model.rangeNote).toBe('R2 has 3 days; day 3 is past its range.')
  })

  it('an unmeasured day INSIDE the range is still a gap — counted apart from the days past it', () => {
    const model = buildMultiSeriesModel({
      series: [r1, release('R2', '2026-02-20', [80, null, 90])],
      metric: RATE,
      alignment: 'release-start',
    })
    expect(model.lines[1].gaps).toBe(1)
    expect(model.gaps).toBe(1)
    expect(model.lines[1].points[1].pastRange).toBeUndefined()
    expect(model.lines[1].points[1].reason).toBe('every test was skipped')
    expect(model.rangeNote).toBe('R2 has 3 days; day 3 is past its range.')
  })

  it('on a calendar axis a series that stops early is still a gap: there is no "range" to be past', () => {
    const early = { ...r1, points: r1.points.slice(0, 2), key: 'early', label: 'early' }
    const model = buildMultiSeriesModel({ series: [r1, early], metric: RATE })
    expect(model.lines[1].gaps).toBe(2)
    expect(model.lines[1].pastRange).toBe(0)
    expect(model.rangeNote).toBeNull()
  })

  it('"Other" is past its range only on a day past EVERY release it folds', () => {
    const long = Array.from({ length: 7 }, (_, i) => release(`L${i}`, '2026-01-05', [60, 61, 62, 63], 1000))
    const short = [release('S0', '2026-02-01', [50, 51], 10), release('S1', '2026-02-10', [52, 53, 54], 10)]
    const model = buildMultiSeriesModel({ series: [...long, ...short], metric: RATE, alignment: 'release-start' })
    const other = model.lines.find((line) => line.key === OTHER_KEY)
    // S0 runs out after day 1, S1 after day 2: only day 3 is past both.
    expect(other?.points.map((p) => p.pastRange === true)).toEqual([false, false, false, true])
    expect(other?.points[2].y).toBe(54)
    expect(model.gaps).toBe(0)
    expect(model.rangeNote).toBe('Other has 3 days; day 3 is past its range.')
  })

  it('the gallery item: R2 has 10 days; days 10–11 are past its range — and nothing is "not measured"', () => {
    const model = buildMultiSeriesModel({ series: GALLERY_RELEASES, metric: RATE, alignment: 'release-start' })
    const [long, short] = GALLERY_RELEASES.map((s) => s.points.length)
    expect(model.xs).toHaveLength(long)
    expect(model.gaps).toBe(0)
    expect(model.rangeNote).toBe(`R2 has ${short} days; days ${short}–${long - 1} are past its range.`)
  })
})

// ── 3. The gallery's not-comparable reason is the API's ──────────────────────

describe('the gallery says what the API says about comparability', () => {
  // `judge_comparability`'s different-suites wording, with its counts as the only variables.
  const API_DIFFERENT_SUITES =
    /^The (\d+) series compared by (release|branch) did not run the same suites in this scope: (\d+) suites ran in at least one of them, (\d+) in all of them\.$/

  it('is in the wire shape, with a reason_code the contract knows', () => {
    expect(GALLERY_NOT_COMPARABLE.comparable).toBe(false)
    expect(COMPARABILITY_REASON_CODES).toContain(GALLERY_NOT_COMPARABLE.reason_code)
    expect(GALLERY_NOT_COMPARABLE.reason_code).toBe('different_suites')
  })

  it('is worded exactly as the API words it, counts only', () => {
    const match = API_DIFFERENT_SUITES.exec(GALLERY_NOT_COMPARABLE.reason ?? '')
    expect(match, GALLERY_NOT_COMPARABLE.reason ?? '').not.toBeNull()
    expect(Number(match?.[1])).toBe(GALLERY_BRANCHES.length)
    expect(match?.[2]).toBe('branch')
    // More suites ran somewhere than everywhere: that is what "different suites" means.
    expect(Number(match?.[3])).toBeGreaterThan(Number(match?.[4]))
    for (const series of GALLERY_BRANCHES) expect(GALLERY_NOT_COMPARABLE.reason).not.toContain(series.key)
  })

  it('reads through the same reader as a real envelope, and the banner never doubles the full stop', () => {
    const read = readComparability(GALLERY_NOT_COMPARABLE)
    expect(read).toEqual({ comparable: false, reason: GALLERY_NOT_COMPARABLE.reason, reasonCode: 'different_suites' })
    const clause = (GALLERY_NOT_COMPARABLE.reason ?? '').slice(0, -1)
    expect(comparabilityBanner(read)).toBe(`Not directly comparable: ${clause}. The comparison is still shown.`)
    expect(comparabilityCaveat(read)).toBe(`not directly comparable: ${clause}; the comparison is still shown`)
    // A reason with no stop of its own reads the same as before.
    expect(comparabilityBanner({ comparable: false, reason: 'R2 ran 3 suites R1 did not' })).toBe(
      'Not directly comparable: R2 ran 3 suites R1 did not. The comparison is still shown.',
    )
  })
})
