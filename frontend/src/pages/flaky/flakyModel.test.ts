import { describe, expect, it } from 'vitest'

import {
  activeNowText,
  flakySubtitle,
  overCapText,
  percentToRate,
  policyDefaultsText,
  rateToPercent,
  slaDueText,
} from './flakyModel'

describe('flakySubtitle', () => {
  it('says nothing about the count before there is one (E2E 2026-10-10)', () => {
    // The Flaky tests header read "0 flaky tests · 0 quarantine candidates"
    // while the request was still running -- 30 s on the homelab -- and the
    // list then showed two flaky tests.
    expect(flakySubtitle(undefined)).toBe('Counting flaky tests · last 30 days')
    expect(flakySubtitle(undefined)).not.toMatch(/\b0 flaky/)
  })

  it('states a measured zero as zero, and agrees in number', () => {
    expect(flakySubtitle({ total_flaky: 0, quarantine_candidates: 0 })).toBe(
      '0 flaky tests · 0 quarantine candidates · last 30 days',
    )
    expect(flakySubtitle({ total_flaky: 1, quarantine_candidates: 1 })).toBe(
      '1 flaky test · 1 quarantine candidate · last 30 days',
    )
  })
})

describe('the quarantine policy rules (W09 FLK-02)', () => {
  it.each([
    [12, 8, "12 tests are quarantined; this project's policy warns past 8."],
    [2, 1, "2 tests are quarantined; this project's policy warns past 1."],
    [8, 8, null], // at the cap is not past it
    [3, 8, null],
    [12, 0, null], // 0 = no limit
    [undefined, 8, null], // no count yet
    [12, undefined, null], // no policy (a failed or malformed read)
    [Number.NaN, 8, null],
  ])('overCapText(%s, %s)', (active, cap, expected) => {
    expect(overCapText(active, cap)).toBe(expected)
  })

  it.each([
    [3, 8, '3 of 8 active now.'],
    [0, 8, '0 of 8 active now.'],
    [3, 0, '3 active now; no limit.'],
    [3, null, '3 active now.'],
    [undefined, 8, null],
  ])('activeNowText(%s, %s)', (active, cap, expected) => {
    expect(activeNowText(active, cap)).toBe(expected)
  })

  const NOW = new Date('2026-10-16T12:00:00Z')
  it.each([
    ['2026-10-23T09:00:00Z', 'SLA due 23 Oct'],
    ['2026-10-16T12:00:01Z', 'SLA due 16 Oct'],
    ['2027-01-02T00:00:00Z', 'SLA due 2 Jan 2027'],
    ['2026-10-16T11:59:59Z', null], // already past: the row says "stale" instead
    [null, null],
    [undefined, null],
    ['', null],
    ['not a date', null],
  ])('slaDueText(%s)', (staleAt, expected) => {
    expect(slaDueText(staleAt, NOW)).toBe(expected)
  })

  it('round-trips the flip rate through the card percent', () => {
    expect(rateToPercent(0.2)).toBe(20)
    expect(rateToPercent(0.125)).toBe(12.5)
    expect(percentToRate(20)).toBe(0.2)
    expect(percentToRate(35)).toBe(0.35)
    expect(percentToRate(12.5)).toBe(0.125)
  })

  it('builds the defaults sentence from the answer, never from literals', () => {
    expect(policyDefaultsText({ sla_days: 14, promote_after_passes: 20, detection_flip_rate_threshold: 0.2, detection_min_runs: 10 }))
      .toBe('Using defaults: SLA 14 days, promote after 20 passes, detection 20 % over 10 runs.')
    expect(policyDefaultsText({ sla_days: 30, promote_after_passes: 5, detection_flip_rate_threshold: 0.35, detection_min_runs: 4 }))
      .toBe('Using defaults: SLA 30 days, promote after 5 passes, detection 35 % over 4 runs.')
  })
})
