import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CasesTableBody } from './TestManagementPage'
import type { ManagedTestCase } from '@/types/test-management'

/**
 * Regression cover for UX audit issue 2: the cases table carried seven fixed
 * columns (~660px) plus Title's `minWidth: 280`, demanding ~940px inside a
 * panel shared with the filters — Title collapsed to a few characters and the
 * trailing columns scrolled out of the `overflow-x-auto` wrapper. ID, Priority
 * and Automation now ride a meta line under the title.
 */
const CASE = {
  id: '8a2f4100-0000-4000-8000-000000000001',
  title: 'Verify checkout with expired saved card falls back to manual entry',
  status: 'active',
  priority: 'high',
  owner: 'Priya Raghavan',
  is_automated: true,
  source: 'catalog',
  test_type: 'end_to_end',
  suite_name: 'checkout-e2e',
  tags: ['smoke'],
  last_execution_status: 'passed',
  last_executed_at: '2026-08-28T14:32:00Z',
} as unknown as ManagedTestCase

const renderTable = () =>
  render(
    <CasesTableBody
      cases={[CASE]}
      onRowClick={vi.fn()}
      onDeprecate={vi.fn()}
      onPromoted={vi.fn()}
      lifecycleV2={false}
    />,
  )

describe('Test Management cases table columns', () => {
  it('drops the ID, Priority and Automation columns', () => {
    renderTable()
    const headers = screen.getAllByRole('columnheader').map(h => h.textContent?.trim())
    expect(headers).not.toContain('ID')
    expect(headers).not.toContain('Priority')
    expect(headers).not.toContain('Automation')
  })

  it('keeps five columns with Test case as the only flexible one', () => {
    renderTable()
    const headers = screen.getAllByRole('columnheader')
    expect(headers).toHaveLength(5)
    expect(headers.map(h => h.textContent?.trim())).toEqual(
      ['Test case', 'Status', 'Owner', 'Last run', ''],
    )
    // The title column must carry no fixed width — it absorbs the slack.
    expect(headers[0].getAttribute('width')).toBeNull()
    expect((headers[0] as HTMLElement).style.width).toBe('')
  })

  it('folds the demoted fields into the meta line rather than dropping them', () => {
    // Demoting a column must relocate its data, not discard it.
    renderTable()
    expect(screen.getByText('TC-8A2F41')).toBeInTheDocument()   // ID
    expect(screen.getByText('P1')).toBeInTheDocument()          // Priority
    expect(screen.getByText('Automated')).toBeInTheDocument()   // Automation
  })

  it('gives the full title a tooltip once it truncates', () => {
    renderTable()
    expect(screen.getByTitle(CASE.title)).toBeInTheDocument()
  })
})
