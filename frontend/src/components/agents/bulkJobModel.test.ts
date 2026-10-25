/**
 * W14 AGT-12 (`W14-contract.md` §2.4 test 7): the bulk-job panel's words, and the "Re-trigger all matching"
 * filter — exactly the runs list's own request (`useRuns`' params, the project, the release), so the server
 * resolves the same runs the list shows.
 */
import { describe, expect, it } from 'vitest'
import type { BulkJob } from '@/types/agentOps'
import {
  BULK_RECORD_EXPIRED, bulkOutcome, bulkUntrackedNote, confirmSentence, filterFromPage, matchingLabel,
  progressLines,
} from './bulkJobModel'

/** The params `RunsPage` hands `useRuns` for a fully filtered list. */
const PAGE_PARAMS = {
  page: 1,
  size: 500,
  days: 7,
  status: 'FAILED',
  suite_name: ['checkout', 'auth'],
  q: 'build 42',
  branch: 'main',
  recommendation: 'NO_GO',
}

describe('filterFromPage — the list\'s own request', () => {
  it('mirrors every filter useRuns sends (lists for the repeatable ones), not the paging', () => {
    const filter = filterFromPage({ projectId: 'proj-1', release: 'rel-9', params: PAGE_PARAMS })
    expect(filter).toEqual({
      project_id: 'proj-1',
      release_id: ['rel-9'],
      days: 7,
      status: 'FAILED',
      suite_name: ['auth', 'checkout'],
      q: 'build 42',
      branch: ['main'],
      recommendation: 'NO_GO',
    })
    // Every filter key the list sends is in the run_filter (page / size are the list's paging).
    for (const key of Object.keys(PAGE_PARAMS).filter((k) => k !== 'page' && k !== 'size')) {
      expect(filter, key).toHaveProperty(key)
    }
  })

  it('All Projects: no project_id (and no release: a release belongs to one project)', () => {
    const filter = filterFromPage({ projectId: null, release: null, params: { page: 1, size: 500, days: 30 } })
    expect(filter).toEqual({ days: 30 })
    expect(filter).not.toHaveProperty('project_id')
    expect(filter).not.toHaveProperty('release_id')
  })

  it('an absent filter is not sent: an unfiltered list asks with its window only', () => {
    expect(filterFromPage({ projectId: 'p', release: [], params: { page: 1, size: 500, days: 14, q: '' } }))
      .toEqual({ project_id: 'p', days: 14 })
  })
})

describe('confirmSentence / matchingLabel', () => {
  it('names the matched count', () => {
    expect(confirmSentence(37)).toBe('Re-trigger the AI pipeline for 37 runs matching this filter?')
    expect(confirmSentence(1)).toBe('Re-trigger the AI pipeline for 1 run matching this filter?')
  })
  it('names the cap and the full count past 2,000', () => {
    expect(confirmSentence(5312)).toBe('Re-trigger the AI pipeline for 2,000 runs matching this filter, the newest 2,000 of 5,312?')
  })
  it('the ⋯ item carries the list\'s COUNT, "2,000+" past the cap', () => {
    expect(matchingLabel(37)).toBe('Re-trigger all matching (37)')
    expect(matchingLabel(2000)).toBe('Re-trigger all matching (2,000)')
    expect(matchingLabel(2001)).toBe('Re-trigger all matching (2,000+)')
  })
})

function job(over: Partial<BulkJob> = {}): BulkJob {
  return {
    bulk_job_id: 'b0000000-0000-4000-8000-000000000001',
    workflow_type: 'offline',
    source: 'selection',
    state: 'active',
    counters_source: 'redis',
    requested: 3,
    queued: 2,
    skipped_in_progress: 1,
    not_found: 0,
    matched: null,
    truncated: false,
    progress: { started: 2, in_progress: 1, completed: 1, passed: 1, failed: 0, not_started: 0 },
    note: null,
    ...over,
  }
}

describe('progressLines', () => {
  it('a selection: the request line and the progress line', () => {
    const lines = progressLines(job())
    expect(lines.request).toBe('3 requested · 2 queued · 1 already running · 0 not found')
    expect(lines.progress).toBe('1 completed · 1 passed · 0 failed · 1 running · 0 not started')
    expect(lines.stateLabel).toBe('Running')
    expect(lines.gauge).toEqual({ started: 2, queued: 2 })
    expect(lines.recordNote).toBeNull()
  })

  it('a filter: no "not found" (a filter names runs that exist); truncated names the matched count', () => {
    expect(progressLines(job({ source: 'filter', requested: 37, queued: 35, skipped_in_progress: 2, matched: 37 })).request)
      .toBe('37 requested · 35 queued · 2 already running')
    expect(progressLines(job({ source: 'filter', requested: 2000, queued: 2000, skipped_in_progress: 0, matched: 5312, truncated: true })).request)
      .toBe('5,312 matched, the newest 2,000 requested · 2,000 queued · 0 already running')
  })

  it('an expired request record: counted from the pipelines, said so — never zeros', () => {
    const lines = progressLines(job({
      counters_source: 'postgres', requested: null, queued: null, skipped_in_progress: null, not_found: null,
      progress: { started: 4, in_progress: 0, completed: 4, passed: 3, failed: 1, not_started: null },
    }))
    expect(lines.request).toBeNull()
    expect(lines.recordNote).toBe(BULK_RECORD_EXPIRED)
    expect(lines.progress).toBe('4 completed · 3 passed · 1 failed · 0 running')
    expect(lines.progress).not.toContain('not started')
    expect(lines.gauge).toBeNull()
  })

  it('the server\'s not-started note rides along; the state reads as words', () => {
    const note = '2 queued pipelines never started (a duplicate request, the cancelled job or a worker restart dropped them). None of them is counted as failed.'
    const lines = progressLines(job({ state: 'finished', note }))
    expect(lines.note).toBe(note)
    expect(lines.stateLabel).toBe('Finished')
    expect(progressLines(job({ state: 'cancelled' })).stateLabel).toBe('Cancelled')
  })
})

describe('bulkOutcome', () => {
  it('a job id opens the panel; tracking false says so; an older answer toasts', () => {
    expect(bulkOutcome({ queued: 3, bulk_job_id: 'job-1' } as never, 3)).toEqual({ kind: 'panel', jobId: 'job-1' })
    expect(bulkOutcome({ queued: 3, tracking: false, bulk_job_id: null } as never, 3))
      .toEqual({ kind: 'untracked', note: bulkUntrackedNote(3) })
    expect(bulkUntrackedNote(3)).toBe('Queued 3 pipelines. Progress cannot be tracked right now.')
    expect(bulkOutcome({ queued: 3 } as never, 5)).toEqual({ kind: 'toast', queued: 3 })
    expect(bulkOutcome(undefined, 5)).toEqual({ kind: 'toast', queued: 5 })
  })
})
