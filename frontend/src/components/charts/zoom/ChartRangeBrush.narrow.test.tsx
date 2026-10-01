/**
 * Wave 2.6 fix round (R2-20) — the range brush at phone width, and on a
 * series with gaps.
 *
 *   - At 375 px the labels under the strip wrapped: "Sep / 5", "Showing all 14
 *     / days". The end labels never wrap, and when the three labels do not fit
 *     the row in the font they are drawn in, the middle one is the short form
 *     ("All 14 days"; zoomed, "7 of 14 days").
 *   - On Suite detail (a run every other day) every day of the strip's line
 *     is alone between gaps, and a zero-length round-capped segment of a
 *     1.25 px stroke is a dot nobody sees: the strip looked empty. A day alone
 *     between gaps is drawn as a dot of radius 2 px.
 */
import { render } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { addUtcDays } from '../seriesAlignment'
import ChartRangeBrush, { SPARK_DOT_RADIUS } from './ChartRangeBrush'
import { brushSelectionText } from './brushLabels'
import { CALENDAR_WORDS, type ZoomRange } from './zoomModel'

/** 7 px a character: the stand-in font every measurement below uses. */
const measure = vi.hoisted(() => ({ perChar: 7 as number | null }))
vi.mock('../textMeasure', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../textMeasure')>()
  return {
    ...actual,
    measureTextWidth: (text: string) => (measure.perChar === null ? null : text.length * measure.perChar),
  }
})

const XS = Array.from({ length: 14 }, (_, i) => addUtcDays('2026-09-05', i))

function Harness({ initial = null, spark }: { initial?: ZoomRange | null; spark?: readonly (readonly (number | null)[])[] }) {
  const [range, setRange] = useState<ZoomRange | null>(initial)
  return (
    <div data-chart-body="" tabIndex={-1}>
      <ChartRangeBrush xs={XS} range={range} onRangeChange={setRange} title="Pass rate trend" scale="point" spark={spark} />
    </div>
  )
}

/** Gives the label row a laid-out width (jsdom lays nothing out). */
function rowWidth(px: number) {
  const original = HTMLElement.prototype.getBoundingClientRect
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.hasAttribute('data-chart-brush-labels')) return { width: px, height: 16, top: 0, left: 0, right: px, bottom: 16, x: 0, y: 0, toJSON: () => ({}) }
    return original.call(this)
  })
}

afterEach(() => {
  vi.restoreAllMocks()
  measure.perChar = 7
})

const label = () => document.querySelector('[data-chart-brush-selection-label]')?.textContent

describe('R2-20: the labels under the strip at phone width', () => {
  it('the words: long, and the short form a narrow row gets', () => {
    expect(brushSelectionText({ xs: XS, range: { start: 0, end: 13 }, zoomed: false, pending: null, words: CALENDAR_WORDS })).toEqual({
      long: 'Showing all 14 days',
      short: 'All 14 days',
    })
    expect(brushSelectionText({ xs: XS, range: { start: 2, end: 8 }, zoomed: true, pending: null, words: CALENDAR_WORDS })).toEqual({
      long: `Showing ${CALENDAR_WORDS.range(XS[2], XS[8])} (7 of 14 days)`,
      short: '7 of 14 days',
    })
  })

  it('keeps the long label where the row has room for all three', () => {
    rowWidth(600)
    render(<Harness />)
    expect(label()).toBe('Showing all 14 days')
  })

  it('switches to the short label where the three do not fit (a 375 px page: a ~200 px strip)', () => {
    rowWidth(200)
    render(<Harness />)
    expect(label()).toBe('All 14 days')
  })

  it('zoomed, on a narrow row: the count of days, without the range the handles already show', () => {
    rowWidth(200)
    render(<Harness initial={{ start: 2, end: 8 }} />)
    expect(label()).toBe('7 of 14 days')
  })

  it('without a measure (no layout) the long label stays, as every other test sees it', () => {
    measure.perChar = null
    rowWidth(200)
    render(<Harness />)
    expect(label()).toBe('Showing all 14 days')
  })

  it('never wraps the end labels', () => {
    render(<Harness />)
    const ends = document.querySelectorAll('[data-chart-brush-labels] > span[aria-hidden="true"]')
    expect(ends).toHaveLength(2)
    for (const end of ends) expect(end.className).toMatch(/\bwhitespace-nowrap\b/)
  })
})

describe('R2-20: a day alone between gaps is a visible dot', () => {
  // Every other day, as Suite detail's data is.
  const everyOther = XS.map((_, i) => (i % 2 === 0 ? 80 + i : null))

  it('draws each lone day as a round dot of radius 2 px, in a layer of its own', () => {
    render(<Harness spark={[everyOther]} />)
    const dots = document.querySelector('[data-chart-brush-spark-dots]')
    expect(dots).not.toBeNull()
    expect(dots?.getAttribute('stroke-linecap')).toBe('round')
    expect(Number(dots?.getAttribute('stroke-width'))).toBe(2 * SPARK_DOT_RADIUS)
    expect(SPARK_DOT_RADIUS).toBe(2)
    expect(dots?.getAttribute('vector-effect')).toBe('non-scaling-stroke')
    // One dot per measured day, and no line path at all: there is no run to draw.
    expect(dots?.getAttribute('d')?.match(/M/g)).toHaveLength(7)
    expect(document.querySelector('[data-chart-brush-spark]')).toBeNull()
  })

  it('keeps runs of two or more days as the line, and only the lone day as a dot', () => {
    render(<Harness spark={[[90, 92, null, 94, null, 95, 96, 97, null, null, null, null, null, null]]} />)
    expect(document.querySelector('[data-chart-brush-spark]')?.getAttribute('d')?.match(/M/g)).toHaveLength(2)
    expect(document.querySelector('[data-chart-brush-spark-dots]')?.getAttribute('d')?.match(/M/g)).toHaveLength(1)
  })
})
