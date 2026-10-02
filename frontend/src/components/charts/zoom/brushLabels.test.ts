/**
 * `brushLabelsFit` (Wave 2.6 R2-20): whether the three labels under the range
 * brush fit its row, measured in the font the row is drawn in — DejaVu on a
 * Linux runner is measured as DejaVu — and with the row's own defaults where
 * the computed style leaves a part of the font unset.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { brushLabelsFit } from './brushLabels'

/** Every measurement: the font asked for, and 7 px a character (or no canvas). */
const measure = vi.hoisted(() => ({ fonts: [] as string[], perChar: 7 as number | null }))
vi.mock('../textMeasure', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../textMeasure')>()),
  measureTextWidth: (text: string, font: string) => {
    measure.fonts.push(font)
    return measure.perChar === null ? null : text.length * measure.perChar
  },
}))

/** A label row `width` px wide (jsdom lays nothing out). */
function row(width: number, style: Partial<CSSStyleDeclaration> = {}): HTMLElement {
  const el = document.createElement('div')
  Object.assign(el.style, style)
  document.body.appendChild(el)
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({ width, height: 16, top: 0, left: 0, right: width, bottom: 16, x: 0, y: 0, toJSON: () => ({}) })
  return el
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
  measure.fonts = []
  measure.perChar = 7
})

const NONE = { left: 0, right: 0 }

describe('brushLabelsFit — measured in the row’s own font', () => {
  it('asks for the computed weight, size and family', () => {
    brushLabelsFit(row(500, { fontWeight: '600', fontSize: '14px', fontFamily: '"DejaVu Sans", sans-serif' }), ['Sep 5', 'All 14 days', 'Sep 18'], NONE)
    expect(new Set(measure.fonts)).toEqual(new Set(['600 14px "DejaVu Sans", sans-serif']))
    expect(measure.fonts).toHaveLength(3)
  })

  it('falls back to normal 12 px sans-serif for each part the computed style leaves empty', () => {
    const el = row(500)
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({ fontWeight: '', fontSize: '', fontFamily: '' } as CSSStyleDeclaration)
    brushLabelsFit(el, ['Sep 5', 'All 14 days', 'Sep 18'], NONE)
    expect(new Set(measure.fonts)).toEqual(new Set(['400 12px sans-serif']))
  })
})

describe('brushLabelsFit — the room is the row less its insets, the labels plus their 8 px gaps', () => {
  // 5 + 11 + 6 characters at 7 px = 154 px, plus two 8 px gaps = 170 px.
  const TEXTS = ['Sep 5', 'All 14 days', 'Sep 18']

  it('fits at exactly the width they take, and not one pixel less', () => {
    expect(brushLabelsFit(row(170), TEXTS, NONE)).toBe(true)
    expect(brushLabelsFit(row(169), TEXTS, NONE)).toBe(false)
  })

  it('takes the strip’s insets off the row', () => {
    expect(brushLabelsFit(row(270), TEXTS, { left: 59, right: 41 })).toBe(true)
    expect(brushLabelsFit(row(270), TEXTS, { left: 59, right: 42 })).toBe(false)
  })

  it('says nothing (null: keep the long label) with no room laid out, or no canvas to measure with', () => {
    expect(brushLabelsFit(row(0), TEXTS, NONE)).toBeNull()
    expect(brushLabelsFit(row(100), TEXTS, { left: 60, right: 40 })).toBeNull()
    measure.perChar = null
    expect(brushLabelsFit(row(500), TEXTS, NONE)).toBeNull()
  })
})
