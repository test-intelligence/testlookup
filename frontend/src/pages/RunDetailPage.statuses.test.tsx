/**
 * The run header must account for every test in the run.
 *
 * Found during UAT of the ingest journey. A JUnit report of 6 tests
 * (2 pass / 2 fail / 1 `<error>` → BROKEN / 1 skip) was uploaded through the UI
 * and stored perfectly — but the run header rendered:
 *
 *     "2 passed   2 failed   1 skipped   / 6 total"
 *
 * Five of six accounted for. The infrastructure error was invisible on the
 * primary screen a user reads about a run, and the arithmetic visibly did not
 * close — which undermines trust in every other number on the page.
 *
 * `RunDetailPage` rendered exactly four spans: passed / failed / skipped /
 * total. Same vocabulary-subset class as the integration-health trends bug
 * (F-074) and the "New failures (24h)" KPI (F-078), this time in the UI, and it
 * also missed `unknown_tests` which migration 0118 had added.
 *
 * These tests assert the invariant, not the markup: **the rendered per-status
 * buckets must sum to `total_tests`**. A new status that gains a column in the
 * data but not in the header fails here.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import RunDetailPage from './RunDetailPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useRuns', () => ({
  useRun: vi.fn(),
  useRuns: vi.fn(),
  useTestCases: vi.fn(),
  // Phase 4 verdicts. Returns empty so these tests assert the page renders
  // identically WITHOUT attribution — the annotation must never be load-bearing.
  useRunAttribution: vi.fn(() => ({ data: { items: [], total: 0 } })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('@/services/runsService', () => ({
  runsService: {
    setRelease: vi.fn(), get: vi.fn(), list: vi.fn(), listTests: vi.fn(), getTest: vi.fn(),
  },
}))

vi.mock('swr', () => ({ default: vi.fn(), mutate: vi.fn() }))

/** The exact run produced by the UAT upload that exposed this. */
const UAT_RUN = {
  id: 'run-1',
  build_number: 'upload-uat',
  jenkins_job: 'job-1',
  created_at: '2026-08-11T07:44:44Z',
  release_name: null,
  passed_tests: 2,
  failed_tests: 2,
  skipped_tests: 1,
  broken_tests: 1,
  unknown_tests: 0,
  total_tests: 6,
  status: 'failed',
}

async function renderRun(run: Record<string, unknown>) {
  const { useRun, useRuns, useTestCases } = await import('@/hooks/useRuns')
  const useSWR = (await import('swr')).default as ReturnType<typeof vi.fn>
  ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
  ;(useRun as ReturnType<typeof vi.fn>).mockReturnValue({ data: run })
  ;(useTestCases as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: [], pages: 1, total: 0 }, isLoading: false, error: undefined,
  })
  useSWR.mockReturnValue({ data: undefined, isLoading: false })

  render(
    <MemoryRouter initialEntries={['/runs/run-1']}>
      <Routes>
        <Route path="/runs" element={<div>Runs List</div>} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
  await screen.findByText(/Run #upload-uat/i)
}

/** Sum every "<n> <label>" bucket rendered in the header. */
function renderedBucketTotal(): number {
  const labels = ['passed', 'failed', 'skipped', 'broken', 'unrecognised']
  return labels.reduce((sum, label) => {
    const el = screen.queryByText(new RegExp(`^\\d+\\s+${label}$`, 'i'))
    if (!el) return sum
    return sum + Number((el.textContent ?? '').trim().split(/\s+/)[0])
  }, 0)
}

describe('RunDetailPage header accounts for every test', () => {
  it('shows the broken count so the buckets reconcile with the total', async () => {
    await renderRun(UAT_RUN)
    expect(screen.getByText(/^1 broken$/i)).toBeTruthy()
    expect(renderedBucketTotal()).toBe(UAT_RUN.total_tests)
  })

  it('shows unrecognised-status results too (migration 0118)', async () => {
    const run = { ...UAT_RUN, unknown_tests: 2, total_tests: 8 }
    await renderRun(run)
    expect(screen.getByText(/^2 unrecognised$/i)).toBeTruthy()
    expect(renderedBucketTotal()).toBe(run.total_tests)
  })

  it('stays uncluttered for an all-green run', async () => {
    const run = {
      ...UAT_RUN, passed_tests: 10, failed_tests: 0, skipped_tests: 0,
      broken_tests: 0, unknown_tests: 0, total_tests: 10, status: 'passed',
    }
    await renderRun(run)
    // Zero-valued broken/unknown are hidden; the counts still reconcile.
    // Anchor to the bucket shape "<n> <label>" — a bare /broken$/ also matches
    // unrelated chrome elsewhere on the page.
    expect(screen.queryByText(/^\d+ broken$/i)).toBeNull()
    expect(screen.queryByText(/^\d+ unrecognised$/i)).toBeNull()
    expect(renderedBucketTotal()).toBe(run.total_tests)
  })

  it('tolerates a run whose payload predates the unknown_tests column', async () => {
    const { unknown_tests: _omitted, ...legacy } = UAT_RUN
    await renderRun(legacy)
    expect(renderedBucketTotal()).toBe(legacy.total_tests)
  })
})
