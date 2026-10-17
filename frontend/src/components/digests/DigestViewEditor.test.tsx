/**
 * NTF-04: the in-page saved-view editor. New views POST the report-view shape
 * with `page: 'trends'`; an edited report view keeps its own page; the name
 * and suite limits block Save with the reason as text; a refused save shows
 * the server's detail; release names are React text.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const svc = vi.hoisted(() => ({ createSavedView: vi.fn(), updateSavedView: vi.fn() }))
vi.mock('@/services/savedViewsService', () => ({
  createSavedView: svc.createSavedView,
  updateSavedView: svc.updateSavedView,
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import DigestViewEditor from './DigestViewEditor'

const P = 'p1'
const RELEASES = [
  { id: 'r-old', name: '2026.08', status: 'released', is_active: false },
  { id: 'r-act', name: '2026.10', status: 'in_progress', is_active: true },
  { id: 'r-bad', name: '<img src=x onerror=alert(1)>', status: 'planning' },
]

function renderEditor(props: Partial<Parameters<typeof DigestViewEditor>[0]> = {}) {
  const onSaved = vi.fn()
  const onCancel = vi.fn()
  render(<DigestViewEditor projectId={P} releases={RELEASES} onSaved={onSaved} onCancel={onCancel} {...props} />)
  return { onSaved, onCancel, editor: within(screen.getByRole('region')) }
}

describe('DigestViewEditor', () => {
  beforeEach(() => {
    svc.createSavedView.mockReset().mockResolvedValue({})
    svc.updateSavedView.mockReset().mockResolvedValue({})
  })

  it('a new view: releases in the model order, every field reaches the POST', async () => {
    const { editor, onSaved } = renderEditor()
    const boxes = editor.getAllByRole('checkbox').slice(0, 3).map(b => b.closest('label')?.textContent)
    // The active release first; the rest keep the list's order.
    expect(boxes).toEqual(['2026.10', '2026.08', '<img src=x onerror=alert(1)>'])
    expect(document.querySelector('img')).toBeNull()

    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'Two releases' } })
    fireEvent.click(editor.getByRole('checkbox', { name: '2026.10' }))
    fireEvent.click(editor.getByRole('checkbox', { name: '2026.08' }))
    fireEvent.change(editor.getByLabelText('Suites (comma-separated)'), { target: { value: 'checkout, payments' } })
    fireEvent.change(editor.getByLabelText('Window'), { target: { value: '14' } })
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(svc.createSavedView).toHaveBeenCalledTimes(1))
    expect(svc.createSavedView).toHaveBeenCalledWith({
      project_id: P,
      name: 'Two releases',
      page: 'trends',
      is_shared: false,
      filters: { kind: 'report_view', page: 'trends', window: 14, release_ids: ['r-act', 'r-old'], suites: ['checkout', 'payments'] },
    })
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
  })

  it('the window offers 1, 7, 14, 30 and 90 days, 7 by default', () => {
    const { editor } = renderEditor()
    const select = editor.getByLabelText('Window') as HTMLSelectElement
    expect([...select.options].map(o => o.value)).toEqual(['1', '7', '14', '30', '90'])
    expect(select.value).toBe('7')
  })

  it('a 1-character name blocks Save, with the reason', () => {
    const { editor } = renderEditor()
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    expect(editor.getByText('A name needs 2 to 255 characters.')).toBeInTheDocument()
    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'x' } })
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    expect(svc.createSavedView).not.toHaveBeenCalled()
  })

  it('51 suites block Save, with the reason', () => {
    const { editor } = renderEditor()
    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'Many' } })
    fireEvent.change(editor.getByLabelText('Suites (comma-separated)'), {
      target: { value: Array.from({ length: 51 }, (_, i) => `suite-${i}`).join(', ') },
    })
    expect(editor.getByText('At most 50 suites.')).toBeInTheDocument()
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    expect(svc.createSavedView).not.toHaveBeenCalled()
  })

  it('editing a report view saved on Failures keeps its page and reads its scope back', async () => {
    const view = {
      id: 'v1', user_id: 'u', project_id: P, name: 'Failures view', description: null, page: 'failures' as const,
      filters: { kind: 'report_view', page: 'failures', window: 30, release_ids: ['r-act'], release_id: 'r-act', suites: ['api'] },
      is_shared: true, is_default: false, created_at: '2026-10-01T00:00:00Z', updated_at: null,
    }
    const { editor } = renderEditor({ view })
    expect((editor.getByLabelText('Name') as HTMLInputElement).value).toBe('Failures view')
    expect((editor.getByRole('checkbox', { name: '2026.10' }) as HTMLInputElement).checked).toBe(true)
    expect((editor.getByLabelText('Suites (comma-separated)') as HTMLInputElement).value).toBe('api')
    expect((editor.getByLabelText('Window') as HTMLSelectElement).value).toBe('30')
    expect((editor.getByRole('checkbox', { name: 'Shared with the project' }) as HTMLInputElement).checked).toBe(true)
    fireEvent.click(editor.getByRole('checkbox', { name: '2026.10' }))
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(svc.updateSavedView).toHaveBeenCalledTimes(1))
    expect(svc.updateSavedView).toHaveBeenCalledWith('v1', {
      name: 'Failures view', page: 'failures', is_shared: true, filters: { kind: 'report_view', page: 'failures', window: 30, suites: ['api'] },
    })
    expect(svc.createSavedView).not.toHaveBeenCalled()
  })

  it("a refused save shows the server's detail as text; Cancel leaves without a request", async () => {
    svc.createSavedView.mockRejectedValue({ response: { status: 422, data: { detail: 'Name already <b>taken</b>' } } })
    const { editor, onCancel, onSaved } = renderEditor()
    fireEvent.change(editor.getByLabelText('Name'), { target: { value: 'Dup' } })
    fireEvent.click(editor.getByRole('button', { name: 'Save' }))
    const alert = await editor.findByRole('alert')
    expect(alert).toHaveTextContent('Name already <b>taken</b>')
    expect(alert.querySelector('b')).toBeNull()
    expect(onSaved).not.toHaveBeenCalled()
    fireEvent.click(editor.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('a garbage release list renders the editor with no release to choose', () => {
    render(<DigestViewEditor projectId={P} releases={[null, 3, { id: 7 }] as never} onSaved={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByText(/No releases in this project/)).toBeInTheDocument()
  })
})
