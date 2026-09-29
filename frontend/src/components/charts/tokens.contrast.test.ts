/**
 * WCAG 2.2 SC 1.4.11 (non-text contrast, at least 3:1) for the kit's EDGES, in
 * all six themes, computed from `index.css` itself (R2 F9).
 *
 * The marks that carry a scale or a day — a gauge track's outline and notch
 * marks, a ring's track outline, an empty day's outline, a failed day's edge —
 * are drawn in the expressions the kit exports. Each is resolved here the way a
 * browser does: `var(--x)` from the theme's own block (the dark themes share a
 * block for the chart axis), `color-mix(in srgb, A p%, B)` interpolated
 * channel by channel, `transparent` as the card it sits on. A theme retuned in
 * `index.css`, or an edge moved to a quieter token, fails here, not in a
 * screenshot nobody measured.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { readSourceFile } from '@/test/readSourceFile'
import { cellFace } from './dayStrip.model'
import { RING_TRACK } from './RingGauge'
import { NON_TEXT_EDGE } from './tokens'

const THEMES = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight'] as const
type Theme = (typeof THEMES)[number]
type Rgb = [number, number, number]

let css = ''
beforeAll(async () => {
  css = await readSourceFile('src/index.css')
})

function blockOf(selector: string): string {
  const start = css.indexOf(selector)
  expect(start, `${selector} in index.css`).toBeGreaterThanOrEqual(0)
  return css.slice(start, css.indexOf('}', start))
}

function tokenHex(theme: Theme, name: string): string {
  const re = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\b`)
  const own = re.exec(blockOf(`[data-theme="${theme}"] {`))?.[1]
  // The five dark themes share one block for the chart axis and its kin.
  const shared = theme === 'lab' ? undefined : re.exec(blockOf('[data-theme="signal"], [data-theme="console"]'))?.[1]
  const hex = own ?? shared
  expect(hex, `${name} in the ${theme} theme`).toBeDefined()
  return hex as string
}

const rgbOf = (hex: string): Rgb => [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)) as Rgb

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

/** A kit colour expression, resolved in a theme, as it paints over the card. */
function resolve(expr: string, theme: Theme): Rgb {
  const e = expr.trim()
  const card = rgbOf(tokenHex(theme, '--color-bg-card'))
  if (e === 'transparent') return card
  const v = /^var\((--[\w-]+)\)$/.exec(e)
  if (v) return rgbOf(tokenHex(theme, v[1]))
  const m = /^color-mix\(in srgb,(.*)\)$/.exec(e)
  if (m) {
    const [a, b] = args(m[1])
    const pa = /^(.*)\s(\d+(?:\.\d+)?)%$/.exec(a)
    expect(pa, `a percentage on the first colour of ${e}`).not.toBeNull()
    const share = Number(pa?.[2]) / 100
    const ca = resolve(pa?.[1] ?? '', theme)
    const cb = resolve(b, theme)
    return ca.map((c, i) => c * share + cb[i] * (1 - share)) as Rgb
  }
  throw new Error(`cannot resolve colour expression: ${e}`)
}

function luminance([r, g, b]: Rgb): number {
  const f = (c: number) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const colourOfBorder = (border: string) => border.replace(/^1px (solid|dashed) /, '')

describe('non-text contrast of the kit’s edges, in all six themes (SC 1.4.11)', () => {
  it('measures something: the quiet tokens the edges replaced are under 3:1 somewhere', () => {
    // Positive control: without it a resolver that always answered "white on
    // black" would pass every assertion below.
    for (const quiet of ['var(--color-border)', 'var(--chart-grid)', 'var(--color-bg-secondary)']) {
      const worst = Math.min(...THEMES.map((t) => contrast(resolve(quiet, t), resolve('var(--color-bg-card)', t))))
      expect(worst, quiet).toBeLessThan(3)
    }
  })

  it.each(THEMES)('%s: the edge colour is at least 3:1 on the card and on the quiet track fill', (theme) => {
    const edge = resolve(NON_TEXT_EDGE, theme)
    expect(contrast(edge, resolve('var(--color-bg-card)', theme))).toBeGreaterThanOrEqual(3)
    // The track and an empty day are filled with `--color-bg-secondary` inside the edge.
    expect(contrast(edge, resolve('var(--color-bg-secondary)', theme))).toBeGreaterThanOrEqual(3)
  })

  it.each(THEMES)('%s: a gauge ring’s track outline and an empty day’s outline are that edge', (theme) => {
    const card = resolve('var(--color-bg-card)', theme)
    expect(contrast(resolve(RING_TRACK.stroke, theme), card)).toBeGreaterThanOrEqual(3)
    for (const mode of ['presence', 'status', 'intensity'] as const) {
      const border = colourOfBorder(cellFace(mode, 'none', 0).border)
      expect(contrast(resolve(border, theme), card), `${mode} empty day`).toBeGreaterThanOrEqual(3)
    }
  })

  it.each(THEMES)('%s: the faintest failed day keeps an edge of at least 3:1', (theme) => {
    const card = resolve('var(--color-bg-card)', theme)
    const faint = cellFace('status', 'fail', 0, 0)
    // The 45 % shade itself is under 3:1 on the dark themes; its edge carries the extent.
    expect(contrast(resolve(colourOfBorder(faint.border), theme), card)).toBeGreaterThanOrEqual(3)
  })
})
