/**
 * W13 TCL-06: a case's Requirements — the rows, Add with the three fields,
 * the 409 sentence inline, Remove asked in the row, no control for a viewer /
 * a retired case / All Projects, and a key click filtering by the NORMALISED key.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ManagedTestCase } from '@/types/test-management'
import type { RequirementLink, RequirementLinkList } from '@/types/test-library'

const addRequirement = vi.fn()
const removeRequirement = vi.fn()
const mutate = vi.fn()
const refreshLibraryHealth = vi.fn()
let answer: { data?: RequirementLinkList; error?: unknown; isLoading: boolean }
const useCaseRequirements = vi.fn((..._args: unknown[]) => ({ ...answer, mutate }))

vi.mock('@/hooks/useTestLibrary', () => ({
  useCaseRequirements: (...args: unknown[]) => useCaseRequirements(...args),
  refreshLibraryHealth: () => refreshLibraryHealth(),
}))
vi.mock('@/services/testLibraryService', () => ({
  testLibraryService: {
    addRequirement: (...args: unknown[]) => addRequirement(...args),
    removeRequirement: (...args: unknown[]) => removeRequirement(...args),
  },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

import CaseRequirementsSection from './CaseRequirementsSection'
import { REQUIREMENTS_EMPTY, REQUIREMENTS_ERROR, REQUIREMENT_KEY_INVALID } from './requirementsModel'

const CASE = {
  id: 'tc-1', project_id: 'p-1', title: 'Refund a partial order', status: 'active', version: 3, steps: [], tags: [],
  test_type: 'functional', priority: 'high', severity: 'major', is_automated: false, automation_status: 'not_automated',
  ai_generated: false, created_at: '2026-10-10T10:00:00Z', updated_at: '2026-10-12T10:00:00Z', source: 'managed',
} as unknown as ManagedTestCase

function link(id: string, key: string, over: Partial<RequirementLink> = {}): RequirementLink {
  return {
    id, test_case_id: 'tc-1', requirement_key: key, normalized_key: key.toUpperCase().replace(/^\[|\]$/g, ''),
    requirement_title: null, link_type: 'verifies', created_by_name: 'Priya Shah', created_at: '2026-10-20T10:00:00Z', ...over,
  }
}

const LINKS: RequirementLinkList = {
  total: 3,
  items: [
    link('l1', 'REQ-PAY-07', { requirement_title: 'Refund within 24h' }),
    link('l2', '[req-pay-09]', { link_type: 'partially_verifies', created_by_name: null }),
    link('l3', 'REQ-<b>x</b>', { link_type: 'related', requirement_title: '<img src=x onerror=alert(1)>' }),
  ],
}

const onFilterRequirement = vi.fn()

function renderSection(over: { caseItem?: ManagedTestCase; canEdit?: boolean } = {}) {
  return render(
    <CaseRequirementsSection caseItem={over.caseItem ?? CASE} canEdit={over.canEdit ?? true} onFilterRequirement={onFilterRequirement} />,
  )
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } })
}

beforeEach(() => {
  addRequirement.mockReset()
  removeRequirement.mockReset()
  mutate.mockReset()
  refreshLibraryHealth.mockReset()
  onFilterRequirement.mockReset()
  useCaseRequirements.mockClear()
  answer = { data: LINKS, isLoading: false }
})

describe('CaseRequirementsSection', () => {
  it('lists each link as React text: key, title, type and who added it', () => {
    const { container } = renderSection()
    expect(useCaseRequirements).toHaveBeenCalledWith('tc-1', true)
    expect(screen.getByRole('region', { name: 'Requirements' })).toBeInTheDocument()
    const rows = container.querySelectorAll('[data-requirement-link]')
    expect(rows).toHaveLength(3)
    const first = within(rows[0] as HTMLElement)
    expect(first.getByRole('button', { name: 'REQ-PAY-07' })).toBeInTheDocument()
    expect(first.getByText('Refund within 24h')).toBeInTheDocument()
    expect(first.getByText('verifies')).toBeInTheDocument()
    expect(first.getByText('added by Priya Shah')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('partly verifies')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('added by not recorded')).toBeInTheDocument()
    expect(within(rows[2] as HTMLElement).getByText('related')).toBeInTheDocument()
    expect(screen.getByText('REQ-<b>x</b>')).toBeInTheDocument()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(container.querySelector('img, b')).toBeNull()
  })

  it('filters the list by the normalised key when a key is clicked', () => {
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: '[req-pay-09]' }))
    expect(onFilterRequirement).toHaveBeenCalledWith('REQ-PAY-09')
  })

  it('adds a requirement with the key, title and link type, then refreshes the list and health', async () => {
    addRequirement.mockResolvedValueOnce(link('l4', 'REQ-PAY-10'))
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Add requirement' }))
    const form = screen.getByRole('region', { name: 'Add requirement' })
    expect(form.querySelector('#tm-requirement-add-title')).not.toBeNull()
    fireEvent.change(within(form).getByLabelText('Key'), { target: { value: '  REQ-PAY-10 ' } })
    fireEvent.change(within(form).getByLabelText('Title (optional)'), { target: { value: 'Partial refunds' } })
    fireEvent.change(within(form).getByLabelText('Link type'), { target: { value: 'partially_verifies' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add requirement' }))
    await waitFor(() => expect(addRequirement).toHaveBeenCalledWith('tc-1', {
      requirement_key: 'REQ-PAY-10', requirement_title: 'Partial refunds', link_type: 'partially_verifies',
    }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Add requirement' })).toBeNull())
    expect(mutate).toHaveBeenCalled()
    expect(refreshLibraryHealth).toHaveBeenCalled()
  })

  it('defaults the link type to Verifies and sends no empty title', async () => {
    addRequirement.mockResolvedValueOnce(link('l4', 'REQ-1'))
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Add requirement' }))
    const form = screen.getByRole('region', { name: 'Add requirement' })
    expect((within(form).getByLabelText('Link type') as HTMLSelectElement).value).toBe('verifies')
    fireEvent.change(within(form).getByLabelText('Key'), { target: { value: 'REQ-1' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add requirement' }))
    await waitFor(() => expect(addRequirement).toHaveBeenCalledWith('tc-1', { requirement_key: 'REQ-1', requirement_title: null, link_type: 'verifies' }))
  })

  it('renders the 409 sentence in the form and keeps it open', async () => {
    addRequirement.mockRejectedValueOnce(httpError(409, 'This case already links REQ-PAY-07.'))
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Add requirement' }))
    const form = screen.getByRole('region', { name: 'Add requirement' })
    fireEvent.change(within(form).getByLabelText('Key'), { target: { value: 'req-pay-07' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add requirement' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('This case already links REQ-PAY-07.')
    expect(screen.getByRole('region', { name: 'Add requirement' })).toBeInTheDocument()
    expect(mutate).not.toHaveBeenCalled()
  })

  it('refuses a key with a control character before sending', () => {
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Add requirement' }))
    const form = screen.getByRole('region', { name: 'Add requirement' })
    fireEvent.change(within(form).getByLabelText('Key'), { target: { value: 'REQ\u0007' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Add requirement' }))
    expect(within(form).getByRole('alert')).toHaveTextContent(REQUIREMENT_KEY_INVALID)
    expect(addRequirement).not.toHaveBeenCalled()
  })

  it('asks in the row before removing, and sends only on its Remove', async () => {
    removeRequirement.mockResolvedValueOnce(undefined)
    renderSection()
    fireEvent.click(screen.getByRole('button', { name: 'Remove REQ-PAY-07' }))
    expect(removeRequirement).not.toHaveBeenCalled()
    const strip = screen.getByRole('group', { name: 'Remove REQ-PAY-07 from this case?' })
    fireEvent.click(within(strip).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(removeRequirement).toHaveBeenCalledWith('tc-1', 'l1'))
    await waitFor(() => expect(mutate).toHaveBeenCalled())
    expect(refreshLibraryHealth).toHaveBeenCalled()
  })

  it.each([
    ['a viewer or All Projects', { canEdit: false }],
    ['a deprecated case', { caseItem: { ...CASE, status: 'deprecated' } as ManagedTestCase }],
    ['an archived case', { caseItem: { ...CASE, status: 'archived' } as ManagedTestCase }],
  ])('offers no control to %s', (_label, over) => {
    renderSection(over)
    expect(screen.queryByRole('button', { name: 'Add requirement' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Remove / })).toBeNull()
    // The key still filters: reading is everyone's.
    expect(screen.getByRole('button', { name: 'REQ-PAY-07' })).toBeInTheDocument()
  })

  it('says when nothing is linked and when the read failed', () => {
    answer = { data: { items: [], total: 0 }, isLoading: false }
    const { rerender } = renderSection()
    expect(screen.getByText(REQUIREMENTS_EMPTY)).toBeInTheDocument()
    answer = { data: undefined, error: new Error('500'), isLoading: false }
    rerender(<CaseRequirementsSection caseItem={CASE} canEdit onFilterRequirement={onFilterRequirement} />)
    expect(screen.getByRole('alert')).toHaveTextContent(REQUIREMENTS_ERROR)
  })
})
