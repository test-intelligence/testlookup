/**
 * W28 TCM-03: the bulk bar. One request per Apply, the per-case answer in the
 * bar, the retire moves only for QA leads and admins (the AMENDMENT: the host
 * passes canRetire = hasRole('QA_LEAD')), the owner list read only on demand.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createRef } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ManagedTestCase, TestCaseBulkResponse } from '@/types/test-management'

const bulkUpdate = vi.fn()
const useProjectMembers = vi.fn()

vi.mock('@/services/testCaseEditingService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testCaseEditingService')>()
  return { ...actual, testCaseEditingService: { bulkUpdate: (...args: unknown[]) => bulkUpdate(...args) } }
})
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: (...args: unknown[]) => useProjectMembers(...args),
}))

import BulkActionBar from './BulkActionBar'

function tc(id: string, title: string, version: number, status: ManagedTestCase['status'] = 'draft'): ManagedTestCase {
  return {
    id, project_id: 'p-1', title, test_type: 'functional', priority: 'medium', severity: 'major', test_suite_id: null,
    status, version, is_automated: false, automation_status: 'not_automated', ai_generated: false,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', source: 'managed',
  }
}

const A = tc('aaaaaaaa-0000-4000-8000-000000000001', 'Login works', 1)
const B = tc('bbbbbbbb-0000-4000-8000-000000000002', 'Refund flow', 2)
const C = tc('cccccccc-0000-4000-8000-000000000003', 'Approved checkout', 4, 'approved')

const MEMBERS = [
  { id: 'm-1', user_id: 'u-priya', project_id: 'p-1', role: 'QA_ENGINEER', created_at: 't', email: 'p@x', username: 'priya', full_name: 'Priya Shah' },
  { id: 'm-2', user_id: 'u-ana', project_id: 'p-1', role: 'TESTER', created_at: 't', email: 'a@x', username: 'ana', full_name: null },
]

function renderBar(over: Partial<React.ComponentProps<typeof BulkActionBar>> = {}) {
  const props: React.ComponentProps<typeof BulkActionBar> = {
    projectId: 'p-1',
    selected: [A, B, C],
    pageSelectableCount: 25,
    lifecycleV2: true,
    canRetire: false,
    suiteOptions: ['Checkout', 'Payments'],
    onSelectPage: vi.fn(),
    onClear: vi.fn(),
    onApplied: vi.fn(),
    ...over,
  }
  const view = render(<BulkActionBar {...props} />)
  return { props, view, bar: () => screen.getByRole('toolbar', { name: 'Bulk edit' }) }
}

const action = () => screen.getByRole('combobox', { name: 'Action' })
const apply = () => screen.getByRole('button', { name: /^(Apply|Applying…)$/ })
const optionLabels = (select: HTMLElement) => Array.from((select as HTMLSelectElement).options).map((o) => o.textContent)

function answer(results: TestCaseBulkResponse['results']): TestCaseBulkResponse {
  const n = (o: string) => results.filter((r) => r.outcome === o).length
  return { action: 'transition', requested: results.length, changed: n('changed'), unchanged: n('unchanged'), skipped: n('skipped'), not_found: n('not_found'), results }
}

describe('BulkActionBar', () => {
  beforeEach(() => {
    bulkUpdate.mockReset()
    useProjectMembers.mockReset()
    useProjectMembers.mockImplementation((projectId: string | null) => ({ data: projectId ? MEMBERS : undefined, isLoading: false }))
  })

  it('is an in-page toolbar with the count, Select all on this page and Clear', () => {
    const { props, bar } = renderBar()
    expect(bar()).toHaveAttribute('data-bulk-bar')
    expect(document.querySelector('[role="dialog"], [aria-modal]')).toBeNull()
    expect(within(bar()).getByText('3 selected')).toBeInTheDocument()
    fireEvent.click(within(bar()).getByRole('button', { name: 'Select all 25 on this page' }))
    expect(props.onSelectPage).toHaveBeenCalledTimes(1)
    fireEvent.click(within(bar()).getByRole('button', { name: 'Clear' }))
    expect(props.onClear).toHaveBeenCalledTimes(1)
    // Every row of the bar wraps; nothing has a fixed width.
    for (const row of bar().querySelectorAll('div')) {
      if (row.className.includes('flex')) expect(row.className).toContain('flex-wrap')
    }
    expect(bar().innerHTML).not.toMatch(/\bw-\d|\bw-\[/)
  })

  it('at 0 selected says how to select, and Apply stays disabled whatever is chosen', () => {
    renderBar({ selected: [] })
    expect(screen.getByText('Select cases with the checkboxes to edit them together.')).toBeInTheDocument()
    fireEvent.change(action(), { target: { value: 'set_priority' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Priority' }), { target: { value: 'high' } })
    expect(apply()).toBeDisabled()
  })

  it('the actions per lifecycleV2', () => {
    const { view } = renderBar()
    expect(optionLabels(action())).toEqual(['Choose an action…', 'Change status', 'Owner', 'Priority', 'Add tags', 'Remove tags', 'Move to suite'])
    view.unmount()
    renderBar({ lifecycleV2: false })
    expect(optionLabels(action())).toEqual(['Choose an action…', 'Owner', 'Priority', 'Add tags', 'Remove tags', 'Move to suite'])
  })

  it('Change status offers Deprecate / Archive / Reinstate only when canRetire (QA leads and admins)', () => {
    const { view } = renderBar({ canRetire: false })
    fireEvent.change(action(), { target: { value: 'transition' } })
    expect(optionLabels(screen.getByRole('combobox', { name: 'New status' }))).toEqual([
      'Choose a status change…', 'Make active', 'Back to draft', 'Needs update', 'Request review',
    ])
    view.unmount()
    renderBar({ canRetire: true })
    fireEvent.change(action(), { target: { value: 'transition' } })
    expect(optionLabels(screen.getByRole('combobox', { name: 'New status' }))).toEqual([
      'Choose a status change…', 'Make active', 'Back to draft', 'Needs update', 'Request review', 'Deprecate', 'Archive', 'Reinstate',
    ])
  })

  it('Apply is disabled until the field is set; a retire move requires its reason; the exact bulkUpdate call', async () => {
    bulkUpdate.mockResolvedValue(answer([{ id: A.id, outcome: 'changed' }, { id: B.id, outcome: 'changed' }, { id: C.id, outcome: 'changed' }]))
    const { props } = renderBar({ canRetire: true })
    expect(apply()).toBeDisabled()
    fireEvent.change(action(), { target: { value: 'transition' } })
    expect(apply()).toBeDisabled()
    fireEvent.change(screen.getByRole('combobox', { name: 'New status' }), { target: { value: 'deprecate' } })
    const reason = screen.getByRole('textbox', { name: 'Reason' })
    expect(reason).toBeRequired()
    expect(apply()).toBeDisabled()
    fireEvent.change(reason, { target: { value: '   ' } })
    expect(apply()).toBeDisabled()
    fireEvent.change(reason, { target: { value: ' Replaced by the v2 flow ' } })
    expect(apply()).toBeEnabled()
    fireEvent.click(apply())
    await waitFor(() => expect(props.onApplied).toHaveBeenCalledTimes(1))
    expect(bulkUpdate).toHaveBeenCalledTimes(1)
    expect(bulkUpdate).toHaveBeenCalledWith({
      project_id: 'p-1',
      case_ids: [A.id, B.id, C.id],
      expected_versions: { [A.id]: 1, [B.id]: 2, [C.id]: 4 },
      action: 'transition',
      transition: 'deprecate',
      reason: 'Replaced by the v2 flow',
    })
  })

  it('Make active needs no reason and sends no reason field', async () => {
    bulkUpdate.mockResolvedValue(answer([{ id: A.id, outcome: 'changed' }, { id: B.id, outcome: 'changed' }, { id: C.id, outcome: 'changed' }]))
    renderBar()
    fireEvent.change(action(), { target: { value: 'transition' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'New status' }), { target: { value: 'activate' } })
    expect(screen.queryByRole('textbox', { name: 'Reason' })).toBeNull()
    fireEvent.click(apply())
    await screen.findByRole('status')
    expect(bulkUpdate.mock.calls[0][0]).toEqual({
      project_id: 'p-1', case_ids: [A.id, B.id, C.id], expected_versions: { [A.id]: 1, [B.id]: 2, [C.id]: 4 },
      action: 'transition', transition: 'activate',
    })
    expect(screen.getByRole('status')).toHaveTextContent(/^3 changed$/)
  })

  it('shows Applying… while the request runs, then the summary and the skipped list by title (React text)', async () => {
    let resolve: (value: TestCaseBulkResponse) => void = () => {}
    bulkUpdate.mockReturnValue(new Promise<TestCaseBulkResponse>((r) => { resolve = r }))
    const evil = tc('dddddddd-0000-4000-8000-000000000004', '<img src=x onerror=alert(1)>', 1)
    const { props } = renderBar({ selected: [A, B, evil] })
    fireEvent.change(action(), { target: { value: 'set_priority' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Priority' }), { target: { value: 'high' } })
    fireEvent.click(apply())
    expect(screen.getByRole('button', { name: 'Applying…' })).toBeDisabled()
    const result = answer([
      { id: A.id, outcome: 'changed', version: 2 },
      { id: B.id, outcome: 'skipped', reason: 'Changed since you selected it (v2 → v3)' },
      { id: evil.id, outcome: 'skipped', reason: 'Reinstate it to edit it' },
    ])
    resolve(result)
    expect(await screen.findByRole('status')).toHaveTextContent(/^1 changed · 2 skipped$/)
    const skipped = document.querySelector('[data-bulk-skipped]') as HTMLElement
    expect(within(skipped).getByText('Skipped')).toBeInTheDocument()
    expect(Array.from(skipped.querySelectorAll('li')).map((li) => li.textContent)).toEqual([
      'Refund flow — Changed since you selected it (v2 → v3)',
      '<img src=x onerror=alert(1)> — Reinstate it to edit it',
    ])
    expect(skipped.querySelector('img')).toBeNull()
    expect(props.onApplied).toHaveBeenCalledWith(result)
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled()
  })

  it('a not-found id is listed by its first 8 characters, no longer in this project', async () => {
    bulkUpdate.mockResolvedValue(answer([{ id: A.id, outcome: 'changed' }, { id: B.id, outcome: 'not_found' }]))
    renderBar({ selected: [A, B] })
    fireEvent.change(action(), { target: { value: 'add_tags' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Tags' }), { target: { value: 'regression' } })
    fireEvent.click(apply())
    expect(await screen.findByRole('status')).toHaveTextContent('1 changed · 1 not found')
    expect(document.querySelector('[data-bulk-skipped] li')?.textContent).toBe('bbbbbbbb — no longer in this project')
  })

  it('a failed request is one inline sentence; the selection is kept (onApplied not called)', async () => {
    bulkUpdate.mockRejectedValue({ response: { status: 403, data: { detail: 'This action requires QA_LEAD or above' } } })
    const { props } = renderBar({ canRetire: true })
    fireEvent.change(action(), { target: { value: 'transition' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'New status' }), { target: { value: 'archive' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Reason' }), { target: { value: 'Retired product' } })
    fireEvent.click(apply())
    expect(await screen.findByRole('alert')).toHaveTextContent('This action requires QA_LEAD or above')
    expect(props.onApplied).not.toHaveBeenCalled()
    expect(props.onClear).not.toHaveBeenCalled()
    expect(screen.getByText('3 selected')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Reason' })).toHaveValue('Retired product')
  })

  it('a 500 without a sentence falls back to the bar\'s own', async () => {
    bulkUpdate.mockRejectedValue({ response: { status: 500, data: {} } })
    renderBar()
    fireEvent.change(action(), { target: { value: 'move_to_suite' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Suite' }), { target: { value: ' Checkout ' } })
    fireEvent.click(apply())
    expect(await screen.findByRole('alert')).toHaveTextContent('The bulk edit could not be applied.')
    expect(bulkUpdate.mock.calls[0][0]).toMatchObject({ action: 'move_to_suite', suite_name: 'Checkout' })
  })

  it('the suite field suggests the page\'s suite names', () => {
    renderBar()
    fireEvent.change(action(), { target: { value: 'move_to_suite' } })
    const input = screen.getByRole('combobox', { name: 'Suite' })
    const list = document.getElementById(input.getAttribute('list') as string) as HTMLDataListElement
    expect(Array.from(list.options).map((o) => o.value)).toEqual(['Checkout', 'Payments'])
  })

  it('the owner list is read only when Owner is chosen; Unassigned sends null', async () => {
    bulkUpdate.mockResolvedValue(answer([{ id: A.id, outcome: 'changed' }]))
    renderBar({ selected: [A] })
    expect(useProjectMembers.mock.calls.every(([projectId]) => projectId === null)).toBe(true)
    fireEvent.change(action(), { target: { value: 'set_owner' } })
    expect(useProjectMembers).toHaveBeenLastCalledWith('p-1')
    const owner = screen.getByRole('combobox', { name: 'Owner' })
    expect(optionLabels(owner)).toEqual(['Choose an owner…', 'Unassigned', 'ana', 'Priya Shah'])
    expect(apply()).toBeDisabled()
    fireEvent.change(owner, { target: { value: 'u-priya' } })
    fireEvent.click(apply())
    await screen.findByRole('status')
    expect(bulkUpdate.mock.calls[0][0]).toEqual({
      project_id: 'p-1', case_ids: [A.id], expected_versions: { [A.id]: 1 }, action: 'set_owner', assignee_id: 'u-priya',
    })
    fireEvent.change(owner, { target: { value: 'unassigned' } })
    expect(screen.queryByRole('status'), 'a new choice clears the last answer').toBeNull()
    fireEvent.click(apply())
    await screen.findByRole('status')
    expect(bulkUpdate.mock.calls[1][0]).toMatchObject({ action: 'set_owner', assignee_id: null })
    fireEvent.change(action(), { target: { value: 'set_priority' } })
    expect(useProjectMembers).toHaveBeenLastCalledWith(null)
  })

  it('actionRef reaches the Action select (⌘E / Ctrl+E focuses it)', () => {
    const ref = createRef<HTMLSelectElement>()
    renderBar({ actionRef: ref })
    expect(ref.current).toBe(action())
    ref.current?.focus()
    expect(action()).toHaveFocus()
  })
})
