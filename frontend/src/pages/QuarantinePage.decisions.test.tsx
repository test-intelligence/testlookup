/**
 * UX redesign P4 (§5 "Flaky tests" — Delete: `window.prompt` notes): a
 * quarantine decision's notes are typed into an inline form under the row,
 * never a browser prompt; Cancel records nothing; Promote out stays one click;
 * every decision refreshes the list AND the counts beside it.
 *
 * W09 FLK-02: the Quarantined view shows each live quarantine's `SLA due`
 * (the stale one keeps its "stale — N days over SLA"), and warns past the
 * policy's cap; only that view reads the policy.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlakyQuarantineRead } from '@/services/flakyQuarantineService'

const hooks = vi.hoisted(() => ({
  requests: [] as unknown[],
  stats: undefined as unknown,
  policy: null as unknown,
  policyCalls: [] as Array<[unknown, unknown]>,
  refresh: vi.fn(() => Promise.resolve()),
  refreshStats: vi.fn(() => Promise.resolve()),
}))

vi.mock('@/hooks/useFlakyQuarantine', () => ({
  useQuarantineList: () => ({ requests: hooks.requests, isLoading: false, isError: false, refresh: hooks.refresh }),
  useQuarantineStats: () => ({ stats: hooks.stats, isLoading: false, isError: false, refresh: hooks.refreshStats }),
}))

vi.mock('@/hooks/useQuarantinePolicy', () => ({
  useQuarantinePolicy: (projectId: unknown, enabled: unknown) => {
    hooks.policyCalls.push([projectId, enabled])
    return { policy: enabled ? hooks.policy : null, error: undefined, isLoading: false, mutate: vi.fn() }
  },
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
  hooks.stats = undefined
  hooks.policy = null
  hooks.policyCalls = []
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

const DAY = 86_400_000
const STATS = (quarantined: number) => ({
  detected: 0, proposed: 0, approved: 0, quarantined, recheck_scheduled: 0, re_quarantined: 0,
  released: 0, rejected: 0, expired: 0, total_live: quarantined,
})
const POLICY = {
  project_id: 'p1', sla_days: 14, auto_create_defect: false, auto_promote: false, promote_after_passes: 20,
  detection_flip_rate_threshold: 0.2, detection_min_runs: 10, max_active_quarantined: 8, is_default: false,
}

describe('the Quarantined view: SLA due and the cap (W09 FLK-02)', () => {
  it('a live quarantine shows when its SLA is due; a stale one keeps "over SLA" and no due date', () => {
    const due = new Date(Date.now() + 7 * DAY)
    hooks.requests = [
      row('due', 'QUARANTINED', { stale_at: due.toISOString(), sla_days: 7 }),
      row('stale', 'QUARANTINED', { stale: true, stale_at: new Date(Date.now() - 6 * DAY).toISOString(), sla_days: 14 }),
      row('none', 'QUARANTINED', { stale_at: null }),
    ]
    renderBody('active')
    const rowOf = (id: string) => document.querySelector(`[data-quarantine-id="${id}"]`) as HTMLElement
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    const expected = `SLA due ${due.getUTCDate()} ${months[due.getUTCMonth()]}`
    expect(within(rowOf('due')).getByText(new RegExp(`^${expected}`))).toHaveAttribute('title', 'SLA: 7 days')
    expect(rowOf('stale')).toHaveTextContent('stale — 6d over SLA')
    expect(rowOf('stale')).not.toHaveTextContent('SLA due')
    expect(rowOf('none')).not.toHaveTextContent('SLA due')
  })

  it('the proposals view shows no SLA due date and never reads the policy', () => {
    hooks.requests = [row('a', 'PROPOSED', { stale_at: new Date(Date.now() + 7 * DAY).toISOString() })]
    hooks.stats = STATS(12)
    hooks.policy = POLICY
    renderBody('proposals')
    expect(screen.queryByText(/SLA due/)).toBeNull()
    expect(document.querySelector('[data-quarantine-over-cap]')).toBeNull()
    expect(hooks.policyCalls.every(([, enabled]) => enabled === false)).toBe(true)
  })

  it('warns past the cap from the stats count, above the list', () => {
    hooks.requests = [row('b', 'QUARANTINED')]
    hooks.stats = STATS(12)
    hooks.policy = POLICY
    renderBody('active')
    const line = document.querySelector('[data-quarantine-over-cap]') as HTMLElement
    expect(line).toHaveTextContent(/^12 tests are quarantined; this project's policy warns past 8\.$/)
    expect(line.compareDocumentPosition(screen.getByTestId('quarantine-row')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(hooks.policyCalls).toContainEqual(['p1', true])
  })

  it.each([
    ['within the cap', STATS(8), POLICY],
    ['a cap of 0', STATS(12), { ...POLICY, max_active_quarantined: 0 }],
    ['no counts yet', undefined, POLICY],
    ['a malformed policy', STATS(12), { max_active_quarantined: '1' }],
    ['no policy', STATS(12), null],
  ])('no warning: %s', (_label, stats, policy) => {
    hooks.requests = [row('b', 'QUARANTINED')]
    hooks.stats = stats
    hooks.policy = policy
    renderBody('active')
    expect(document.querySelector('[data-quarantine-over-cap]')).toBeNull()
    expect(screen.getByTestId('quarantine-row')).toBeInTheDocument()
  })
})
