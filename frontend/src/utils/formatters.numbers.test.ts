/**
 * VIZ-109: the number formatters every chart, table and filter count shares.
 *
 * Table-driven, and every expectation is a literal string: a formatter test
 * that computed its expectation with `Intl.NumberFormat` would agree with any
 * bug in how the formatter calls it. The outputs are Node's ICU (en-US unless
 * stated), which the browsers the app supports agree with for these inputs.
 */
import { describe, expect, it } from 'vitest'

import {
  DEFAULT_NUMBER_LOCALE,
  NO_VALUE,
  formatCompact,
  formatCompactWithExact,
  formatNumber,
  formatPercent,
  truncateMiddle,
} from './formatters'

/** ICU separates the German percent sign with U+00A0, not a plain space. */
const NO_BREAK_SPACE = String.fromCharCode(0xa0)

/** Every "no value" input. Each must render the em-dash, never a zero. */
const UNMEASURED: Array<[string, number | null | undefined]> = [
  ['null', null],
  ['undefined', undefined],
  ['NaN', Number.NaN],
  ['+Infinity', Number.POSITIVE_INFINITY],
  ['-Infinity', Number.NEGATIVE_INFINITY],
]

describe('shared constants', () => {
  it('pins one locale and one "no value" glyph', () => {
    expect(DEFAULT_NUMBER_LOCALE).toBe('en-US')
    expect(NO_VALUE).toBe('—')
  })
})

describe('formatNumber', () => {
  it.each<[number, string]>([
    [0, '0'],
    [-0, '0'],
    [-0.4, '0'],
    [0.4, '0'],
    [0.5, '1'],
    [-0.5, '-1'],
    [7, '7'],
    [999, '999'],
    [1000, '1,000'],
    [1234567, '1,234,567'],
    [-1234567, '-1,234,567'],
    [1.5, '2'],
    [Number.MAX_SAFE_INTEGER, '9,007,199,254,740,991'],
    [1e21, '1,000,000,000,000,000,000,000'],
  ])('%s → %s', (value, expected) => {
    expect(formatNumber(value)).toBe(expected)
  })

  it.each(UNMEASURED)('%s → the em-dash', (_name, value) => {
    expect(formatNumber(value)).toBe('—')
  })

  it('keeps as many fraction digits as asked for', () => {
    expect(formatNumber(1234.5678, { maximumFractionDigits: 2 })).toBe('1,234.57')
    expect(formatNumber(-0.004, { maximumFractionDigits: 2 })).toBe('0')
    expect(formatNumber(-0.005, { maximumFractionDigits: 2 })).toBe('-0.01')
  })

  it('uses a locale only when one is passed', () => {
    expect(formatNumber(1234567, { locale: 'de-DE' })).toBe('1.234.567')
    expect(formatNumber(1234567)).toBe('1,234,567')
  })
})

describe('formatPercent', () => {
  it.each<[number, string]>([
    [0, '0.0%'],
    [-0, '0.0%'],
    [-0.04, '0.0%'],
    [87.25, '87.3%'],
    [87.2, '87.2%'],
    [100, '100.0%'],
    [99.999, '100.0%'],
    [-5, '-5.0%'],
    [1234.5, '1,234.5%'],
  ])('percentage points %s → %s', (value, expected) => {
    expect(formatPercent(value)).toBe(expected)
  })

  it.each<[number, string]>([
    [0, '0.0%'],
    [0.8725, '87.3%'],
    [1, '100.0%'],
    [-0.05, '-5.0%'],
  ])('ratio %s → %s', (value, expected) => {
    expect(formatPercent(value, { from: 'ratio' })).toBe(expected)
  })

  it.each(UNMEASURED)('%s → the em-dash, never "0%" or "0.0%"', (_name, value) => {
    const text = formatPercent(value)
    expect(text).toBe('—')
    expect(text).not.toMatch(/0/)
  })

  it('reads the input scale it is told, not a guess', () => {
    // 0.5 is "half a percent" as points and "fifty percent" as a ratio.
    expect(formatPercent(0.5)).toBe('0.5%')
    expect(formatPercent(0.5, { from: 'ratio' })).toBe('50.0%')
  })

  it('formats in a passed locale', () => {
    expect(formatPercent(50, { locale: 'de-DE' })).toBe(`50,0${NO_BREAK_SPACE}%`)
  })
})

describe('formatCompact', () => {
  it.each<[number, string]>([
    [0, '0'],
    [-0, '0'],
    [-0.004, '0'],
    [999, '999'],
    [1000, '1K'],
    [1234, '1.23K'],
    [1234567, '1.23M'],
    [-1234567, '-1.23M'],
    [1e9, '1B'],
    [1e12, '1T'],
    // Past trillions there is no bigger en-US suffix: ICU keeps counting in T.
    [1e15, '1000T'],
    [Number.MAX_SAFE_INTEGER, '9007.2T'],
    [1e21, '1,000,000,000T'],
  ])('%s → %s', (value, expected) => {
    expect(formatCompact(value)).toBe(expected)
  })

  it.each(UNMEASURED)('%s → the em-dash', (_name, value) => {
    expect(formatCompact(value)).toBe('—')
  })
})

describe('formatCompactWithExact', () => {
  it.each<[number | null, { text: string; title: string }]>([
    [1234567, { text: '1.23M', title: '1,234,567' }],
    [1234567.891, { text: '1.23M', title: '1,234,567.891' }],
    [999, { text: '999', title: '999' }],
    [-1e21, { text: '-1,000,000,000T', title: '-1,000,000,000,000,000,000,000' }],
    [null, { text: '—', title: '—' }],
  ])('%s → %j', (value, expected) => {
    expect(formatCompactWithExact(value)).toEqual(expected)
  })
})

describe('truncateMiddle', () => {
  it.each<[string, number, string]>([
    ['short', 10, 'short'],
    ['exactly-10', 10, 'exactly-10'],
    ['checkout-regression-payments-spec', 11, 'check…-spec'],
    ['abcdefghij', 4, 'ab…j'],
    ['abcdefghij', 2, 'a…'],
    ['abcdefghij', 1, '…'],
    ['abcdefghij', 0, '…'],
    ['', 5, ''],
  ])('%j at %i → %j', (text, max, expected) => {
    expect(truncateMiddle(text, max)).toBe(expected)
  })

  it('never exceeds the limit and keeps both ends of a 300-character label', () => {
    const label = `suite-start-${'x'.repeat(277)}-suite-end!`
    expect(label).toHaveLength(300)
    const out = truncateMiddle(label, 60)
    expect(Array.from(out)).toHaveLength(60)
    expect(out.startsWith('suite-start-')).toBe(true)
    expect(out.endsWith('suite-end!')).toBe(true)
    expect(out).toContain('…')
  })

  it('never splits a surrogate pair', () => {
    const out = truncateMiddle('😀😀😀😀😀😀', 4)
    expect(out).toBe('😀😀…😀')
    // No lone surrogate anywhere in the result.
    expect(out).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/)
  })

  it('never splits a grapheme cluster (ZWJ family, combining accent)', () => {
    // MAN + ZWJ + WOMAN + ZWJ + GIRL: five code points, one character.
    const family = String.fromCodePoint(0x1f468, 0x200d, 0x1f469, 0x200d, 0x1f467)
    expect(truncateMiddle(`${family}${family}${family}${family}`, 3)).toBe(`${family}…${family}`)
    // "e" + COMBINING ACUTE ACCENT: two code points, one character.
    const accented = `e${String.fromCharCode(0x301)}`
    expect(accented).toHaveLength(2)
    expect(truncateMiddle(`${accented.repeat(5)}`, 3)).toBe(`${accented}…${accented}`)
  })
})
