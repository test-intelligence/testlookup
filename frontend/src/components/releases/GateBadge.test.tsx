/**
 * REL-01: the gate badge names each decision in the contract's words. Two
 * different truths stay apart: "Not evaluated" is a RECORDED verdict
 * NOT_EVALUATED (evaluated, below the evidence floor); "Not evaluated yet" is
 * a release no QA lead has evaluated at all. Both use muted chrome.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import GateBadge from './GateBadge'
import type { GateDecision } from './types'

describe('GateBadge', () => {
  it.each([
    ['go', 'Go'],
    ['conditional', 'Conditional Go'],
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
      const { container, unmount } = render(<GateBadge decision={decision} />)
      const c = (container.firstElementChild as HTMLElement).style.color
      unmount()
      return c
    }
    expect(colour('never_evaluated')).toBe('var(--color-text-muted)')
    expect(colour('not_evaluated')).toBe('var(--color-text-muted)')
    expect(colour('go')).toBe('var(--status-passed)')
    expect(colour('no_go')).toBe('var(--status-failed)')
  })

  it('carries a reason in its accessible name', () => {
    render(<GateBadge decision="not_evaluated" reason="3 of 40 tests carry evidence; 5 required" />)
    expect(screen.getByText('Not evaluated')).toHaveAttribute('aria-label', 'Not evaluated — 3 of 40 tests carry evidence; 5 required')
  })
})
