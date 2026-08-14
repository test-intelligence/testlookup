/**
 * The verdict surface must render every answer, expose its evidence, and be
 * incapable of hiding a failure.
 *
 * Roadmap Phase 4's UI. Roughly 84% of pass→fail transitions involve a flaky
 * test, so a bare failure list is mostly noise — but a verdict that a reader
 * cannot interrogate just relocates the trust problem.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import AttributionVerdictBadge from './AttributionVerdictBadge'
import {
  ATTRIBUTION_VERDICTS,
  type AttributionItem,
  type AttributionVerdict,
} from '@/types/attribution'

function item(
  verdict: AttributionVerdict = 'UNCERTAIN',
  overrides: Partial<AttributionItem> = {},
): AttributionItem {
  return {
    test_case_id: 'tc-1',
    test_name: 'test_checkout_total',
    suite_name: 'checkout',
    verdict,
    verdict_label: verdict.replace(/_/g, ' ').toLowerCase(),
    verdict_description: 'description',
    confidence: 0.85,
    rationale: 'Because of the evidence below.',
    inputs: {
      is_new_failure: true,
      last_green_run_id: 'run-0',
      flaky_score: 0.42,
      flaky_confidence: 'medium',
      cluster_key: null,
      cluster_cause_family: null,
      cluster_size: 0,
      change_overlap: 0.6,
      changed_files: ['src/checkout/total.py'],
      calibration_mode: 'advisory',
      calibration_specificity: 0.97,
    },
    votes: {},
    policy: 'Advisory only. No verdict suppresses, hides or auto-closes a failure.',
    ...overrides,
  }
}

describe('AttributionVerdictBadge', () => {
  it.each(ATTRIBUTION_VERDICTS)('renders a badge for %s', (verdict) => {
    // Every member renders — including UNCERTAIN, which is a real answer and
    // must not degrade to a blank space that reads as "nothing to see".
    render(<AttributionVerdictBadge attribution={item(verdict)} compact />)
    expect(screen.getByTitle(/because of the evidence/i)).toBeInTheDocument()
  })

  it('covers every verdict the API can return', () => {
    // Guards the vocabulary-subset defect class: a member added to the union
    // without a colour entry is a type error, and this catches a member added
    // to the API without reaching the constant.
    expect(new Set(ATTRIBUTION_VERDICTS).size).toBe(ATTRIBUTION_VERDICTS.length)
    expect(ATTRIBUTION_VERDICTS).toContain('UNCERTAIN')
  })

  it('hides the evidence behind a disclosure rather than omitting it', () => {
    render(<AttributionVerdictBadge attribution={item('LIKELY_YOUR_CHANGE')} />)
    const toggle = screen.getByRole('button', { name: /why/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('shows all five composed signals once expanded', () => {
    // When a verdict disagrees with an engineer, the useful question is WHICH
    // INPUT was wrong — unanswerable from a bare label.
    render(<AttributionVerdictBadge attribution={item('LIKELY_YOUR_CHANGE')} />)
    fireEvent.click(screen.getByRole('button', { name: /why/i }))

    expect(screen.getByText('New failure')).toBeInTheDocument()
    expect(screen.getByText('Flakiness')).toBeInTheDocument()
    expect(screen.getByText('Co-failure cluster')).toBeInTheDocument()
    expect(screen.getByText('Change overlap')).toBeInTheDocument()
    expect(screen.getByText('Classifier calibration')).toBeInTheDocument()
  })

  it('names the changed files rather than saying "your change"', () => {
    // Practitioners reject generic factor-level explanations.
    render(<AttributionVerdictBadge attribution={item('LIKELY_YOUR_CHANGE')} />)
    fireEvent.click(screen.getByRole('button', { name: /why/i }))
    expect(screen.getByText(/src\/checkout\/total\.py/)).toBeInTheDocument()
  })

  it('renders the advisory policy where a reader will see it', () => {
    // Not buried in a tooltip: a reader must not take a verdict as permission
    // to stop looking. ~1 in 6 newly-flaky tests reflected a real bug.
    render(<AttributionVerdictBadge attribution={item('LIKELY_FLAKY')} />)
    fireEvent.click(screen.getByRole('button', { name: /why/i }))
    expect(screen.getByText(/no verdict suppresses/i)).toBeInTheDocument()
  })

  it('says when a signal is absent instead of implying a value', () => {
    render(
      <AttributionVerdictBadge
        attribution={item('UNCERTAIN', {
          inputs: {
            ...item().inputs,
            flaky_score: null,
            change_overlap: null,
            cluster_key: null,
            calibration_specificity: null,
          },
        })}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /why/i }))
    expect(screen.getByText('not scored')).toBeInTheDocument()
    expect(screen.getByText('no commit range known')).toBeInTheDocument()
    expect(screen.getByText('none')).toBeInTheDocument()
    expect(screen.getByText(/not measured on this project yet/)).toBeInTheDocument()
  })

  it('never tells a reader a failure is safe to ignore', () => {
    render(<AttributionVerdictBadge attribution={item('LIKELY_FLAKY')} />)
    fireEvent.click(screen.getByRole('button', { name: /why/i }))
    const text = (document.body.textContent ?? '').toLowerCase()
    for (const forbidden of ['safe to ignore', 'can be ignored', 'no action needed']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('compact mode renders only the badge, for dense rows', () => {
    render(<AttributionVerdictBadge attribution={item('LIKELY_INFRA')} compact />)
    expect(screen.queryByRole('button', { name: /why/i })).not.toBeInTheDocument()
  })
})
