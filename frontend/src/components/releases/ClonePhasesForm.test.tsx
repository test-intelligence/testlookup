/**
 * W11 REL-05: the Copy phases form. Its sources are the same project's
 * releases that HAVE phases (from the list the page holds); the preview shows
 * the dates as they will land; Copy sends `{ from_release_id, shift_to }`
 * (the chosen day at 00:00 UTC); a refusal is the server's sentence, in the
 * form, as text.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Release, ReleasePhase } from '@/types/releases'
import ClonePhasesForm, { NO_CLONE_SOURCE } from './ClonePhasesForm'
import { releasesService } from '@/services/releasesService'
import toast from 'react-hot-toast'

vi.mock('@/services/releasesService', () => ({ releasesService: { clonePhases: vi.fn() } }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function phase(name: string, order: number, start: string | null, end: string | null): ReleasePhase {
  return {
    id: `ph-${name}`, release_id: 'r', name, phase_type: 'qa_testing', status: 'completed', description: null,
    order_index: order, planned_start: start, planned_end: end, actual_start: null, actual_end: null,
    exit_criteria: null, notes: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
  }
}

function rel(id: string, overrides: Partial<Release> & { cutoff_start_at?: string | null } = {}) {
  return {
    id, project_id: 'proj-1', name: id, version: null, description: null, status: 'released',
    planned_date: null, released_at: null, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    phases: [], ...overrides,
  } as Release & { cutoff_start_at?: string | null }
}

const TARGET = rel('2026.12', { status: 'planning', cutoff_start_at: '2026-11-03T00:00:00Z' })
const SOURCE = rel('2026.10', {
  planned_date: '2026-10-01T00:00:00Z',
  phases: [
    phase('Smoke', 0, '2026-09-01T00:00:00Z', '2026-09-05T00:00:00Z'),
    phase('UAT', 1, '2026-09-08T00:00:00Z', '2026-09-20T00:00:00Z'),
  ],
})
const RELEASES = [
  TARGET,
  SOURCE,
  rel('2026.09', { planned_date: '2026-09-01T00:00:00Z', phases: [phase('Only', 0, null, null)] }),
  rel('2026.11-empty', { phases: [] }),
  rel('Other project', { project_id: 'proj-2', phases: SOURCE.phases }),
]

function renderForm(releases: Release[] = RELEASES) {
  const onDone = vi.fn()
  const onCancel = vi.fn()
  render(<ClonePhasesForm release={TARGET} releases={releases} onDone={onDone} onCancel={onCancel} />)
  return { onDone, onCancel, form: screen.getByRole('region', { name: 'Copy phases' }) }
}

beforeEach(() => {
  vi.mocked(releasesService.clonePhases).mockReset()
  vi.mocked(toast.success).mockReset()
})

describe('ClonePhasesForm', () => {
  it('lists only this project\'s releases that have phases', () => {
    const { form } = renderForm()
    const select = within(form).getByLabelText('From release') as HTMLSelectElement
    expect(Array.from(select.options).map(o => o.textContent)).toEqual(['2026.10 · 2 phases', '2026.09 · 1 phase'])
    expect(document.getElementById('clone-phases-title')).toHaveTextContent('Copy phases')
  })

  it('prefills First phase starts with the cutoff start and previews the moved dates', () => {
    const { form } = renderForm()
    expect(within(form).getByLabelText('First phase starts')).toHaveValue('2026-11-03')
    const rows = within(within(form).getByRole('list', { name: 'Phases to copy' })).getAllByRole('listitem')
    expect(rows.map(r => r.textContent)).toEqual([
      'Smoke·QA Testing·5 days2026-11-03 → 2026-11-07',
      'UAT·QA Testing·13 days2026-11-10 → 2026-11-22',
    ])
    fireEvent.change(within(form).getByLabelText('First phase starts'), { target: { value: '2026-12-01' } })
    expect(rows[0]).toHaveTextContent('2026-12-01 → 2026-12-05')
  })

  it('an undated source asks for no start and sends shift_to null', async () => {
    vi.mocked(releasesService.clonePhases).mockResolvedValue({ release_id: TARGET.id, created: 1, phases: [] })
    const { form } = renderForm()
    fireEvent.change(within(form).getByLabelText('From release'), { target: { value: '2026.09' } })
    expect(within(form).queryByLabelText('First phase starts')).toBeNull()
    fireEvent.click(within(form).getByRole('button', { name: 'Copy phases' }))
    await waitFor(() => expect(releasesService.clonePhases).toHaveBeenCalledWith('2026.12', {
      from_release_id: '2026.09', shift_to: null,
    }))
  })

  it('Copy sends { from_release_id, shift_to } at 00:00 UTC, toasts once and hands the result back', async () => {
    const created = { release_id: TARGET.id, created: 2, phases: [], source_release_id: SOURCE.id, shifted_by_days: 63 }
    vi.mocked(releasesService.clonePhases).mockResolvedValue(created)
    const { form, onDone } = renderForm()
    fireEvent.click(within(form).getByRole('button', { name: 'Copy phases' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(created))
    expect(releasesService.clonePhases).toHaveBeenCalledWith('2026.12', {
      from_release_id: '2026.10', shift_to: '2026-11-03T00:00:00Z',
    })
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('Copied 2 phases from 2026.10.')
  })

  it('a refusal is the server\'s sentence, in the form, as text', async () => {
    vi.mocked(releasesService.clonePhases).mockRejectedValue({
      response: { status: 409, data: { detail: 'This release already has phases. Remove them first, or add phases one at a time.' } },
    })
    const { form, onDone } = renderForm()
    fireEvent.click(within(form).getByRole('button', { name: 'Copy phases' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(
      'This release already has phases. Remove them first, or add phases one at a time.',
    )
    expect(onDone).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('with nothing to copy from it says so and cannot copy', () => {
    const { form } = renderForm([TARGET, rel('Other project', { project_id: 'proj-2', phases: SOURCE.phases })])
    expect(within(form).getByText(NO_CLONE_SOURCE)).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Copy phases' })).toBeDisabled()
  })

  it('renders a release name and a phase name as text', () => {
    const hostile = rel('<img src=x onerror=alert(1)>', { phases: [phase('<b>bold</b>', 0, null, null)] })
    const { form } = renderForm([TARGET, hostile])
    expect(within(form).getByText('<b>bold</b>')).toBeInTheDocument()
    expect(form.querySelector('img')).toBeNull()
    expect(form.querySelector('b')).toBeNull()
  })

  it('Cancel hands back without a request', () => {
    const { form, onCancel } = renderForm()
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalled()
    expect(releasesService.clonePhases).not.toHaveBeenCalled()
  })
})
