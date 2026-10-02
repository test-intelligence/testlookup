/**
 * VIZ-106 presentation mode, from `index.css` itself: "body text contrast is
 * at least 7:1" in all six themes, on the four backgrounds text sits on, and
 * the type scale grows to room sizes.
 *
 * The `[data-presentation="on"]` block is resolved the way a browser resolves
 * it on <html>: a token the block sets takes the block's value (it is
 * unlayered and beats the theme blocks in `@layer base`), `var(--x)` follows
 * the same rule, anything else is the theme's own value (the five dark themes
 * share one block for the chart axis). A remap onto a quieter token, or a
 * theme retuned below 7:1, fails here rather than in a screenshot.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { readSourceFile } from '@/test/readSourceFile'

const THEMES = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight'] as const
type Theme = (typeof THEMES)[number]
type Rgb = [number, number, number]

const BACKGROUNDS = ['--color-bg', '--color-bg-secondary', '--color-bg-card', '--color-bg-hover'] as const
/** The text tokens the mode remaps: every one a body-text or axis-text colour below 7:1 on its own. */
const REMAPPED_TEXT = ['--color-text-muted', '--color-text-faint', '--chart-axis'] as const
const PRESENTATION_SELECTOR = '[data-presentation="on"] {'

let css = ''
beforeAll(async () => {
  css = await readSourceFile('src/index.css')
})

const withoutComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, (c) => ' '.repeat(c.length))

function blockOf(selector: string): string {
  const start = css.indexOf(selector)
  expect(start, `${selector} in index.css`).toBeGreaterThanOrEqual(0)
  return withoutComments(css.slice(start, css.indexOf('}', start)))
}

/** `--name: value;` declarations of a block, comments ignored. */
function declarations(block: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const m of block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) out.set(m[1], m[2].trim())
  return out
}

function themeHex(theme: Theme, name: string): string | undefined {
  const re = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\b`)
  const own = re.exec(blockOf(`[data-theme="${theme}"] {`))?.[1]
  const shared = theme === 'lab' ? undefined : re.exec(blockOf('[data-theme="signal"], [data-theme="console"]'))?.[1]
  return own ?? shared
}

const rgbOf = (hex: string): Rgb => [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)) as Rgb

/** A token's colour on <html> in `theme`, with or without presentation mode. */
function colourOf(name: string, theme: Theme, presenting: boolean, seen: string[] = []): Rgb {
  expect(seen, `a var() cycle through ${name}`).not.toContain(name)
  const block = presenting ? declarations(blockOf(PRESENTATION_SELECTOR)) : new Map<string, string>()
  const value = block.get(name)
  if (value !== undefined) {
    const ref = /^var\((--[\w-]+)\)$/.exec(value)
    if (ref) {
      // A var() on the same element reads the theme's value unless the block
      // sets that token too.
      return block.has(ref[1]) ? colourOf(ref[1], theme, presenting, [...seen, name]) : colourOf(ref[1], theme, false, [...seen, name])
    }
    expect(value, `${name} in the presentation block is a var() or a hex`).toMatch(/^#[0-9a-fA-F]{6}$/)
    return rgbOf(value)
  }
  const hex = themeHex(theme, name)
  expect(hex, `${name} in the ${theme} theme`).toBeDefined()
  return rgbOf(hex as string)
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

const px = (value: string | undefined) => Number(/^(\d+(?:\.\d+)?)px$/.exec(value ?? '')?.[1] ?? Number.NaN)

describe('presentation mode: text contrast of at least 7:1 (VIZ-106)', () => {
  it('measures something: without the mode, muted and faint text are under 7:1 in every theme', () => {
    // Positive control: a resolver that always answered "white on black"
    // would pass everything below.
    for (const theme of THEMES) {
      for (const name of ['--color-text-muted', '--color-text-faint']) {
        const worst = Math.min(...BACKGROUNDS.map((bg) => contrast(colourOf(name, theme, false), colourOf(bg, theme, false))))
        expect(worst, `${name} in ${theme}`).toBeLessThan(7)
      }
    }
  })

  it('the block remaps every quiet text token, and nothing else of colour', () => {
    const block = declarations(blockOf(PRESENTATION_SELECTOR))
    for (const name of REMAPPED_TEXT) expect(block.has(name), name).toBe(true)
    const colours = [...block.keys()].filter((k) => k.startsWith('--color-') || k.startsWith('--chart-'))
    expect(colours.sort()).toEqual([...REMAPPED_TEXT].sort())
  })

  it.each(THEMES)('%s: every remapped text token is at least 7:1 on the page, secondary, card and hover backgrounds', (theme) => {
    for (const name of REMAPPED_TEXT) {
      for (const bg of BACKGROUNDS) {
        const ratio = contrast(colourOf(name, theme, true), colourOf(bg, theme, true))
        expect(ratio, `${name} on ${bg} in ${theme}`).toBeGreaterThanOrEqual(7)
      }
    }
  })

  it('the block is unlayered, so it beats the theme blocks that set the same tokens on <html>', () => {
    const before = withoutComments(css.slice(0, css.indexOf(PRESENTATION_SELECTOR)))
    const depth = (before.match(/\{/g) ?? []).length - (before.match(/\}/g) ?? []).length
    expect(depth).toBe(0)
  })
})

describe('presentation mode: the type scale (VIZ-106)', () => {
  it('adds --text-stat-md at 22px and --text-stat-sm at 18px, the literals the Summary tiles and counts move onto (no pixel change)', () => {
    const root = declarations(blockOf(':root {\n    --text-display-sm'))
    expect(root.get('--text-stat-md')).toBe('22px')
    expect(root.get('--text-stat-sm')).toBe('18px')
    expect(root.get('--text-stat-lg')).toBe('24px')
  })

  it('every metric-value and headline token grows, and every metric value reaches --text-stat-lg', () => {
    const root = declarations(blockOf(':root {\n    --text-display-sm'))
    const block = declarations(blockOf(PRESENTATION_SELECTOR))
    const metricTokens = ['--text-stat-lg', '--text-stat-md', '--text-stat-sm']
    for (const name of ['--text-display-sm', '--text-display-md', '--text-display-lg', ...metricTokens]) {
      expect(px(block.get(name)), name).toBeGreaterThan(px(root.get(name)))
    }
    for (const name of metricTokens) {
      expect(px(block.get(name)), name).toBeGreaterThanOrEqual(px(block.get('--text-stat-lg')))
    }
  })

  it('inside a scaled chart drawing the small tokens are the DESK sizes again: the 16/11 scale alone enlarges them (B0 finding 4)', () => {
    // Without this a legend inside the drawing was enlarged twice: 16px x 16/11 = 23px.
    const root = declarations(blockOf(':root {\n    --text-xs:'))
    const scaled = declarations(blockOf('[data-presentation="on"] [data-chart-presentation] {'))
    for (const name of ['--text-xs', '--text-sm', '--text-base']) {
      expect(scaled.get(name), name).toBe(root.get(name))
      // And on screen that is still at least the 16px floor.
      expect(px(scaled.get(name)) * (16 / 11), name).toBeGreaterThanOrEqual(16)
    }
  })

  it('labels read through the small tokens are at least 16px', () => {
    const block = declarations(blockOf(PRESENTATION_SELECTOR))
    for (const name of ['--text-xs', '--text-sm', '--text-base']) {
      expect(px(block.get(name)), name).toBeGreaterThanOrEqual(16)
    }
  })
})
