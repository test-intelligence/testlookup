/**
 * REL-01: the gate badge names each decision in the contract's words. Two
 * different truths stay apart: "Not evaluated" is a RECORDED verdict
 * NOT_EVALUATED (evaluated, below the evidence floor); "Not evaluated yet" is
 * a release no QA lead has evaluated at all. Both use muted chrome.
 *
 * W29a UIX-03: the decided states read the one decision vocabulary (Go /
 * Conditional / No-Go, sentence case, no `uppercase`), with the decision's
 * legible ink as text; conditional is the warning hue, never skipped-gold.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import GateBadge from './GateBadge'
import type { GateDecision } from './types'

const ALL: GateDecision[] = ['go', 'conditional', 'no_go', 'not_evaluated', 'never_evaluated', 'cancelled']

const badgeOf = (decision: GateDecision) => {
  const { container, unmount } = render(<GateBadge decision={decision} />)
  const el = container.firstElementChild as HTMLElement
  return { el, unmount }
}

describe('GateBadge', () => {
  it.each([
    ['go', 'Go'],
    ['conditional', 'Conditional'],
    ['no_go', 'No-Go'],
    ['not_evaluated', 'Not evaluated'],
    ['never_evaluated', 'Not evaluated yet'],
    ['cancelled', 'Cancelled'],
  ] as const)('%s reads "%s"', (decision, label) => {
    render(<GateBadge decision={decision} />)
    const badge = screen.getByText(label)
    expect(badge).toHaveAttribute('aria-label', label)
    expect(badge).toHaveAttribute('data-gate-decision', decision)
  })

  it('never evaluated and not evaluated are muted, unlike a verdict', () => {
    const colour = (decision: GateDecision) => {
      const { el, unmount } = badgeOf(decision)
      const c = el.style.color
      unmount()
      return c
    }
    expect(colour('never_evaluated')).toBe('var(--color-text-muted)')
    expect(colour('not_evaluated')).toBe('var(--color-text-muted)')
    // W29a UIX-03: a decided state's text is its legible ink, on its own tint.
    expect(colour('go')).toBe('var(--status-passed-ink)')
    expect(colour('no_go')).toBe('var(--status-failed-ink)')
  })

  it('conditional is the warning hue (broken), never skipped-gold', () => {
    const { el } = badgeOf('conditional')
    const style = el.getAttribute('style') ?? ''
    expect(style).not.toMatch(/--status-skipped/)
    expect(el.style.color).toBe('var(--status-broken-ink)')
    expect(el.style.background).toBe('var(--status-broken-bg)')
    expect(el.style.borderColor).toBe('var(--status-broken-bd)')
  })

  it('draws its words in sentence case: no uppercase class on any state', () => {
    for (const decision of ALL) {
      const { el, unmount } = badgeOf(decision)
      expect(el.className, decision).not.toMatch(/\buppercase\b/)
      expect(el.className, decision).not.toMatch(/tracking-/)
      unmount()
    }
  })

  it('carries a reason in its accessible name', () => {
    render(<GateBadge decision="not_evaluated" reason="3 of 40 tests carry evidence; 5 required" />)
    expect(screen.getByText('Not evaluated')).toHaveAttribute('aria-label', 'Not evaluated — 3 of 40 tests carry evidence; 5 required')
  })
})
