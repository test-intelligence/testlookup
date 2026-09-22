/**
 * MetricCard accessibility (VIZ-302 follow-up): the two defects axe found in
 * the report-context gallery, pinned here so they cannot come back through a
 * page that happens not to be in that gallery.
 *
 *  1. The loading placeholder was a role-less `<div aria-label="Loading">`.
 *     `aria-label` is PROHIBITED on an element with no role (axe
 *     `aria-prohibited-attr`, serious): most screen readers ignore it, so the
 *     busy state was announced as nothing at all.
 *  2. Text contrast is measured against the real theme tokens in
 *     `src/index.css`, in all six themes, for every piece of TEXT the card
 *     renders (title, trend line — in each trend colour) on the card
 *     background. `lab` failed: the title was `--color-text-muted` (4.15:1)
 *     and a flat trend line likewise; the status hues at 12 px are below 4.5:1
 *     in four dark themes (`--status-failed` 3.94:1 on slate).
 */
import { render, screen } from '@testing-library/react'
import { BarChart3 } from 'lucide-react'
import { beforeAll, describe, expect, it } from 'vitest'
import MetricCard from './MetricCard'
import { readSourceFile } from '@/test/readSourceFile'

let CSS = ''
beforeAll(async () => {
  CSS = await readSourceFile('src/index.css')
})

const THEMES = ['signal', 'console', 'slate', 'ember', 'lab', 'midnight'] as const

/** `--token: #rrggbb` pairs declared in one `[data-theme="x"] { ... }` block. */
function themeTokens(theme: string): Map<string, string> {
  const start = CSS.indexOf(`[data-theme="${theme}"] {`)
  expect(start, `theme block ${theme} not found`).toBeGreaterThanOrEqual(0)
  const block = CSS.slice(start, CSS.indexOf('\n  }', start))
  const tokens = new Map<string, string>()
  for (const m of block.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b/g)) tokens.set(m[1], m[2])
  return tokens
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** The colour token an element's OWN text is painted with: the nearest
 *  `text-[var(--x)]` class on it or an ancestor inside the card. */
function textToken(el: Element | null): string {
  for (let node = el; node; node = node.parentElement) {
    const m = /(?:^|\s)text-\[var\((--[\w-]+)\)\]/.exec(node.getAttribute('class') ?? '')
    if (m) return m[1]
  }
  throw new Error('no text colour token found')
}

describe('MetricCard accessibility', () => {
  it('the loading placeholder puts no aria-label on a role-less element, and still says "Loading"', () => {
    const { container } = render(<MetricCard title="Pass Rate" icon={<BarChart3 />} loading />)
    for (const el of container.querySelectorAll('[aria-label]')) {
      // aria-label is only allowed where the element has a role that takes a name.
      expect(el.getAttribute('role'), `aria-label on a role-less <${el.tagName.toLowerCase()}>`).not.toBeNull()
    }
    // The busy state is still exposed to assistive tech, as text.
    expect(screen.getByText('Loading')).toBeInTheDocument()
    // ...and not as a live region (the card must never announce itself).
    expect(container.querySelector('[aria-live],[role="status"]')).toBeNull()
  })

  const cases: Array<[label: string, direction: 'up' | 'down' | 'flat', positive: 'up' | 'down']> = [
    ['good trend', 'up', 'up'],
    ['bad trend', 'up', 'down'],
    ['flat trend', 'flat', 'up'],
  ]

  for (const [label, direction, positive] of cases) {
    it(`title and trend text reach 4.5:1 on the card in all six themes (${label})`, () => {
      render(
        <MetricCard
          title="Pass Rate"
          metric={{ value: '98%', trend: 3, trend_direction: direction }}
          icon={<BarChart3 />}
          positiveDirection={positive}
        />,
      )
      const texts = {
        title: textToken(screen.getByText('Pass Rate')),
        trend: textToken(screen.getByText('3% vs prev period')),
      }
      for (const theme of THEMES) {
        const tokens = themeTokens(theme)
        const card = tokens.get('--color-bg-card')
        expect(card, `${theme} --color-bg-card`).toBeDefined()
        for (const [what, token] of Object.entries(texts)) {
          const fg = tokens.get(token)
          expect(fg, `${theme}: ${what} token ${token} is not a theme colour`).toBeDefined()
          const ratio = contrast(fg as string, card as string)
          expect(ratio, `${theme}: ${what} (${token}) ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })
  }
})
