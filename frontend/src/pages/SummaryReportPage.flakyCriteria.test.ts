/**
 * "Flaky 0" must not read as "no flaky tests" — BUG-007.
 *
 * A user saw this tile at 0 beside a flaky test on Flaky Coach and reported the
 * pair as a contradiction. Both were right under their own rule: Flaky Coach
 * needs 3 runs in 30 days, this tile needs 5 of a test's last 10. Measured live,
 * the two disagreed on 2 of 5 projects.
 *
 * The zero is the reading that misleads, so it is the case with the most
 * assertions here.
 */
import { describe, expect, it } from 'vitest'

import { flakyCriteriaSentence, flakySubtitle } from './summaryFlakyCriteria'
import type { FlakyCountCriteria } from '@/types/summaryReport'

const CRITERIA: FlakyCountCriteria = {
  window_runs: 10,
  min_runs: 5,
  min_flips: 2,
  min_failure_ratio: 0.1,
  max_failure_ratio: 0.9,
}

describe('flakySubtitle', () => {
  it('explains a zero instead of restating it', () => {
    const sub = flakySubtitle(0, CRITERIA, 0)
    // "0% of total" is the old subtitle: true, and no help at all.
    expect(sub).not.toMatch(/0(\.0)?%/)
    expect(sub).toMatch(/threshold/i)
    expect(sub).toContain('5')
  })

  it('shows the rate when there IS something to rate', () => {
    expect(flakySubtitle(12.5, CRITERIA, 3)).toMatch(/12\.5%/)
  })

  it('falls back to the rate when the payload predates the criteria', () => {
    // An older cached payload must not render "none met the undefined-run
    // threshold".
    const sub = flakySubtitle(0, undefined, 0)
    expect(sub).not.toMatch(/undefined/)
    expect(sub).toMatch(/of total/)
  })
})

describe('flakyCriteriaSentence', () => {
  it('states every condition the count applied', () => {
    const s = String(flakyCriteriaSentence(CRITERIA))
    expect(s).toContain('10')   // window
    expect(s).toContain('5')    // min runs
    expect(s).toContain('10%')  // ratio floor
    expect(s).toContain('90%')  // ratio ceiling
    expect(s).toMatch(/flips/)
  })

  it('says tests with too little history are not judged either way', () => {
    // The actual misreading: a QA lead concluding the project is clean.
    expect(String(flakyCriteriaSentence(CRITERIA))).toMatch(/not judged/i)
  })

  it('points at Flaky Coach as the looser surface', () => {
    // Without this the two pages still look like they contradict each other.
    expect(String(flakyCriteriaSentence(CRITERIA))).toMatch(/Flaky Coach/)
  })

  it('reads the numbers from the payload rather than hardcoding them', () => {
    // This count feeds the release gate's flaky hard cap. A tile that spelled
    // "5 runs" as a literal would keep saying so after the gate moved.
    const moved = String(flakyCriteriaSentence({
      ...CRITERIA,
      window_runs: 20,
      min_runs: 8,
      min_failure_ratio: 0.2,
      max_failure_ratio: 0.8,
    }))
    expect(moved).toContain('20')
    expect(moved).toContain('8')
    expect(moved).toContain('20%')
    expect(moved).toContain('80%')
    expect(moved).not.toContain('10%')
  })

  it('renders nothing rather than a half-sentence without criteria', () => {
    expect(flakyCriteriaSentence(undefined)).toBeUndefined()
  })
})
