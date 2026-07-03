/**
 * Tests for ExecutiveSummaryPanel.
 *
 * Regression guard for the design-token migration: the panel must colour
 * every status/gate/category/metric surface through the per-theme
 * `--status-*`, `--gate-*`, and `--color-*` CSS tokens, never through raw
 * Tailwind palette classes (which bypass the light-theme legibility system).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ExecutiveSummaryPanel from './ExecutiveSummaryPanel'
import type { ExecutivePanel } from '@/services/runIntelligenceService'

function makePanel(overrides: Partial<ExecutivePanel> = {}): ExecutivePanel {
  return {
    headline: 'Release candidate is healthy',
    status_signal: 'GO',
    risk_score: 12,
    metrics: {
      build_number: '42',
      branch: 'main',
      total_tests: 100,
      passed: 98,
      failed: 2,
      skipped: 0,
      pass_rate: 98,
      duration_seconds: 120,
      failure_clusters: 1,
      anomaly_count: 0,
    },
    dominant_failure: { category: 'PRODUCT_BUG', count: 2, percentage: 80 },
    key_takeaways: ['Two product bugs remain'],
    baseline_comparison: {
      pass_rate_delta: -1.5,
      new_failures: 2,
      resolved: 3,
      classification: 'REGRESSION',
    },
    next_actions: ['Fix the checkout regression'],
    ...overrides,
  }
}

// Matches any raw Tailwind palette class the token system is meant to replace.
const RAW_PALETTE = /(?:text|bg|border)-(?:emerald|green|red|amber|yellow|orange|purple|blue|sky|violet|rose|teal|indigo|lime)-\d/

describe('ExecutiveSummaryPanel', () => {
  it('renders the headline and status signal', () => {
    render(<ExecutiveSummaryPanel panel={makePanel()} />)
    expect(screen.getByText('Release candidate is healthy')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: /Release readiness: GO/ })).toBeInTheDocument()
  })

  it('colours the GO gate through gate/status tokens, not raw palette', () => {
    const { container } = render(<ExecutiveSummaryPanel panel={makePanel()} />)
    const badge = screen.getByRole('status', { name: /Release readiness: GO/ })
    expect(badge.className).toContain('var(--gate-go)')
    expect(badge.className).toContain('var(--status-passed-bg)')
    expect(container.innerHTML).not.toMatch(RAW_PALETTE)
  })

  it('colours the CONDITIONAL and NO_GO gates through tokens', () => {
    const conditional = render(
      <ExecutiveSummaryPanel panel={makePanel({ status_signal: 'CONDITIONAL_GO' })} />,
    )
    expect(
      screen.getByRole('status', { name: /Release readiness: CONDITIONAL GO/ }).className,
    ).toContain('var(--gate-conditional)')
    expect(conditional.container.innerHTML).not.toMatch(RAW_PALETTE)
    conditional.unmount()

    const noGo = render(<ExecutiveSummaryPanel panel={makePanel({ status_signal: 'NO_GO' })} />)
    expect(
      screen.getByRole('status', { name: /Release readiness: NO GO/ }).className,
    ).toContain('var(--gate-no-go)')
    expect(noGo.container.innerHTML).not.toMatch(RAW_PALETTE)
  })

  it('renders dominant-failure and baseline sections without raw palette classes', () => {
    const { container } = render(
      <ExecutiveSummaryPanel
        panel={makePanel({
          dominant_failure: { category: 'FLAKY', count: 5, percentage: 40 },
        })}
      />,
    )
    // Baseline deltas, resolved/new-failure counts, and the failure bar all
    // route through tokens.
    expect(screen.getByText('new failures')).toBeInTheDocument()
    expect(screen.getByText('resolved')).toBeInTheDocument()
    expect(container.innerHTML).not.toMatch(RAW_PALETTE)
    expect(container.innerHTML).toContain('var(--status-flaky)')
  })
})
