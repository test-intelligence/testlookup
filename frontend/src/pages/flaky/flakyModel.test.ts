import { describe, expect, it } from 'vitest'

import { flakySubtitle } from './flakyModel'

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
