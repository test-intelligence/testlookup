/**
 * The Runs table's AI verdict (UX redesign P4, D2; FAIL-04): which
 * recommendation a run's Run Intelligence report yields — the one its Analysis
 * tab shows — read from the FULL payload (the Analysis tab) or from the batch
 * route's SLIM `verdict_inputs` (the column), and how a batch item becomes a
 * cell's verdict.
 *
 * `VERDICT_CASES` is the parity table of contract §1.2: the SAME 16 rows, in
 * the SAME order, are `CASES` in `backend/tests/test_run_verdict.py`, where
 * `verdict_inputs_of(full) == slim` and `ai_recommendation_of(...) ==
 * recommendation`. Here `aiVerdictOf(full)` and `aiVerdictOf(slim)` must both
 * equal `verdict`. A change to one reducer that is not made to the other
 * breaks a row on one side.
 */
import { describe, expect, it } from 'vitest'

import type { RunIntelligence } from '@/services/runIntelligenceService'
import type { RunVerdictItem } from '@/types/runs'
import * as verdictModule from './aiVerdict'
import {
  aiVerdictFromBatch, aiVerdictOf, NO_VERDICT_TITLE, UNAVAILABLE_RUN_TITLE, type AiVerdict,
} from './aiVerdict'

type Payload = Record<string, unknown>
interface VerdictCase {
  name: string
  full: Payload
  slim: Payload
  verdict: AiVerdict
  recommendation: string | null
}

const PUBLISHED_VER = { status: 'passed', failures: [], checked_at: '2026-10-01T00:00:00Z' }
const PUBLISHED_ATT = { status: 'published', verification_status: 'passed', attempt: 2 }
const SLIM_PUBLISHED_VER = { status: 'passed' }
const SLIM_PUBLISHED_ATT = { status: 'published', verification_status: 'passed' }
const verdict = (recommendation: 'GO' | 'CONDITIONAL_GO' | 'NO_GO'): AiVerdict => ({ recommendation, review: null })
const none = (reason: 'not-analysed' | 'no-recommendation' | 'not-verified'): AiVerdict => ({ recommendation: null, reason })

const VERDICT_CASES: VerdictCase[] = [
  { // 1
    name: 'verified published report beats a NO_GO release decision',
    full: {
      intelligence_available: true,
      release_decision: { recommendation: 'NO_GO', confidence: 0.7 },
      structured_summary: {
        decision_intelligence: {
          status: 'complete',
          verification: { status: 'passed', checks: 4 },
          release_decision: { recommendation: 'GO', rationale: 'All gates green.' },
        },
        decision_report_verification: PUBLISHED_VER,
        latest_decision_attempt: PUBLISHED_ATT,
        executive: { headline: 'Ready' },
      },
      top_analyses: [{ test_name: 'test_login' }],
      run: { id: 'r1', failed_tests: 3 },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'passed' },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: SLIM_PUBLISHED_VER,
        latest_decision_attempt: SLIM_PUBLISHED_ATT,
      },
      release_decision: { recommendation: 'NO_GO' },
      intelligence_available: true,
      top_analyses: [true],
    },
    verdict: verdict('GO'),
    recommendation: 'GO',
  },
  { // 2
    name: 'verified report whose publication was rejected is still shown',
    full: {
      release_decision: { recommendation: 'GO' },
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'passed' },
          release_decision: { recommendation: 'CONDITIONAL_GO' },
        },
        decision_report_verification: { status: 'failed', failures: ['stale'] },
        latest_decision_attempt: { status: 'rejected', verification_status: 'failed' },
      },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'passed' },
          release_decision: { recommendation: 'CONDITIONAL_GO' },
        },
        decision_report_verification: { status: 'failed' },
        latest_decision_attempt: { status: 'rejected', verification_status: 'failed' },
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: verdict('CONDITIONAL_GO'),
    recommendation: 'CONDITIONAL_GO',
  },
  { // 3
    name: 'a pending envelope is not displayable and never falls back',
    full: {
      intelligence_available: true,
      release_decision: { recommendation: 'GO' },
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'pending' },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: { status: 'pending' },
      },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'pending' },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: { status: 'pending' },
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: true,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
  { // 4
    name: 'no structured summary: the release decision answers',
    full: {
      intelligence_available: true,
      structured_summary: null,
      release_decision: { recommendation: 'NO_GO', reasons: ['3 blockers'] },
      top_analyses: [],
    },
    slim: {
      structured_summary: null,
      release_decision: { recommendation: 'NO_GO' },
      intelligence_available: true,
      top_analyses: [],
    },
    verdict: verdict('NO_GO'),
    recommendation: 'NO_GO',
  },
  { // 5
    name: 'a release decision outside the vocabulary is no verdict',
    full: {
      structured_summary: { executive: {} },
      release_decision: { recommendation: 'MAYBE' },
    },
    slim: {
      structured_summary: {
        decision_intelligence: null,
        decision_report_verification: null,
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'MAYBE' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: none('no-recommendation'),
    recommendation: null,
  },
  { // 6
    name: 'an EMPTY report object is truthy: an envelope, no fallback',
    full: {
      structured_summary: { decision_intelligence: {} },
      release_decision: { recommendation: 'GO' },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: null },
          release_decision: { recommendation: null },
        },
        decision_report_verification: null,
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
  { // 7
    name: 'an empty-string report is falsy: the release decision answers',
    full: {
      structured_summary: { decision_intelligence: '' },
      release_decision: { recommendation: 'GO' },
    },
    slim: {
      structured_summary: {
        decision_intelligence: null,
        decision_report_verification: null,
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: verdict('GO'),
    recommendation: 'GO',
  },
  { // 8
    name: 'a non-empty string report is truthy: [] in the slim',
    full: {
      structured_summary: { decision_intelligence: 'x' },
      release_decision: { recommendation: 'GO' },
    },
    slim: {
      structured_summary: {
        decision_intelligence: [],
        decision_report_verification: null,
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
  { // 9
    name: 'a zero report is falsy: the release decision answers',
    full: {
      structured_summary: { decision_intelligence: 0 },
      release_decision: { recommendation: 'CONDITIONAL_GO' },
    },
    slim: {
      structured_summary: {
        decision_intelligence: null,
        decision_report_verification: null,
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'CONDITIONAL_GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: verdict('CONDITIONAL_GO'),
    recommendation: 'CONDITIONAL_GO',
  },
  { // 10
    name: 'a report of 1 is truthy: an envelope, no fallback',
    full: {
      structured_summary: { decision_intelligence: 1 },
      release_decision: { recommendation: 'NO_GO' },
    },
    slim: {
      structured_summary: {
        decision_intelligence: [],
        decision_report_verification: null,
        latest_decision_attempt: null,
      },
      release_decision: { recommendation: 'NO_GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
  { // 11
    name: 'a verification status of the number 1 is never passed',
    full: {
      release_decision: { recommendation: 'GO' },
      structured_summary: {
        decision_intelligence: {
          verification: { status: 1 },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: PUBLISHED_VER,
        latest_decision_attempt: PUBLISHED_ATT,
      },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: 1 },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: SLIM_PUBLISHED_VER,
        latest_decision_attempt: SLIM_PUBLISHED_ATT,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
  { // 12
    name: 'a verification status of ["passed"] is never passed',
    full: {
      release_decision: { recommendation: 'GO' },
      structured_summary: {
        decision_intelligence: {
          verification: { status: ['passed'] },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: PUBLISHED_VER,
        latest_decision_attempt: PUBLISHED_ATT,
      },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: ['passed'] },
          release_decision: { recommendation: 'GO' },
        },
        decision_report_verification: SLIM_PUBLISHED_VER,
        latest_decision_attempt: SLIM_PUBLISHED_ATT,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: false,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
  { // 13
    name: 'no analyses, intelligence_available 0: not analysed',
    full: { intelligence_available: 0, top_analyses: [], run: { id: 'r13' } },
    slim: {
      structured_summary: null,
      release_decision: null,
      intelligence_available: false,
      top_analyses: [],
    },
    verdict: none('not-analysed'),
    recommendation: null,
  },
  { // 14
    name: 'a list of analyses: analysed, no recommendation',
    full: { top_analyses: [{ test_name: 'test_a', failure_category: 'PRODUCT_BUG' }] },
    slim: {
      structured_summary: null,
      release_decision: null,
      intelligence_available: false,
      top_analyses: [true],
    },
    verdict: none('no-recommendation'),
    recommendation: null,
  },
  { // 15
    name: 'a string of analyses has a length too: analysed',
    full: { top_analyses: 'abc', release_decision: null },
    slim: {
      structured_summary: null,
      release_decision: null,
      intelligence_available: false,
      top_analyses: [true],
    },
    verdict: none('no-recommendation'),
    recommendation: null,
  },
  { // 16
    name: 'a verified report with a null recommendation is no verdict, no fallback',
    full: {
      intelligence_available: true,
      release_decision: { recommendation: 'GO' },
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'passed' },
          release_decision: { recommendation: null },
        },
        decision_report_verification: PUBLISHED_VER,
        latest_decision_attempt: PUBLISHED_ATT,
      },
    },
    slim: {
      structured_summary: {
        decision_intelligence: {
          verification: { status: 'passed' },
          release_decision: { recommendation: null },
        },
        decision_report_verification: SLIM_PUBLISHED_VER,
        latest_decision_attempt: SLIM_PUBLISHED_ATT,
      },
      release_decision: { recommendation: 'GO' },
      intelligence_available: true,
      top_analyses: null,
    },
    verdict: none('not-verified'),
    recommendation: null,
  },
]

const asIntel = (payload: Payload) => payload as Partial<RunIntelligence>

describe('VERDICT_CASES — one reducer over the full payload and the slim one (contract §1.2)', () => {
  it("has the contract's sixteen rows (the backend copy asserts the same count)", () => {
    expect(VERDICT_CASES).toHaveLength(16)
    expect(new Set(VERDICT_CASES.map((c) => c.name)).size).toBe(16)
  })

  it.each(VERDICT_CASES.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    expect(aiVerdictOf(asIntel(c.full))).toEqual(c.verdict)
    expect(aiVerdictOf(asIntel(c.slim))).toEqual(c.verdict)
    // The pill the column shows is the `ai_recommendation` the filter reads.
    expect(c.verdict.recommendation).toBe(c.recommendation)
  })
})

describe("aiVerdictOf — the recommendation the run's Analysis tab shows", () => {
  it('reads the persisted release decision when there is no decision report', () => {
    expect(aiVerdictOf({ intelligence_available: true, release_decision: { recommendation: 'NO_GO' } } as never))
      .toEqual({ recommendation: 'NO_GO', review: null })
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

  it('makes nothing up: no payload or an analysis without a recommendation is "—"', () => {
    expect(aiVerdictOf(null)).toEqual({ recommendation: null, reason: 'not-analysed' })
    expect(aiVerdictOf({} as never)).toEqual({ recommendation: null, reason: 'not-analysed' })
    expect(aiVerdictOf({ intelligence_available: true, release_decision: null } as never))
      .toEqual({ recommendation: null, reason: 'no-recommendation' })
  })

  it('every "—" has a reason a reader can hover', () => {
    for (const reason of ['in-progress', 'not-analysed', 'not-summarised', 'no-recommendation', 'not-verified', 'unavailable'] as const) {
      expect(NO_VERDICT_TITLE[reason]).toMatch(/^(No AI verdict|AI verdict unavailable)/)
    }
    expect(NO_VERDICT_TITLE['not-summarised']).toBe(
      "No AI verdict on this list yet: the run was analysed but its summary is not built. Open the run's analysis to build it.",
    )
  })
})

describe('aiVerdictFromBatch — a batch item becomes the cell\'s verdict (FAIL-04)', () => {
  const item = (over: Partial<RunVerdictItem> = {}): RunVerdictItem => ({
    run_id: 'r1', snapshot: true, stale: false, generated_at: '2026-10-15T10:00:00Z', analysed: true,
    verdict_inputs: VERDICT_CASES[3].slim, review: { state: 'accepted' },
    ...over,
  })

  it('no snapshot but a finished analysis: not summarised (open the run\'s analysis)', () => {
    expect(aiVerdictFromBatch(item({ snapshot: false, analysed: true, verdict_inputs: null, review: null }), false))
      .toEqual({ recommendation: null, reason: 'not-summarised' })
  })

  it('no snapshot and no analysis: not analysed', () => {
    expect(aiVerdictFromBatch(item({ snapshot: false, analysed: false, verdict_inputs: null, review: null }), false))
      .toEqual({ recommendation: null, reason: 'not-analysed' })
  })

  it('a snapshot: the slim payload through aiVerdictOf, the review state attached (pending_review → draft)', () => {
    expect(aiVerdictFromBatch(item(), false)).toEqual({ recommendation: 'NO_GO', review: null })
    expect(aiVerdictFromBatch(item({ review: { state: 'pending_review' } }), false)).toEqual({ recommendation: 'NO_GO', review: 'draft' })
    expect(aiVerdictFromBatch(item({ review: { state: 'rejected' } }), false)).toEqual({ recommendation: 'NO_GO', review: 'rejected' })
    // Every slim of the table gives the same verdict through the batch path.
    for (const c of VERDICT_CASES) {
      expect(aiVerdictFromBatch(item({ verdict_inputs: c.slim, review: null }), false)).toEqual(c.verdict)
    }
  })

  it('an id the batch answered as unavailable: "unavailable", with its own title; a missing item is a failed answer', () => {
    expect(aiVerdictFromBatch(undefined, true)).toEqual({ recommendation: null, reason: 'unavailable', title: UNAVAILABLE_RUN_TITLE })
    expect(UNAVAILABLE_RUN_TITLE).toBe('AI verdict unavailable: the run is not available to you')
    expect(aiVerdictFromBatch(undefined, false)).toEqual({ recommendation: null, reason: 'unavailable' })
  })

  it('the per-run fetching is gone: no throttle, no per-run request, no per-run hook', () => {
    for (const removed of ['fetchAiVerdict', 'MAX_IN_FLIGHT', 'useRunAiVerdict', 'limited']) {
      expect(removed in verdictModule, removed).toBe(false)
    }
  })
})
