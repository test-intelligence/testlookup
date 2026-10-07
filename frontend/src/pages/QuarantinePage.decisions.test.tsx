/**
 * UX redesign P4 (§5 "Flaky tests" — Delete: `window.prompt` notes): a
 * quarantine decision's notes are typed into an inline form under the row,
 * never a browser prompt; Cancel records nothing; Promote out stays one click;
 * every decision refreshes the list AND the counts beside it.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlakyQuarantineRead } from '@/services/flakyQuarantineService'

const hooks = vi.hoisted(() => ({
  requests: [] as unknown[],
  refresh: vi.fn(() => Promise.resolve()),
  refreshStats: vi.fn(() => Promise.resolve()),
}))

vi.mock('@/hooks/useFlakyQuarantine', () => ({
  useQuarantineList: () => ({ requests: hooks.requests, isLoading: false, isError: false, refresh: hooks.refresh }),
  useQuarantineStats: () => ({ stats: undefined, isLoading: false, isError: false, refresh: hooks.refreshStats }),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true, isQaEngineer: false }),
}))

const service = vi.hoisted(() => ({
  approve: vi.fn(() => Promise.resolve({})),
  reject: vi.fn(() => Promise.resolve({})),
  release: vi.fn(() => Promise.resolve({})),
}))
vi.mock('@/services/flakyQuarantineService', () => ({ flakyQuarantineService: service }))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import { QuarantineBody, type QuarantineView } from './QuarantinePage'

function row(id: string, status: FlakyQuarantineRead['status'], extra: Partial<FlakyQuarantineRead> = {}) {
  return {
    id,
    project_id: 'p1',
    test_fingerprint: `fp-${id}`,
    test_name: `test_${id}`,
    suite_name: 'Checkout',
    status,
    flip_rate: 0.3,
    flip_window_size: 10,
    owner_name: null,
    defect_id: null,
    defect_jira_key: null,
    stale: false,
    ready_to_promote: false,
    consecutive_passes: 0,
    reviewer_notes: null,
    updated_at: '2026-09-02T00:00:00Z',
    ...extra,
  }
}

const renderBody = (view: QuarantineView) =>
  render(
    <MemoryRouter>
      <QuarantineBody view={view} projectId="p1" />
    </MemoryRouter>,
  )

const prompt = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('prompt', prompt)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('quarantine decisions record their notes inline', () => {
  it('approves with the typed notes, refreshes list and counts, and never opens a prompt', async () => {
    hooks.requests = [row('a', 'PROPOSED')]
    renderBody('proposals')
    fireEvent.click(screen.getByTestId('quarantine-approve'))
    const form = screen.getByTestId('quarantine-decision-form')
    fireEvent.change(within(form).getByRole('textbox', { name: /Approval notes/ }), { target: { value: '  flips on CI only ' } })
    await act(async () => {
      fireEvent.click(within(form).getByRole('button', { name: 'Confirm approve' }))
    })
    expect(service.approve).toHaveBeenCalledWith('a', { notes: 'flips on CI only' })
    expect(hooks.refresh).toHaveBeenCalled()
    expect(hooks.refreshStats).toHaveBeenCalled()
    expect(prompt).not.toHaveBeenCalled()
    expect(screen.queryByTestId('quarantine-decision-form')).toBeNull()
  })

  it('rejects with no notes as undefined, and Cancel records nothing', async () => {
    hooks.requests = [row('a', 'PROPOSED')]
    renderBody('proposals')
    fireEvent.click(screen.getByTestId('quarantine-reject'))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(service.reject).not.toHaveBeenCalled()
    expect(screen.queryByTestId('quarantine-decision-form')).toBeNull()

    fireEvent.click(screen.getByTestId('quarantine-reject'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Confirm reject' }))
    })
    expect(service.reject).toHaveBeenCalledWith('a', { notes: undefined })
    expect(prompt).not.toHaveBeenCalled()
  })

  it('releases an active quarantine through the form; Promote out stays one click', async () => {
    hooks.requests = [row('b', 'QUARANTINED', { ready_to_promote: true, consecutive_passes: 6 })]
    renderBody('active')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Promote out/ }))
    })
    expect(service.release).toHaveBeenCalledWith('b', {
      notes: 'Promoted out of quarantine after 6 consecutive passing runs',
    })

    fireEvent.click(screen.getByTestId('quarantine-release'))
    fireEvent.change(screen.getByTestId('quarantine-notes'), { target: { value: 'fixed upstream' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Confirm release' }))
    })
    expect(service.release).toHaveBeenLastCalledWith('b', { notes: 'fixed upstream' })
    expect(prompt).not.toHaveBeenCalled()
  })

  it('shows only the rows of its view', () => {
    hooks.requests = [row('a', 'PROPOSED'), row('b', 'QUARANTINED'), row('c', 'DETECTED')]
    renderBody('proposals')
    expect(screen.getAllByTestId('quarantine-row').map((r) => r.getAttribute('data-quarantine-id'))).toEqual(['a', 'c'])
  })

  it('points an empty proposals view at the help topic instead of restating the detection rule', () => {
    hooks.requests = []
    renderBody('proposals')
    expect(screen.getByText('No pending quarantine proposals.')).toBeInTheDocument()
    expect(screen.queryByText(/flip rate >= 20%/)).toBeNull()
    expect(screen.getByRole('button', { name: 'How flaky tests are detected and quarantined' })).toBeInTheDocument()
  })
})
