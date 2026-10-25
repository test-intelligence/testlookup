/**
 * W13 TCL-07: the suite page's latest-run cell shows the run's date.
 *
 * The cell keeps `run 1a2b3c4d` (the link) and adds a second line with
 * `formatCompactDateTime(last_seen_at)`, the full instant and the run id in
 * its `title`. No `last_seen_at` (an older API, or a run in no known project)
 * → no second line: never a guessed date.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { CanonicalTestCase } from '@/types/suites'
import { formatCompactDateTime } from '@/utils/formatters'
import SuiteTestsTable from './SuiteTestsTable'

function canonical(id: string, overrides: Partial<CanonicalTestCase> = {}): CanonicalTestCase {
  return {
    id, project_id: 'p1', test_suite_id: 's1', test_suite_name: 'Smoke', test_fingerprint: `fp-${id}`,
    test_name: `test_${id}`, class_name: null, status: 'active', source: 'execution', first_seen_run_id: null,
    last_seen_run_id: '1a2b3c4d-0000-4000-8000-000000000001', last_seen_test_case_id: null,
    deleted_at_run_id: null, managed_test_case_id: null, review_tag: null, tags: null, run_count: null,
    created_at: '2026-10-01T00:00:00Z', updated_at: null,
    ...overrides,
  } as CanonicalTestCase
}

function renderTable(cases: CanonicalTestCase[]) {
  return render(
    <MemoryRouter>
      <SuiteTestsTable
        cases={cases}
        stats={new Map()}
        noRunTitle="Not run in the last 30 days"
        canEdit={false}
        selectedIds={new Set()}
        onToggleOne={vi.fn()}
        onToggleAll={vi.fn()}
        onMove={vi.fn()}
      />
    </MemoryRouter>,
  )
}

const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement

describe('SuiteTestsTable latest-run date', () => {
  it('adds the run date under the run link, with the instant and the run id on hover', () => {
    const at = '2026-10-21T14:05:00Z'
    renderTable([canonical('dated', { last_seen_at: at, last_seen_test_case_id: 'result-1' })])
    const row = rowOf('test_dated')
    expect(within(row).getByRole('link', { name: /run 1a2b3c4d/ })).toHaveAttribute(
      'href', '/runs/1a2b3c4d-0000-4000-8000-000000000001/tests/result-1',
    )
    const line = row.querySelector('[data-last-seen-at]') as HTMLElement
    expect(line).toHaveTextContent(formatCompactDateTime(at))
    expect(line).toHaveAttribute('data-last-seen-at', at)
    expect(line).toHaveAttribute(
      'title', 'Latest run 2026-10-21T14:05:00.000Z · run 1a2b3c4d-0000-4000-8000-000000000001',
    )
  })

  it('keeps the date when the per-run result is gone (the link names the run)', () => {
    renderTable([canonical('gone', { last_seen_at: '2026-10-20T09:30:00Z' })])
    const row = rowOf('test_gone')
    expect(within(row).getByRole('link', { name: /run 1a2b3c4d/ })).toHaveAttribute(
      'href', '/runs/1a2b3c4d-0000-4000-8000-000000000001',
    )
    expect(row.querySelector('[data-last-seen-at]')).not.toBeNull()
  })

  it('draws no date line without last_seen_at, or without a run', () => {
    renderTable([
      canonical('undated', { last_seen_at: null }),
      canonical('legacy'),
      canonical('never', { last_seen_run_id: null, last_seen_at: '2026-10-20T09:30:00Z' }),
    ])
    expect(document.querySelectorAll('[data-last-seen-at]')).toHaveLength(0)
    // Nothing under the run link at all (no placeholder, no dash, no guessed date).
    for (const name of ['test_undated', 'test_legacy']) {
      const link = within(rowOf(name)).getByRole('link', { name: /run 1a2b3c4d/ })
      expect(link.parentElement?.children).toHaveLength(1)
      expect(link.parentElement?.textContent?.trim()).toBe('run 1a2b3c4d')
    }
  })
})
