/**
 * The Runs table's AI verdict (UX redesign P4, D2): which recommendation a
 * run's Run Intelligence report yields — the one its Analysis tab shows — and
 * that a page of runs is asked a few at a time.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

const http = vi.hoisted(() => ({ pending: [] as Array<{ url: string; resolve: (v: unknown) => void }> }))
vi.mock('@/services/http', () => ({
  getData: vi.fn((url: string) => new Promise((resolve) => http.pending.push({ url, resolve }))),
}))

import { getData } from '@/services/http'
import { aiVerdictOf, fetchAiVerdict, MAX_IN_FLIGHT, NO_VERDICT_TITLE } from './aiVerdict'

const verifiedReport = (recommendation: string) => ({
  decision_intelligence: {
    status: 'complete',
    verification: { status: 'passed' },
    release_decision: { recommendation },
    quality_review: { gap_report: null },
  },
  decision_report_verification: { status: 'passed' },
  latest_decision_attempt: { pipeline_run_id: 'p-1', status: 'published', verification_status: 'passed', at: '2026-09-18T10:00:00Z' },
})

describe('aiVerdictOf — the recommendation the run\'s Analysis tab shows', () => {
  it('reads the persisted release decision when there is no decision report', () => {
    expect(aiVerdictOf({ intelligence_available: true, release_decision: { recommendation: 'NO_GO' } } as never))
      .toEqual({ recommendation: 'NO_GO', review: null })
  })

  it('a verified decision report wins over the persisted decision', () => {
    const intel = {
      intelligence_available: true,
      structured_summary: verifiedReport('GO'),
      release_decision: { recommendation: 'NO_GO' },
    }
    expect(aiVerdictOf(intel as never)).toEqual({ recommendation: 'GO', review: null })
  })

  it('a report that did not pass verification yields no verdict — not the older persisted one', () => {
    const intel = {
      intelligence_available: true,
      structured_summary: { decision_report_verification: { status: 'failed' } },
      release_decision: { recommendation: 'GO' },
    }
    expect(aiVerdictOf(intel as never)).toEqual({ recommendation: null, reason: 'not-verified' })
  })

  it('carries the review state: a draft nobody reviewed, a rejected report; nothing for accepted', () => {
    const with_ = (state: string) => aiVerdictOf({ release_decision: { recommendation: 'GO' }, review: { state, message: '' } } as never)
    expect(with_('pending_review')).toEqual({ recommendation: 'GO', review: 'draft' })
    expect(with_('rejected')).toEqual({ recommendation: 'GO', review: 'rejected' })
    expect(with_('accepted')).toEqual({ recommendation: 'GO', review: null })
    expect(with_('not_applicable')).toEqual({ recommendation: 'GO', review: null })
  })

  it('makes nothing up: no payload, an unknown value, or an analysis without a recommendation is "—"', () => {
    expect(aiVerdictOf(null)).toEqual({ recommendation: null, reason: 'not-analysed' })
    expect(aiVerdictOf({} as never)).toEqual({ recommendation: null, reason: 'not-analysed' })
    expect(aiVerdictOf({ intelligence_available: false, release_decision: null } as never))
      .toEqual({ recommendation: null, reason: 'not-analysed' })
    expect(aiVerdictOf({ intelligence_available: true, release_decision: null } as never))
      .toEqual({ recommendation: null, reason: 'no-recommendation' })
    expect(aiVerdictOf({ intelligence_available: true, release_decision: { recommendation: 'MAYBE' } } as never))
      .toEqual({ recommendation: null, reason: 'no-recommendation' })
  })

  it('every "—" has a reason a reader can hover', () => {
    for (const reason of ['in-progress', 'not-analysed', 'no-recommendation', 'not-verified', 'unavailable'] as const) {
      expect(NO_VERDICT_TITLE[reason]).toMatch(/^(No AI verdict|AI verdict unavailable): /)
    }
  })
})

describe('fetchAiVerdict — a page of runs is asked a few at a time', () => {
  afterEach(() => {
    http.pending.length = 0
    vi.mocked(getData).mockClear()
  })

  it(`never has more than ${MAX_IN_FLIGHT} requests in flight, and asks each run once`, async () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f', 'g']
    const results = ids.map((id) => fetchAiVerdict(id))
    const settle = () => new Promise((r) => setTimeout(r, 0))
    await settle()
    expect(http.pending).toHaveLength(MAX_IN_FLIGHT)
    let answered = 0
    while (http.pending.length > 0) {
      expect(http.pending.length).toBeLessThanOrEqual(MAX_IN_FLIGHT)
      http.pending.shift()?.resolve({ release_decision: { recommendation: 'GO' } })
      answered += 1
      await settle()
    }
    expect(answered).toBe(ids.length)
    expect(vi.mocked(getData).mock.calls.map(([url]) => url)).toEqual(ids.map((id) => `/api/v1/runs/${id}/intelligence`))
    // The column renders its own "unavailable": no global toast for a row.
    expect(vi.mocked(getData).mock.calls.every(([, config]) => (config as { suppressToast?: boolean })?.suppressToast === true)).toBe(true)
    await expect(Promise.all(results)).resolves.toEqual(ids.map(() => ({ recommendation: 'GO', review: null })))
  })
})
