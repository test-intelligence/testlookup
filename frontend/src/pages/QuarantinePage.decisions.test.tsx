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
import toast from 'react-hot-toast'

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

const perms = vi.hoisted(() => ({ isQaEngineer: false }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ canAccessManagement: true, isQaEngineer: perms.isQaEngineer }),
}))

// W27 PAN-04: File Jira's form (its reads stay loading here: nothing is filed in these tests).
// D-43: the page itself reads no probe; offline mode comes from the AI mode setting.
const ai = vi.hoisted(() => ({ offline: false }))
vi.mock('@/hooks/useAIConfig', () => ({ useAIConfig: () => ({ data: { ai_offline_mode: ai.offline } }) }))
const jira = vi.hoisted(() => ({ meta: undefined as unknown, keys: [] as unknown[] }))
vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: (projectId: unknown) => {
    jira.keys.push(projectId)
    return { metadata: jira.meta, isLoading: jira.meta === undefined, isError: false }
  },
  useJiraDefectPreview: () => ({ preview: undefined, isLoading: true, isError: false }),
}))

const service = vi.hoisted(() => ({
  approve: vi.fn(() => Promise.resolve({})),
  reject: vi.fn(() => Promise.resolve({})),
  release: vi.fn(() => Promise.resolve({})),
}))
vi.mock('@/services/flakyQuarantineService', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/flakyQuarantineService')>(), flakyQuarantineService: service }))

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
  perms.isQaEngineer = false
  hooks.stats = undefined
  hooks.policy = null
  hooks.policyCalls = []
  jira.meta = undefined
  jira.keys = []
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

  // S-E-4: a refused decision showed "Failed: Request failed with status code 409" beside the
  // server's sentence (which the shared client toasts). The page adds no toast of its own then,
  // and for a 404 (the client stays quiet) it shows the server's words.
  async function approveAndFail(error: unknown) {
    hooks.requests = [row('a', 'PROPOSED')]
    service.approve.mockImplementationOnce(() => Promise.reject(error))
    renderBody('proposals')
    fireEvent.click(screen.getByTestId('quarantine-approve'))
    const form = screen.getByTestId('quarantine-decision-form')
    await act(async () => {
      fireEvent.click(within(form).getByRole('button', { name: 'Confirm approve' }))
    })
  }

  it('S-E-4: a 409 adds no status-code toast on top of the server sentence', async () => {
    await approveAndFail(Object.assign(new Error('Request failed with status code 409'), {
      response: { status: 409, data: { detail: 'This request is quarantined now, so it cannot be approved. Refresh the list to see where it stands.' } },
      config: {},
    }))
    expect(toast.error).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('S-E-4: a 404 the client keeps quiet reads as the server sentence', async () => {
    await approveAndFail(Object.assign(new Error('Request failed with status code 404'), {
      response: { status: 404, data: { detail: 'Quarantine request not found' } },
      config: {},
    }))
    expect(toast.error).toHaveBeenCalledWith('Quarantine request not found')
    expect(vi.mocked(toast.error).mock.calls.flat().join(' ')).not.toMatch(/status code/)
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

// W27 PAN-04: File Jira opens its prefilled form IN THE PAGE, under the row it was filed from.
describe('File Jira opens under its row', () => {
  beforeEach(() => {
    perms.isQaEngineer = true
    hooks.requests = [row('a', 'QUARANTINED'), row('b', 'QUARANTINED')]
  })

  const rowOf = (id: string) => document.querySelector(`tr[data-quarantine-id="${id}"]`) as HTMLElement

  it('renders Create Jira issue in td[colspan="8"] right after that row, above its decision form; no modal', () => {
    renderBody('active')
    fireEvent.click(within(rowOf('b')).getByRole('button', { name: 'File Jira' }))
    const form = screen.getByRole('region', { name: 'Create Jira issue' })
    const formRow = form.closest('tr') as HTMLElement
    expect(formRow).toHaveAttribute('data-in-page-row')
    expect(formRow.previousElementSibling).toBe(rowOf('b'))
    expect(formRow.querySelector('td')).toHaveAttribute('colspan', '8')
    expect(within(form).getByText('test_b')).toBeInTheDocument()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()

    // The row's own decision form opens below the Jira form.
    fireEvent.click(within(rowOf('b')).getByRole('button', { name: /Release/ }))
    expect(formRow.nextElementSibling).toHaveAttribute('data-testid', 'quarantine-decision-form')
  })

  it('one Jira form per table: another row replaces it; Cancel closes it', () => {
    renderBody('active')
    fireEvent.click(within(rowOf('a')).getByRole('button', { name: 'File Jira' }))
    fireEvent.click(within(rowOf('b')).getByRole('button', { name: 'File Jira' }))
    const forms = screen.getAllByRole('region', { name: 'Create Jira issue' })
    expect(forms).toHaveLength(1)
    expect((forms[0].closest('tr') as HTMLElement).previousElementSibling).toBe(rowOf('b'))
    fireEvent.click(within(forms[0]).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Create Jira issue' })).toBeNull()
  })
})

// D-43: a proposal row offered "File Jira" while offline mode blocked Jira; the
// refusal only appeared after the form opened.
describe('File Jira knows whether Jira is available before the form opens (D-43)', () => {
  beforeEach(() => {
    perms.isQaEngineer = true
    hooks.requests = [row('a', 'PROPOSED')]
  })

  it('offline: the action reads "File defect" and says why and where it goes', () => {
    ai.offline = true
    renderBody('proposals')
    expect(screen.queryByRole('button', { name: 'File Jira' })).toBeNull()
    const button = screen.getByRole('button', { name: 'File defect' })
    expect(button).toBeEnabled()
    expect(button).toHaveAttribute('title', expect.stringContaining('Offline mode is on, so nothing is sent to Jira'))
    expect(button).toHaveAttribute('title', expect.stringContaining('webhook receiver'))
  })

  it('online: "File Jira" as before, and the page reads no Jira probe before the form opens', () => {
    ai.offline = false
    renderBody('proposals')
    expect(screen.getByRole('button', { name: 'File Jira' })).toBeEnabled()
    expect(jira.keys).toEqual([])
  })
})

describe('D-53: the Proposed tab names who asked', () => {
  it('reads "Proposed by {name}", "Detected automatically", or "Proposed manually" for an older manual row', () => {
    hooks.requests = [
      row('a', 'PROPOSED', { detection_method: 'manual', proposed_by_name: 'Quinn Lead' }),
      row('b', 'PROPOSED', { detection_method: 'pass_fail_ratio', proposed_by_name: null }),
      row('c', 'PROPOSED', { detection_method: 'manual', proposed_by_name: null }),
      // An agent action: the agent asked and a person approved; an older row recorded the approver as proposer.
      row('d', 'PROPOSED', { detection_method: 'agent_action', proposed_by_name: 'Quinn Lead' }),
    ]
    renderBody('proposals')
    const lines = screen
      .getAllByTestId('quarantine-row')
      .map((tr) => tr.querySelector('[data-quarantine-proposer]')?.textContent)
    expect(lines).toEqual(['Proposed by Quinn Lead', 'Detected automatically', 'Proposed manually', 'Proposed by an agent'])
  })

  it('shows no proposer line on the Quarantined tab', () => {
    hooks.requests = [row('a', 'QUARANTINED', { detection_method: 'manual', proposed_by_name: 'Quinn Lead' })]
    renderBody('active')
    expect(screen.queryByText('Proposed by Quinn Lead')).toBeNull()
  })
})

describe('D-52: an unmeasured flip figure says so', () => {
  it('reads "not measured" with a reason, never a bare dash', () => {
    hooks.requests = [row('a', 'PROPOSED', { flip_rate: null, flip_window_size: null })]
    renderBody('proposals')
    const tr = screen.getByTestId('quarantine-row')
    const cells = within(tr).getAllByText('not measured')
    expect(cells).toHaveLength(2)
    for (const cell of cells) expect(cell.getAttribute('title')).toMatch(/No pass or fail result/)
  })
})
