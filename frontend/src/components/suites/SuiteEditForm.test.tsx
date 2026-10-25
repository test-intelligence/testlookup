/**
 * W13 TCL-07: the suite Edit form, in the page.
 *
 * Prefilled from the suite; Save sends ONLY the changed fields to
 * `PATCH /api/v1/suites/{id}` with the global toast suppressed; the rename
 * note shows only while the name differs; the server's 409 sentence renders
 * in the form; Escape with typed input asks before discarding (the slot rule).
 */
import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useInlineSlot } from '@/components/ui/inPage/useInlineSlot'
import type { TestSuite } from '@/types/suites'
import { SuiteEditForm } from './SuiteEditForm'
import { changedFields, parseTags, renameWarning } from './suiteEditModel'

const patchData = vi.fn()
vi.mock('@/services/http', () => ({
  patchData: (...args: unknown[]) => patchData(...args),
}))
const refreshSuites = vi.fn()
vi.mock('@/hooks/useSuites', () => ({ refreshSuites: () => refreshSuites() }))
const toastSuccess = vi.fn()
vi.mock('react-hot-toast', () => ({ default: { success: (m: string) => toastSuccess(m), error: vi.fn() } }))

const SUITE: TestSuite = {
  id: 'suite-1', project_id: 'p1', name: 'Checkout API', description: 'Cards', is_default: false,
  tags: ['smoke', 'payments'], test_case_count: 4, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
}

function Harness({ suite = SUITE, onSaved = vi.fn() }: { suite?: TestSuite; onSaved?: (s: TestSuite) => void }) {
  const slot = useInlineSlot()
  const [open, setOpen] = useState(true)
  if (!open) return <p>closed</p>
  return (
    <SuiteEditForm
      suite={suite}
      slot={slot}
      onClose={() => { slot.close(); setOpen(false) }}
      onSaved={(saved) => { onSaved(saved); setOpen(false) }}
    />
  )
}

const field = (name: string) => screen.getByLabelText(name) as HTMLInputElement

describe('SuiteEditForm', () => {
  beforeEach(() => {
    patchData.mockReset()
    refreshSuites.mockReset()
    toastSuccess.mockReset()
  })

  it('opens in the page, prefilled, with Save offered only once something changed', () => {
    render(<Harness />)
    expect(screen.getByRole('region', { name: 'Edit suite' })).toBeInTheDocument()
    expect(document.getElementById('suite-edit-title')).toHaveTextContent('Edit suite')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(field('Name').value).toBe('Checkout API')
    expect(field('Description').value).toBe('Cards')
    expect(field('Tags').value).toBe('smoke, payments')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('sends only the changed fields, toasts, refreshes the suites and closes', async () => {
    const onSaved = vi.fn()
    patchData.mockResolvedValue({ ...SUITE, description: '' })
    render(<Harness onSaved={onSaved} />)
    fireEvent.change(field('Description'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(patchData).toHaveBeenCalledWith('/api/v1/suites/suite-1', { description: '' }, { suppressToast: true })
    expect(toastSuccess).toHaveBeenCalledWith('Suite “Checkout API” saved.')
    expect(refreshSuites).toHaveBeenCalledTimes(1)
    expect(screen.getByText('closed')).toBeInTheDocument()
  })

  it('shows the rename note only while the trimmed name differs', () => {
    render(<Harness />)
    fireEvent.change(field('Name'), { target: { value: '  Checkout API  ' } })
    expect(screen.queryByRole('note')).toBeNull()
    fireEvent.change(field('Name'), { target: { value: 'Checkout API v2' } })
    expect(screen.getByRole('note')).toHaveTextContent(
      'Past runs keep reporting “Checkout API”: their results stay under that name on Test Cases › Suites. ' +
      'A run that still sends the old name creates a suite of that name again for tests new to this project.',
    )
  })

  it('renders the server sentence of a 409 inside the form and stays open', async () => {
    patchData.mockRejectedValue({ response: { status: 409, data: { detail: 'Another suite in this project already uses that name' } } })
    render(<Harness />)
    fireEvent.change(field('Name'), { target: { value: 'Smoke' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Another suite in this project already uses that name')
    expect(patchData).toHaveBeenCalledWith('/api/v1/suites/suite-1', { name: 'Smoke' }, { suppressToast: true })
    expect(screen.getByRole('region', { name: 'Edit suite' })).toBeInTheDocument()
    expect(toastSuccess).not.toHaveBeenCalled()
  })

  it('Escape with typed input asks first; Escape untouched closes at once', () => {
    const { unmount } = render(<Harness />)
    fireEvent.change(field('Tags'), { target: { value: 'smoke' } })
    fireEvent.keyDown(field('Tags'), { key: 'Escape' })
    expect(screen.getByText(/Discard your changes to/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(field('Tags').value).toBe('smoke')
    fireEvent.keyDown(field('Tags'), { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(screen.getByText('closed')).toBeInTheDocument()
    unmount()

    render(<Harness />)
    fireEvent.keyDown(field('Name'), { key: 'Escape' })
    expect(screen.getByText('closed')).toBeInTheDocument()
  })

  it('renders a hostile name as text', () => {
    render(<Harness suite={{ ...SUITE, name: '<img src=x onerror=alert(1)>' }} />)
    fireEvent.change(field('Name'), { target: { value: 'Renamed' } })
    expect(screen.getByRole('note')).toHaveTextContent('“<img src=x onerror=alert(1)>”')
    expect(document.querySelector('img')).toBeNull()
  })
})

describe('suiteEditModel', () => {
  it('parses tags, compares them in order and sends an emptied tag list as []', () => {
    expect(parseTags(' smoke , ,payments, smoke ')).toEqual(['smoke', 'payments'])
    const draft = { name: 'Checkout API', description: 'Cards', tags: 'payments, smoke' }
    expect(changedFields(SUITE, draft)).toEqual({ tags: ['payments', 'smoke'] })
    expect(changedFields(SUITE, { ...draft, tags: '' })).toEqual({ tags: [] })
    expect(changedFields({ ...SUITE, tags: null, description: null }, { name: 'Checkout API', description: '', tags: '' })).toEqual({})
    expect(renameWarning('A', ' ')).toBeNull()
  })
})
