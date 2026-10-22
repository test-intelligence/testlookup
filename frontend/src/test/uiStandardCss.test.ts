/**
 * W29b UI standard (UIX-04b, UIX-05, UIX-06, UIX-09, F5), read from `index.css` itself like W29a's
 * `buttonPrimitives.test.ts`:
 *
 * - `.eyebrow` is THE uppercase label: 11 px (`--text-2xs`) / 600 / uppercase / `--tracking-label`;
 * - the table classes: `.th` is the label style on the sunken row in --color-text-secondary, `.th-sort` inherits
 *   the header's case, tracking and weight (a button's UA style resets text-transform), `.th-num` / `.td-num`
 *   right-align tabular figures, `.td` keeps its 13 px and padding, the densities copy the page tables' paddings,
 *   the hover is grey and the selected row accent-muted with a 3 px bar;
 * - `.card` is 12 px (`rounded-lg`);
 * - `--chart-measure` / `--chart-measure-tint` are derived from `--chart-series-2` in the derived `:root`;
 * - the checkbox and radio are drawn from tokens, and `color-scheme` is dark in a `:root` and light in the rule
 *   naming exactly the light themes.
 */
import { beforeAll, describe, expect, it } from 'vitest'
import { LIGHT_THEMES } from '@/store/themeStore'
import { readSourceFile } from './readSourceFile'

let css = ''
beforeAll(async () => {
  css = (await readSourceFile('src/index.css')).replace(/\/\*[\s\S]*?\*\//g, '')
})

/** `{ selector, body }` of every innermost rule, in source order. */
function rules(): { selector: string; body: string }[] {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1].trim().replace(/\s+/g, ' '), body: m[2] }))
}

/** The body of the one rule whose selector is exactly `selector` (whitespace-normalised). */
function rule(selector: string): string {
  const found = rules().filter((r) => r.selector === selector)
  expect(found, `exactly one "${selector}" rule in index.css`).toHaveLength(1)
  return found[0]?.body ?? ''
}

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The value of `property` in a rule body, or null. */
function decl(body: string, property: string): string | null {
  const m = new RegExp(`(?:^|[;{\\s])${escapeRe(property)}\\s*:\\s*([^;]+);`).exec(body)
  return m ? m[1].trim() : null
}

/** The values that make a label (the .eyebrow look). */
function expectLabelStyle(body: string, what: string) {
  expect(decl(body, 'font-size'), `${what} font-size`).toBe('var(--text-2xs)')
  expect(decl(body, 'line-height'), `${what} line-height`).toBe('calc(16 / 11)')
  expect(decl(body, 'font-weight'), `${what} font-weight`).toBe('600')
  expect(decl(body, 'text-transform'), `${what} text-transform`).toBe('uppercase')
  expect(decl(body, 'letter-spacing'), `${what} letter-spacing`).toBe('var(--tracking-label)')
}

describe('the label style (.eyebrow)', () => {
  it('is 11 px / 600 / uppercase / --tracking-label, muted', () => {
    const body = rule('.eyebrow')
    expectLabelStyle(body, '.eyebrow')
    expect(decl(body, 'color')).toBe('var(--color-text-muted)')
  })

  it('--tracking-label is 0.06em, in the type :root that still opens with --text-xs', () => {
    const typeRoot = rules().find((r) => r.selector === ':root' && /--text-2xs\s*:/.test(r.body))
    expect(typeRoot, 'the type-scale :root').toBeDefined()
    expect(decl(typeRoot?.body ?? '', '--tracking-label')).toBe('0.06em')
    expect((typeRoot?.body ?? '').trim().startsWith('--text-xs:')).toBe(true)
  })
})

describe('one table (UIX-06)', () => {
  it('.th is the label style in --color-text-secondary on the sunken row, px-4 py-3, left', () => {
    const body = rule('.th')
    expectLabelStyle(body, '.th')
    expect(decl(body, 'color')).toBe('var(--color-text-secondary)')
    expect(decl(body, 'background')).toBe('var(--color-bg-secondary)')
    expect(body).toMatch(/@apply\s+px-4 py-3 text-left;/)
  })

  it(".th-sort inherits the header's font, case, tracking and colour (a button resets them)", () => {
    const body = rule('.th-sort')
    for (const property of ['font', 'letter-spacing', 'text-transform', 'color']) {
      expect(decl(body, property), `.th-sort ${property}`).toBe('inherit')
    }
    expect(body).toMatch(/@apply[^;]*\binline-flex\b[^;]*\bw-full\b[^;]*\bbg-transparent\b/)
    expect(decl(rule('.th-sort:hover'), 'color')).toBe('var(--color-text)')
  })

  it('.th-num / .td-num right-align tabular figures, and a numeric sort button sits on the right', () => {
    const body = rule('.th-num, .td-num')
    expect(decl(body, 'text-align')).toBe('right')
    expect(decl(body, 'font-variant-numeric')).toBe('tabular-nums')
    expect(decl(rule('.th-num .th-sort'), 'justify-content')).toBe('flex-end')
  })

  it('.td keeps its 13 px text and its padding', () => {
    const body = rule('.td')
    expect(decl(body, 'color')).toBe('var(--color-text-secondary)')
    expect(body).toMatch(/@apply\s+px-4 py-3 text-sm;/)
  })

  it("the densities copy the page tables' paddings (dense px-3 py-2, compact px-2 py-1.5)", () => {
    expect(rule('table[data-density="dense"] :is(.th, .td)')).toMatch(/@apply\s+px-3 py-2;/)
    expect(rule('table[data-density="compact"] :is(.th, .td)')).toMatch(/@apply\s+px-2 py-1\.5;/)
  })

  it('the row hover is grey; the selected row is accent-muted with the 3 px accent bar on its first cell', () => {
    expect(decl(rule('.table-row:hover'), 'background')).toBe('var(--color-bg-hover)')
    expect(decl(rule('.table-row[data-selected]'), 'background')).toBe('var(--color-accent-muted)')
    expect(decl(rule('.table-row[data-selected] > :first-child'), 'box-shadow')).toBe('inset 3px 0 0 var(--color-accent)')
    expect(decl(rule('.table-row'), 'border-bottom')).toBe('1px solid var(--color-border)')
  })
})

describe('.card (UIX-09)', () => {
  it('applies rounded-lg (12 px), never rounded-xl', () => {
    const body = rule('.card')
    expect(body).toMatch(/@apply\s+rounded-lg p-4;/)
    expect(body).not.toMatch(/rounded-xl/)
  })
})

describe('the one-measure colour (UIX-05)', () => {
  it('--chart-measure and --chart-measure-tint are derived from --chart-series-2 in the derived :root', () => {
    const derived = rules().find((r) => r.selector === ':root' && /--status-passed-bg\s*:/.test(r.body))
    expect(derived, 'the derived :root').toBeDefined()
    expect(decl(derived?.body ?? '', '--chart-measure')).toBe('var(--chart-series-2)')
    expect(decl(derived?.body ?? '', '--chart-measure-tint')).toBe('color-mix(in srgb, var(--chart-series-2) 35%, var(--color-bg-card))')
    // No theme block re-defines them: they follow every theme's series-2.
    for (const r of rules().filter((x) => x.selector.includes('[data-theme='))) {
      expect(r.body, r.selector).not.toMatch(/--chart-measure/)
    }
  })
})

describe('the scheme of native controls', () => {
  it('is dark in a :root', () => {
    const roots = rules().filter((r) => r.selector === ':root' && decl(r.body, 'color-scheme') !== null)
    expect(roots.map((r) => decl(r.body, 'color-scheme'))).toEqual(['dark'])
    expect(decl(roots[0]?.body ?? '', 'accent-color')).toBe('var(--color-accent)')
  })

  it('is light in the one rule that names exactly the light themes', () => {
    const light = rules().filter((r) => r.selector.includes('[data-theme=') && decl(r.body, 'color-scheme') !== null)
    expect(light).toHaveLength(1)
    expect(decl(light[0]?.body ?? '', 'color-scheme')).toBe('light')
    const named = [...(light[0]?.selector ?? '').matchAll(/\[data-theme="([\w-]+)"\]/g)].map((m) => m[1]).sort()
    expect(named).toEqual([...LIGHT_THEMES].sort())
    // The same rule as the light themes' solid destructive button (W29a), placed after the derived :root.
    expect(decl(light[0]?.body ?? '', '--color-btn-danger-bg')).toBe('var(--status-failed)')
  })
})

describe('the token-drawn checkbox and radio', () => {
  const controlRules = () => rules().filter((r) => /input\[type="(?:checkbox|radio)"\]/.test(r.selector))

  it('one base rule for both types: appearance none, 13 px; one colour rule: the input surface and a muted edge', () => {
    const base = rule('input[type="checkbox"], input[type="radio"]')
    expect(decl(base, 'appearance')).toBe('none')
    expect(decl(base, 'width')).toBe('13px')
    expect(decl(base, 'height')).toBe('13px')
    const colours = rule(':is(input[type="checkbox"], input[type="radio"])')
    expect(decl(colours, 'background')).toBe('var(--color-bg-input)')
    expect(decl(colours, 'border')).toBe('1px solid var(--color-text-muted)')
    expect(controlRules().some((r) => r.selector.includes('checkbox'))).toBe(true)
    expect(controlRules().some((r) => r.selector.includes('radio'))).toBe(true)
  })

  it('checked is the primary button pair; the focus ring is kept', () => {
    expect(decl(rule('input[type="checkbox"]:is(:checked, :indeterminate)'), 'background')).toBe('var(--color-btn-primary-bg)')
    expect(decl(rule('input[type="checkbox"]:checked::before'), 'opacity')).toBe('1')
    expect(decl(rule('input[type="radio"]:is(:checked)'), 'background')).toBe('var(--color-btn-primary-bg)')
    expect(decl(rule('input[type="checkbox"]:focus-visible, input[type="radio"]:focus-visible'), 'outline')).toBe('2px solid var(--color-ring)')
  })

  it('the colours sit in @layer utilities, where a page bg-* / border-* utility cannot paint over the checked box', () => {
    // Tailwind v4 layers: a utility beats any @layer base rule whatever its specificity, so a checkbox carrying
    // `bg-[var(--color-bg-secondary)]` drew its checked state in the unchecked colour (white tick on near-white).
    const start = css.indexOf('@layer utilities {')
    expect(start, 'an @layer utilities block in index.css').toBeGreaterThan(-1)
    let depth = 0
    let end = start
    for (let i = css.indexOf('{', start); i < css.length; i += 1) {
      if (css[i] === '{') depth += 1
      if (css[i] === '}') depth -= 1
      if (depth === 0) {
        end = i
        break
      }
    }
    const block = css.slice(start, end)
    for (const selector of [
      ':is(input[type="checkbox"], input[type="radio"])',
      'input[type="checkbox"]:is(:checked, :indeterminate)',
      'input[type="radio"]:is(:checked)',
    ]) {
      expect(block, selector).toContain(selector)
    }
    expect(block, 'no size in the utilities layer: page size utilities (h-4 w-4) must keep winning').not.toMatch(/\b(width|height)\s*:/)
  })

  it('reads tokens only: no hex, and no function but var() and rotate() (no colour literal, no image: CSP)', () => {
    for (const r of controlRules()) {
      expect(r.body, r.selector).not.toContain('#')
      const functions = [...r.body.matchAll(/([\w-]+)\(/g)].map((m) => m[1])
      expect(functions.filter((f) => f !== 'var' && f !== 'rotate'), r.selector).toEqual([])
    }
  })
})
