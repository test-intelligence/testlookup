/**
 * W22a ACT-05 — the Holds card on /settings/retention.
 *
 * The list hook is replaced by a per-state world (so "one read per selected
 * state" and the answer's `counts` are observable); the two writes are
 * mocked at the service. Pins: the list per state with the server's counts,
 * the load error (never "No holds" from a failed read), place → body
 * `{entity_type: 'project', reason, expires_at}`, a server 422 sentence shown
 * inline, release → body `{reason}`, a 409 re-reads every state.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { HoldState, RetentionHold, RetentionHoldList } from '@/types/retentionHolds'
import { HOLD_PROJECT_ID, HOLD_RELEASE_ID, holdList, holdRow } from '@/test/retentionHoldMocks'

const service = vi.hoisted(() => ({ placeRetentionHold: vi.fn(), releaseRetentionHold: vi.fn(), listRetentionHolds: vi.fn() }))
vi.mock('@/services/retentionHoldService', () => service)

const hook = vi.hoisted(() => ({
  world: {} as Record<string, { data?: unknown; error?: unknown; isLoading?: boolean }>,
  calls: [] as Array<[string | null, string]>,
  mutate: vi.fn(),
  refresh: vi.fn(),
}))
vi.mock('@/hooks/useRetentionHolds', () => ({
  useRetentionHolds: (projectId: string | null, state = 'live') => {
    hook.calls.push([projectId, state])
    const entry = hook.world[state] ?? {}
    const data = entry.data as RetentionHoldList | undefined
    return {
      holds: Array.isArray(data?.items) ? data.items : [],
      total: data ? data.total : null,
      counts: data?.counts ?? null,
      isLoading: Boolean(entry.isLoading),
      error: entry.error,
      mutate: hook.mutate,
    }
  },
  refreshRetentionHolds: (...a: unknown[]) => hook.refresh(...a),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import HoldsCard from './HoldsCard'

function answer(state: HoldState, rows: RetentionHold[], counts = { live: 0, expired: 0, released: 0 }, total?: number) {
  hook.world[state] = { data: { ...holdList(rows, total), counts } }
}

function renderCard() {
  return render(
    <MemoryRouter>
      <HoldsCard projectId={HOLD_PROJECT_ID} projectName="Payments" />
    </MemoryRouter>,
  )
}

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error('request failed'), { isAxiosError: true, response: { status, data: { detail } } })
}

beforeEach(() => {
  hook.world = {}
  hook.calls = []
  hook.mutate.mockReset()
  hook.refresh.mockReset()
  service.placeRetentionHold.mockReset()
  service.releaseRetentionHold.mockReset()
})

describe('HoldsCard — the list', () => {
  it('lists the live holds with the counts of the answer', () => {
    answer('live', [
      holdRow({ id: 'h-rel', reason: 'Audit Q4 sentinel' }),
      holdRow({ id: 'h-prj', entity_type: 'project', entity_id: null, entity_label: 'the whole project', reason: 'Litigation sentinel' }),
    ], { live: 2, expired: 1, released: 4 })
    renderCard()

    expect(hook.calls[0]).toEqual([HOLD_PROJECT_ID, 'live'])
    const items = screen.getAllByRole('listitem')
    expect(items.map((li) => li.getAttribute('data-retention-hold'))).toEqual(['h-rel', 'h-prj'])
    expect(within(items[0]).getByText('Held: Audit Q4 sentinel')).toBeInTheDocument()
    expect(within(items[0]).getByRole('link', { name: 'release Payments 2026-Q4' })).toHaveAttribute('href', `/releases/${HOLD_RELEASE_ID}`)
    expect(within(items[1]).queryByRole('link')).toBeNull()
    expect(within(items[1]).getByText('Project')).toBeInTheDocument()
    const radios = screen.getAllByRole('radio')
    expect(radios.map((r) => r.textContent)).toEqual(['Live 2', 'Expired 1', 'Released 4'])
  })

  it('asks for the selected state and shows its rows', () => {
    answer('live', [], { live: 0, expired: 1, released: 0 })
    answer('expired', [holdRow({ id: 'h-exp', state: 'expired', expires_at: new Date(Date.now() - 86_400_000).toISOString() })], { live: 0, expired: 1, released: 0 })
    renderCard()
    expect(screen.getByText('No live holds.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: /Expired/ }))
    expect(hook.calls[hook.calls.length - 1]).toEqual([HOLD_PROJECT_ID, 'expired'])
    const row = screen.getByRole('listitem')
    expect(row.textContent).toContain('· Expired ')
    expect(within(row).queryByRole('button', { name: 'Release' })).toBeNull()
  })

  it('reads a released row as who released it, why', () => {
    answer('released', [holdRow({ id: 'h-done', state: 'released', released_at: new Date().toISOString(), released_by_name: 'Ada Admin', release_reason: 'Audit closed' })])
    answer('live', [])
    renderCard()
    fireEvent.click(screen.getByRole('radio', { name: /Released/ }))
    expect(screen.getByRole('listitem').textContent).toMatch(/Released by Ada Admin on .+: Audit closed/)
  })

  it('says the read failed — never "No holds" — and retries', () => {
    hook.world.live = { error: new Error('boom') }
    renderCard()
    expect(screen.getByRole('alert').textContent).toContain('Could not load the retention holds.')
    expect(screen.queryByText(/No live holds/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    expect(hook.mutate).toHaveBeenCalledTimes(1)
  })

  it('shows the total when the page is shorter than it', () => {
    answer('live', [holdRow({ id: 'h-1' })], { live: 60, expired: 0, released: 0 }, 60)
    renderCard()
    expect(screen.getByText('Showing the newest 1 of 60 live holds.')).toBeInTheDocument()
  })

  it('renders a reason as text, never markup', () => {
    answer('live', [holdRow({ id: 'h-x', reason: '<img src=x onerror=alert(1)>' })])
    const { container } = renderCard()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('Held: <img src=x onerror=alert(1)>')).toBeInTheDocument()
  })
})

describe('HoldsCard — place a project hold', () => {
  function openForm() {
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    return document.querySelector('[data-retention-hold-form]') as HTMLElement
  }

  it('sends {entity_type: project, reason, expires_at} and re-reads every state', async () => {
    answer('live', [])
    service.placeRetentionHold.mockResolvedValue(holdRow({ entity_type: 'project', entity_id: null }))
    renderCard()
    const form = openForm()
    expect(form.textContent).toContain('Whole project (Payments)')

    fireEvent.change(within(form).getByLabelText(/Reason/), { target: { value: '  Litigation sentinel  ' } })
    expect(within(form).getByText('19/500')).toBeInTheDocument()
    fireEvent.change(within(form).getByLabelText(/Expires/), { target: { value: '2027-01-31' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Place hold' }))

    await waitFor(() => expect(service.placeRetentionHold).toHaveBeenCalledTimes(1))
    const [projectId, body] = service.placeRetentionHold.mock.calls[0]
    expect(projectId).toBe(HOLD_PROJECT_ID)
    expect(Object.keys(body).sort()).toEqual(['entity_type', 'expires_at', 'reason'])
    expect(body.entity_type).toBe('project')
    expect(body.reason).toBe('Litigation sentinel')
    const end = new Date(body.expires_at)
    expect([end.getFullYear(), end.getMonth(), end.getDate(), end.getHours(), end.getMinutes()]).toEqual([2027, 0, 31, 23, 59])
    await waitFor(() => expect(hook.refresh).toHaveBeenCalledWith(HOLD_PROJECT_ID))
    expect(document.querySelector('[data-retention-hold-form]')).toBeNull()
  })

  it('sends expires_at null without an expiry day', async () => {
    answer('live', [])
    service.placeRetentionHold.mockResolvedValue(holdRow())
    renderCard()
    const form = openForm()
    fireEvent.change(within(form).getByLabelText(/Reason/), { target: { value: 'Audit Q4 sentinel' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Place hold' }))
    await waitFor(() => expect(service.placeRetentionHold).toHaveBeenCalledTimes(1))
    expect(service.placeRetentionHold.mock.calls[0][1]).toEqual({ entity_type: 'project', reason: 'Audit Q4 sentinel', expires_at: null })
  })

  it('refuses a too-short reason inline without a request', () => {
    answer('live', [])
    renderCard()
    const form = openForm()
    fireEvent.change(within(form).getByLabelText(/Reason/), { target: { value: 'ab' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Place hold' }))
    expect(within(form).getByRole('alert').textContent).toBe('A reason of 3 to 500 characters is required.')
    expect(service.placeRetentionHold).not.toHaveBeenCalled()
  })

  it("shows the server's 422 sentence inline in the form", async () => {
    answer('live', [])
    service.placeRetentionHold.mockRejectedValue(apiError(422, { code: 'expiry_not_future', message: 'expires_at must be in the future.' }))
    renderCard()
    const form = openForm()
    fireEvent.change(within(form).getByLabelText(/Reason/), { target: { value: 'Audit Q4 sentinel' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Place hold' }))
    expect((await within(form).findByRole('alert')).textContent).toBe('expires_at must be in the future.')
    expect(hook.refresh).not.toHaveBeenCalled()
  })
})

describe('HoldsCard — release a hold', () => {
  function openRelease(holdId: string) {
    const row = document.querySelector(`[data-retention-hold="${holdId}"]`) as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Release' }))
    return row
  }

  it('sends {reason} for that hold and re-reads every state', async () => {
    answer('live', [holdRow({ id: 'h-rel' })], { live: 1, expired: 0, released: 0 })
    service.releaseRetentionHold.mockResolvedValue(holdRow({ id: 'h-rel', state: 'released' }))
    renderCard()
    const row = openRelease('h-rel')
    fireEvent.change(within(row).getByLabelText('Release reason'), { target: { value: 'Audit closed' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Release hold' }))
    await waitFor(() => expect(service.releaseRetentionHold).toHaveBeenCalledWith(HOLD_PROJECT_ID, 'h-rel', 'Audit closed'))
    await waitFor(() => expect(hook.refresh).toHaveBeenCalledWith(HOLD_PROJECT_ID))
  })

  it('a 409 (released or expired meanwhile) shows the sentence and re-reads the list', async () => {
    answer('live', [holdRow({ id: 'h-rel' })], { live: 1, expired: 0, released: 0 })
    service.releaseRetentionHold.mockRejectedValue(
      apiError(409, { code: 'hold_not_live', message: 'This hold is no longer live; it was released or it expired.' }),
    )
    renderCard()
    const row = openRelease('h-rel')
    fireEvent.change(within(row).getByLabelText('Release reason'), { target: { value: 'Audit closed' } })
    fireEvent.click(within(row).getByRole('button', { name: 'Release hold' }))
    expect((await within(row).findByRole('alert')).textContent).toBe('This hold is no longer live; it was released or it expired.')
    expect(hook.refresh).toHaveBeenCalledWith(HOLD_PROJECT_ID)
  })

  it('refuses an empty release reason without a request', () => {
    answer('live', [holdRow({ id: 'h-rel' })])
    renderCard()
    const row = openRelease('h-rel')
    fireEvent.click(within(row).getByRole('button', { name: 'Release hold' }))
    expect(within(row).getByRole('alert').textContent).toBe('A reason of 3 to 500 characters is required.')
    expect(service.releaseRetentionHold).not.toHaveBeenCalled()
  })
})
