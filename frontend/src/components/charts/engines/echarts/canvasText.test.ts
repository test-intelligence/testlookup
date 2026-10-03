/**
 * Canvas text and zrender's text cache (B0 / I-G finding, Wave 3): a drawn
 * string that is an `Object.prototype` member name must never reach zrender's
 * plain-object LRU as itself. Checked against the REAL LRU, then through the
 * option builders that hand ECharts data names.
 */
import { afterEach, describe, expect, it } from 'vitest'
import LRU from 'zrender/lib/core/LRU.js'
import { canvasSafeLabels, canvasSafeText } from './canvasText'

const JOINER = String.fromCharCode(0x2060)
const MEMBER_NAMES = ['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf', '__defineGetter__']

/** Own keys Object.prototype should never have (what the polluted LRU wrote). */
const polluted = () => ['prev', 'next', 'key', 'value'].filter((k) => Object.prototype.hasOwnProperty.call(Object.prototype, k))

afterEach(() => {
  // Never leave a polluted prototype behind for another test, whatever happened above.
  for (const k of polluted()) delete (Object.prototype as Record<string, unknown>)[k]
})

describe('canvasSafeText', () => {
  it('joins every Object.prototype member name (a LEADING joiner, R1B-1), and leaves every other text alone', () => {
    for (const name of MEMBER_NAMES) {
      expect(canvasSafeText(name)).toBe(`${JOINER}${name}`)
      expect(Object.prototype.hasOwnProperty.call(Object.prototype, canvasSafeText(name))).toBe(false)
    }
    for (const text of ['payments', '', 'proto', '<img src=x onerror="window.__xss=1">', ' constructor', 'x__proto__']) {
      expect(canvasSafeText(text)).toBe(text)
    }
    const labels = ['a', '__proto__']
    expect(canvasSafeLabels(labels)).toEqual(['a', `${JOINER}__proto__`])
    expect(labels).toEqual(['a', '__proto__'])
  })

  it('a joined name cached and evicted through zrender’s real LRU leaves Object.prototype untouched', () => {
    const cache = new LRU<number>(2)
    for (const name of MEMBER_NAMES) {
      const key = canvasSafeText(name)
      cache.put(key, name.length)
      expect(cache.get(key)).toBe(name.length)
    }
    // Every member name was put through a 2-entry cache: most were evicted again.
    expect(polluted()).toEqual([])
    expect(Object.getPrototypeOf({})).toBe(Object.prototype)
  })
})
