import { describe, expect, it } from 'vitest'
import { failureGroupsSeries, HOSTILE_GROUP_LABELS } from './failureGroups.fixtures'
import { failureGroupsModel, ownRows } from './failureGroups.model'
import { middleTruncate } from '../labelTruncate'
import {
  fitLabel,
  floorNote,
  groupPlotItems,
  MIN_VISIBLE_CHARS,
  plotBox,
  rankWalk,
  READOUT_WIDTH,
  WRAP_ANYWHERE,
  wordTruncate,
} from './plot.model'

const chars = (text: string) => [...text].length * 10

describe('plotBox', () => {
  it('a square plot sits beside the readout when both fit, else above it', () => {
    expect(plotBox(1000, 360, 'square')).toEqual({ width: 360, height: 360, beside: true })
    const narrow = plotBox(500, 360, 'square')
    expect(narrow).toEqual({ width: 360, height: 360, beside: false })
    expect(plotBox(300, 360, 'square')).toEqual({ width: 300, height: 300, beside: false })
    expect(plotBox(0, 360, 'square').width).toBe(0)
  })

  it('a wide plot takes the width the readout leaves', () => {
    expect(plotBox(1000, 360, 'wide')).toEqual({ width: 1000 - READOUT_WIDTH - 16, height: 360, beside: true })
    expect(plotBox(400, 360, 'wide')).toEqual({ width: 400, height: 360, beside: false })
  })
})

describe('fitLabel', () => {
  it('keeps a label that fits and cuts the MIDDLE of one that does not', () => {
    expect(fitLabel('short', 100, chars)).toBe('short')
    const cut = fitLabel('TimeoutError waiting for selector #checkout', 100, chars)
    expect(cut).toHaveLength(10)
    expect(cut).toMatch(/^Timeo….*kout$/u)
    expect(fitLabel('TimeoutError', 30, chars)).toBeNull()
  })

  it('never splits a surrogate pair', () => {
    const cut = fitLabel('😀😀😀😀😀😀😀😀😀😀😀😀', 90, chars) as string
    expect(cut).not.toBeNull()
    expect(cut).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/)
  })

  // R2-B F-01 / F-12: "co…or", "<i…>", "Err…3" named nothing; "const…ctor" saved one character of "constructor".
  it(`keeps at least ${MIN_VISIBLE_CHARS} characters of the label, else draws none`, () => {
    expect(MIN_VISIBLE_CHARS).toBe(6)
    // 'constructor_payment' at 10 px a character: 70 px holds 7 characters = 6 visible + the ellipsis.
    expect(fitLabel('constructor_payment', 70, chars)).toBe('con…ent')
    expect(fitLabel('constructor_payment', 69, chars)).toBeNull()
    expect(fitLabel('<img src=x onerror=1>', 50, chars)).toBeNull()
  })

  it('a cut that would save fewer than 3 characters is not made: the whole label, when the circle holds it', () => {
    // 11 characters, 100 px of room: the one-character saving "const…ctor" is not drawn …
    expect(fitLabel('constructor', 100, chars, 120)).toBe('constructor')
    // … nor is it when the whole label is too wide for the circle as well: the next cut saves 3.
    expect(fitLabel('constructor', 100, chars, 105)).toBe('cons…tor')
    // Without a looser bound the rule still holds: no cut saving 1 or 2 characters.
    expect(fitLabel('constructor', 100, chars)).toBe('cons…tor')
    expect(fitLabel('abcdefghij', 95, chars)).toBe('abc…hij')
  })
})

describe('wordTruncate (the readout button: R2-B F-23)', () => {
  it('cuts the middle out on WORD boundaries, so no word is split across the cut', () => {
    const label = 'TimeoutError: waiting for selector "#checkout" failed: timeout 30000ms exceeded'
    const cut = wordTruncate(label, 48)
    expect([...cut].length).toBeLessThanOrEqual(48)
    expect(cut).toBe('TimeoutError: waiting for…30000ms exceeded')
    const words = new Set(label.split(/\s+/))
    const [head, tail] = cut.split('…')
    for (const word of `${head} ${tail}`.split(/\s+/).filter(Boolean)) expect(words.has(word)).toBe(true)
  })

  it('a label that fits is unchanged; one long token falls back to the character cut', () => {
    expect(wordTruncate('KeyError: sku', 48)).toBe('KeyError: sku')
    const token = 'x'.repeat(160)
    expect(wordTruncate(token, 48)).toBe(middleTruncate(token, 48))
    expect([...wordTruncate(`${'😀'.repeat(40)} ${'😀'.repeat(40)}`, 48)].length).toBeLessThanOrEqual(48)
  })
})

describe('rankWalk', () => {
  const walk = rankWalk(3)
  const go = (current: number | null, key: Parameters<typeof walk>[1]['key']) => walk(current, { key, whole: false })
  it('moves in rank order and stops at the ends', () => {
    expect(go(null, 'ArrowRight')).toBe(0)
    expect(go(null, 'ArrowDown')).toBe(0)
    expect(go(null, 'ArrowLeft')).toBe(2)
    expect(go(null, 'End')).toBe(2)
    expect(go(0, 'ArrowRight')).toBe(1)
    expect(go(2, 'ArrowDown')).toBe(2)
    expect(go(1, 'ArrowUp')).toBe(0)
    expect(go(0, 'ArrowLeft')).toBe(0)
    expect(go(1, 'Home')).toBe(0)
    expect(go(0, 'End')).toBe(2)
    expect(rankWalk(0)(null, { key: 'ArrowRight', whole: false })).toBeNull()
  })
})

describe('groupPlotItems', () => {
  it('one item per group, in rank order, with its category, readout and mark', () => {
    const model = failureGroupsModel(failureGroupsSeries({ groups: 3, hostile: true }))
    const items = groupPlotItems(model.groups)
    expect(items.map((i) => i.rank)).toEqual([1, 2, 3])
    expect(items[0]).toMatchObject({ id: model.groups[0].id, label: HOSTILE_GROUP_LABELS[0], category: 'product_bug' })
    expect(items[1].category).toBe('infrastructure')
    expect(items[0].mark).toEqual({
      dimension: 'error_signature',
      value: model.groups[0].id,
      label: HOSTILE_GROUP_LABELS[0],
      y: 400,
      n: null,
    })
    expect(items[0].tip.title).toBe(`#1 ${HOSTILE_GROUP_LABELS[0]}`)
  })
})

describe('small words', () => {
  it('floorNote names the floor, or nothing', () => {
    expect(floorNote(0)).toBeNull()
    expect(floorNote(12)).toBe('Groups with fewer than 12 failures are drawn at the smallest size, so they can be pointed at.')
  })

  it('WRAP_ANYWHERE is the inline style (no new Tailwind class)', () => {
    expect(WRAP_ANYWHERE).toEqual({ overflowWrap: 'anywhere' })
  })

  it("ownRows: a failure group's rows panel opens only for one error_signature level", () => {
    expect(ownRows([{ dimension: 'error_signature', value: 'x' }])).toEqual([{ dimension: 'error_signature', value: 'x' }])
    expect(ownRows([])).toEqual([])
    expect(ownRows([{ dimension: 'suite', value: 'payments' }])).toEqual([])
    expect(
      ownRows([
        { dimension: 'error_signature', value: 'x' },
        { dimension: 'suite', value: 'payments' },
      ]),
    ).toEqual([])
  })
})
