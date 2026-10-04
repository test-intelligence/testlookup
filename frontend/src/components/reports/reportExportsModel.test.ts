import { describe, expect, it } from 'vitest'
import type { ReportExport } from '@/services/summaryReportService'
import { formatBytes, isActive, newlyFinished, scopeSummary } from './reportExportsModel'

function exp(overrides: Partial<ReportExport> = {}): ReportExport {
  return {
    id: 'e1',
    project_id: 'p1',
    format: 'pdf',
    status: 'queued',
    attempts: 0,
    params: { mode: 'window', days: 30, release_ids: [], suite_names: [] },
    filename: null,
    size_bytes: null,
    error: null,
    requested_at: '2026-10-03T10:00:00Z',
    started_at: null,
    finished_at: null,
    expires_at: '2026-10-10T10:00:00Z',
    retryable: false,
    download_url: null,
    ...overrides,
  }
}

describe('reportExportsModel (VIZ-607)', () => {
  it('treats queued and running as still working', () => {
    expect(isActive(exp({ status: 'queued' }))).toBe(true)
    expect(isActive(exp({ status: 'running' }))).toBe(true)
    expect(isActive(exp({ status: 'completed' }))).toBe(false)
    expect(isActive(exp({ status: 'failed' }))).toBe(false)
  })

  it('reports nothing on the first load: finished exports there are not news', () => {
    expect(newlyFinished(undefined, [exp({ status: 'completed' })])).toEqual({ completed: [], failed: [] })
  })

  it('reports an export the moment it leaves queued/running, once', () => {
    const before = [exp({ id: 'a', status: 'running' }), exp({ id: 'b', status: 'queued' }), exp({ id: 'c', status: 'completed' })]
    const after = [exp({ id: 'a', status: 'completed' }), exp({ id: 'b', status: 'failed' }), exp({ id: 'c', status: 'completed' })]
    const news = newlyFinished(before, after)
    expect(news.completed.map(e => e.id)).toEqual(['a'])
    expect(news.failed.map(e => e.id)).toEqual(['b'])
    expect(newlyFinished(after, after)).toEqual({ completed: [], failed: [] })
  })

  it('does not report an export it never saw working (e.g. a new row already done)', () => {
    expect(newlyFinished([], [exp({ status: 'completed' })]).completed).toEqual([])
  })

  it('summarises the scope in words', () => {
    expect(scopeSummary(exp())).toBe('PDF · last 30 days · all runs')
    expect(scopeSummary(exp({
      format: 'xlsx',
      params: { mode: 'latest', days: 1, release_ids: ['r1'], suite_names: ['a', 'b'] },
    }))).toBe('Excel · last 24 h · latest run per suite · 1 release · 2 suites')
  })

  it('formats sizes, and says nothing for an unmeasured one', () => {
    expect(formatBytes(null)).toBe('')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(48_000)).toBe('47 KB')
    expect(formatBytes(3_500_000)).toBe('3.3 MB')
  })
})
