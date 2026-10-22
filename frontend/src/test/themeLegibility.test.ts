/**
 * W29a UIX-01 / UIX-03 / UIX-07: TEXT legibility of the new token families, in
 * every theme of the registry, computed from `index.css` itself.
 *
 * Resolved the way a browser resolves them on <html>: every rule whose
 * selector is `:root` or exactly `[data-theme="<id>"]` applies, in source
 * order (equal specificity: the later rule wins — so the derived `:root` beats
 * the theme block and the light-theme rule placed after it beats the `:root`);
 * `var(--x)` follows the same map; `color-mix(in srgb, A p%, B)` interpolates
 * channel by channel; `transparent` is the card the colour is painted over.
 * WCAG 2.2 contrast; 4.5:1 is the body-text floor.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { LIGHT_THEMES, THEME_IDS, type ThemeId } from '@/store/themeStore'
import { readSourceFile } from './readSourceFile'

type Rgb = [number, number, number]

let css = ''
beforeAll(async () => {
  css = (await readSourceFile('src/index.css')).replace(/\/\*[\s\S]*?\*\//g, '')
})

/** The custom properties a theme resolves on <html>, in cascade order. */
function declarationsOf(theme: ThemeId): Map<string, string> {
  const out = new Map<string, string>()
  for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = rule[1].split(',').map((s) => s.trim())
    if (!selectors.some((s) => s === ':root' || s === `[data-theme="${theme}"]`)) continue
    for (const decl of rule[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out.set(decl[1], decl[2].trim())
  }
  return out
}

/** The top-level comma split of a function's arguments. */
function args(inner: string): string[] {
  const out: string[] = []
  let depth = 0
  let from = 0
  for (let i = 0; i < inner.length; i++) {
    if (inner[i] === '(') depth++
    else if (inner[i] === ')') depth--
    else if (inner[i] === ',' && depth === 0) {
      out.push(inner.slice(from, i).trim())
      from = i + 1
    }
  }
  out.push(inner.slice(from).trim())
  return out
}

const rgbOfHex = (hex: string): Rgb => {
  const h = hex.length === 4 ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}` : hex
  return [1, 3, 5].map((i) => Number.parseInt(h.slice(i, i + 2), 16)) as Rgb
}

function resolver(theme: ThemeId) {
  const tokens = declarationsOf(theme)
  const resolve = (expr: string, seen: string[] = []): Rgb => {
    const e = expr.trim()
    if (e === 'transparent') return resolve('var(--color-bg-card)', seen)
    if (e === 'white') return [255, 255, 255]
    if (e === 'black') return [0, 0, 0]
    if (/^#[0-9a-fA-F]{3}$|^#[0-9a-fA-F]{6}$/.test(e)) return rgbOfHex(e)
    const v = /^var\((--[\w-]+)\)$/.exec(e)
    if (v) {
      expect(seen, `a var() cycle through ${v[1]} in ${theme}`).not.toContain(v[1])
      const value = tokens.get(v[1])
      expect(value, `${v[1]} in the ${theme} theme`).toBeDefined()
      return resolve(value ?? '', [...seen, v[1]])
    }
    const m = /^color-mix\(in srgb,(.*)\)$/.exec(e)
    expect(m, `a colour expression the resolver reads: ${e}`).not.toBeNull()
    const [a, b] = args(m?.[1] ?? '')
    const pa = /^(.*)\s(\d+(?:\.\d+)?)%$/.exec(a)
    expect(pa, `a percentage on the first colour of ${e}`).not.toBeNull()
    const share = Number(pa?.[2]) / 100
    const ca = resolve(pa?.[1] ?? '', seen)
    const cb = resolve(b, seen)
    return ca.map((c, i) => c * share + cb[i] * (1 - share)) as Rgb
  }
  return resolve
}

function luminance([r, g, b]: Rgb): number {
  const lin = (c: number) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const STATUSES = ['passed', 'failed', 'broken', 'skipped', 'flaky'] as const
const SURFACES = ['--color-bg-card', '--color-bg', '--color-bg-secondary', '--color-bg-hover'] as const
const TEXT_FLOOR = 4.5

describe('the registry the suite walks', () => {
  it('is seven themes, two of them light', () => {
    expect(THEME_IDS).toHaveLength(7)
    expect([...LIGHT_THEMES].sort()).toEqual(['lab', 'paper'])
  })
})

describe.each(THEME_IDS)('%s', (theme) => {
  it('every --status-<s>-ink reads >= 4.5:1 on its own tint over the card, and on the four surfaces', () => {
    const resolve = resolver(theme)
    for (const s of STATUSES) {
      const ink = resolve(`var(--status-${s}-ink)`)
      expect(contrast(ink, resolve(`var(--status-${s}-bg)`)), `${s} ink on its tint`).toBeGreaterThanOrEqual(TEXT_FLOOR)
      for (const surface of SURFACES) {
        expect(contrast(ink, resolve(`var(${surface})`)), `${s} ink on ${surface}`).toBeGreaterThanOrEqual(TEXT_FLOOR)
      }
    }
  })

  it('--ui-info-ink reads >= 4.5:1 on --ui-info-bg', () => {
    const resolve = resolver(theme)
    expect(contrast(resolve('var(--ui-info-ink)'), resolve('var(--ui-info-bg)'))).toBeGreaterThanOrEqual(TEXT_FLOOR)
  })

  it('the destructive button text reads >= 4.5:1 on its fill', () => {
    const resolve = resolver(theme)
    expect(contrast(resolve('var(--color-btn-danger-text)'), resolve('var(--color-btn-danger-bg)'))).toBeGreaterThanOrEqual(TEXT_FLOOR)
  })

  // W29b UIX-05: the one-measure colour. A label sits ON the tint (the uncategorised cluster bubbles, no halo);
  // the measure itself is a mark (a bar, an edge), so 3:1 on the card.
  it('--color-text reads >= 4.5:1 on --chart-measure-tint, and --chart-measure is >= 3:1 on the card', () => {
    const resolve = resolver(theme)
    expect(contrast(resolve('var(--color-text)'), resolve('var(--chart-measure-tint)')), 'text on the measure tint').toBeGreaterThanOrEqual(TEXT_FLOOR)
    expect(contrast(resolve('var(--chart-measure)'), resolve('var(--color-bg-card)')), 'the measure on the card').toBeGreaterThanOrEqual(3)
    expect(resolve('var(--chart-measure)')).toEqual(resolve('var(--chart-series-2)'))
  })

  it('a solid status fill carries --color-text-on-status (count bubbles, stacked-bar segments)', () => {
    const resolve = resolver(theme)
    expect(resolve('var(--color-text-on-status)')).toEqual(resolve('var(--color-bg-card)'))
  })

  if (LIGHT_THEMES.has(theme)) {
    it('(light) white reads >= 4.5:1 on the primary button, and the danger button is SOLID failed', () => {
      const resolve = resolver(theme)
      expect(contrast([255, 255, 255], resolve('var(--color-btn-primary-bg)'))).toBeGreaterThanOrEqual(TEXT_FLOOR)
      expect(resolve('var(--color-btn-danger-bg)')).toEqual(resolve('var(--status-failed)'))
    })
  } else {
    it('(dark) the danger button keeps the failed tint', () => {
      const resolve = resolver(theme)
      expect(resolve('var(--color-btn-danger-bg)')).toEqual(resolve('var(--status-failed-bg)'))
    })
  }
})

describe('Paper (OR-06)', () => {
  it('muted text reads >= 4.5:1 on all four surfaces', () => {
    const resolve = resolver('paper')
    for (const surface of SURFACES) {
      expect(contrast(resolve('var(--color-text-muted)'), resolve(`var(${surface})`)), surface).toBeGreaterThanOrEqual(TEXT_FLOOR)
    }
  })
})

describe('the measure is real (positive control)', () => {
  it("white on Lab's 15 % failed tint is the bug this wave fixes: under 1.5:1", () => {
    const resolve = resolver('lab')
    expect(contrast([255, 255, 255], resolve('var(--status-failed-bg)'))).toBeLessThan(1.5)
  })

  it('the bare failed hue on its tint misses 4.5:1 in Slate (why the ink exists)', () => {
    const resolve = resolver('slate')
    expect(contrast(resolve('var(--status-failed)'), resolve('var(--status-failed-bg)'))).toBeLessThan(TEXT_FLOOR)
  })
})
