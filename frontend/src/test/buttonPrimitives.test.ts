/**
 * W29a UIX-08 / UIX-13: one button shape, from `index.css` itself, and the
 * Tailwind config without its dead colour map.
 *
 * - Every `.btn-*` variant is 8 px (`rounded-md`), none a pill or 12 px;
 *   `.btn-primary` sets a VALID ring colour (the old `focus-ring-color` was
 *   not a CSS property); `.btn-danger` reads its three tokens; `.btn-sm` comes
 *   after the variants (same layer: the later rule wins).
 * - `tailwind.config.js` has no `colors` key and no hex literal, and its
 *   `fontSize` adds `2xs` and `md`.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { readSourceFile } from './readSourceFile'

let css = ''
let tailwind = ''
beforeAll(async () => {
  css = await readSourceFile('src/index.css')
  tailwind = await readSourceFile('tailwind.config.js')
})

const withoutComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '')

/** The body of the first rule whose selector is exactly `selector`. */
function rule(selector: string): string {
  const text = withoutComments(css)
  const m = new RegExp(`(?:^|[\\s}])${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`).exec(text)
  expect(m, `${selector} in index.css`).not.toBeNull()
  return m?.[1] ?? ''
}

const VARIANTS = ['.btn-primary', '.btn-secondary', '.btn-ghost', '.btn-danger'] as const

describe('one button shape (index.css)', () => {
  it.each(VARIANTS)('%s is 8 px: rounded-md, never rounded-full / rounded-lg', (variant) => {
    const body = rule(variant)
    expect(body).toMatch(/@apply[^;]*\brounded-md\b/)
    expect(body).not.toMatch(/\brounded-(full|lg|xl)\b/)
  })

  it('.btn-primary sets --tw-ring-color and no invalid focus-ring-color', () => {
    const body = rule('.btn-primary')
    expect(body).toMatch(/--tw-ring-color:\s*var\(--color-ring\)/)
    expect(withoutComments(css)).not.toMatch(/focus-ring-color/)
  })

  it('.btn-danger paints with the three danger tokens, and hovers with the fourth', () => {
    const body = rule('.btn-danger')
    expect(body).toMatch(/background:\s*var\(--color-btn-danger-bg\)/)
    expect(body).toMatch(/color:\s*var\(--color-btn-danger-text\)/)
    expect(body).toMatch(/border:\s*1px solid var\(--color-btn-danger-bd\)/)
    expect(rule('.btn-danger:hover:not(:disabled)')).toMatch(/background:\s*var\(--color-btn-danger-hover\)/)
  })

  it('.btn-sm is 28 px with 12 px text, declared AFTER every variant', () => {
    expect(rule('.btn-sm')).toMatch(/@apply\s+px-3 py-1\.5 text-xs/)
    const text = withoutComments(css)
    const sm = text.indexOf('.btn-sm {')
    for (const variant of VARIANTS) {
      expect(text.indexOf(`${variant} {`), variant).toBeGreaterThan(-1)
      expect(text.indexOf(`${variant} {`), variant).toBeLessThan(sm)
    }
  })

  it('the md variants keep their padding and 13 px text (no button changes height)', () => {
    for (const variant of ['.btn-primary', '.btn-secondary', '.btn-danger']) {
      expect(rule(variant), variant).toMatch(/@apply\s+px-4 py-2 rounded-md font-semibold text-sm/)
    }
    expect(rule('.btn-ghost')).toMatch(/@apply\s+px-3 py-1\.5 rounded-md text-sm/)
  })
})

describe('tailwind.config.js (UIX-13 / UIX-04a)', () => {
  it('has no colour map and no hex literal', () => {
    expect(tailwind).not.toMatch(/\bcolors\s*:/)
    expect(tailwind).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
    expect(tailwind).not.toMatch(/\bbrand\s*:/)
  })

  it('fontSize adds 2xs and md, written as the app tokens', () => {
    const block = /fontSize:\s*\{([\s\S]*?)\n\s{6}\}/.exec(tailwind)?.[1] ?? ''
    expect(block).toMatch(/'2xs':\s*\['var\(--text-2xs\)'/)
    expect(block).toMatch(/\bmd:\s*\['var\(--text-md\)'/)
  })
})
