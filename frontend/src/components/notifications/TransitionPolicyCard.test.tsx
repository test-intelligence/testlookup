/**
 * NTF-02: the project notification policy card on Email (SMTP) & channels.
 *
 * The real hook over a mocked service: All Projects asks nothing; the
 * defaults line comes from `is_default`; Save sends the WHOLE policy; state
 * changes off disables the seven checkboxes; a refused save shows the
 * server's detail as text; a failed or malformed read shows a sentence and
 * Retry, and no form (never placeholder defaults Save could write).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const svc = vi.hoisted(() => ({ getTransitionPolicy: vi.fn(), updateTransitionPolicy: vi.fn() }))
vi.mock('@/services/notificationPolicyService', () => ({
  getTransitionPolicy: svc.getTransitionPolicy,
  updateTransitionPolicy: svc.updateTransitionPolicy,
}))
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toasts }))

import TransitionPolicyCard, {
  POLICY_ALL_PROJECTS_TEXT,
  POLICY_DEFAULTS_TEXT,
  POLICY_UNREADABLE_TEXT,
} from './TransitionPolicyCard'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

const P = '00000000-0000-4000-8000-000000000001'
const SEVEN = [
  'test.newly_failing',
  'test.recovered',
  'test.newly_flaky',
  'test.quarantined',
  'test.unquarantined',
  'test.quarantine_stale',
  'test.ready_to_unquarantine',
]
const DEFAULTS = {
  project_id: P,
  transitions_enabled: true,
  per_run_events_enabled: false,
  enabled_events: SEVEN,
  consecutive_failure_threshold: 2,
  is_default: true,
}

function selectProject(id: string | null) {
  if (id === null || id === ALL_PROJECTS_ID) {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID, activeProject: null })
  } else {
    useProjectStore.setState({ activeProjectId: id, activeProject: { id, name: 'Checkout' } as never })
  }
}

function renderCard() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <TransitionPolicyCard />
      </MemoryRouter>
    </SWRConfig>,
  )
}

const card = () => screen.getByRole('region', { name: 'Project notification policy' })
const saveButton = () => within(card()).getByRole('button', { name: /^Save$/ })

describe('TransitionPolicyCard', () => {
  beforeEach(() => {
    svc.getTransitionPolicy.mockReset()
    svc.updateTransitionPolicy.mockReset()
    toasts.success.mockClear()
    selectProject(P)
  })

  it('is the section the hints link to', () => {
    selectProject(ALL_PROJECTS_ID)
    renderCard()
    expect(card()).toHaveAttribute('id', 'notification-policy')
    expect(card()).toHaveAttribute('data-notification-policy')
  })

  it('All Projects: one line, and no service call', async () => {
    selectProject(ALL_PROJECTS_ID)
    renderCard()
    expect(within(card()).getByText(POLICY_ALL_PROJECTS_TEXT)).toBeInTheDocument()
    expect(within(card()).queryByRole('button', { name: /^Save$/ })).toBeNull()
    await new Promise(r => setTimeout(r, 10))
    expect(svc.getTransitionPolicy).not.toHaveBeenCalled()
  })

  it('a project with no row: the defaults line, read from is_default', async () => {
    svc.getTransitionPolicy.mockResolvedValue(DEFAULTS)
    renderCard()
    expect(await within(card()).findByText(POLICY_DEFAULTS_TEXT)).toBeInTheDocument()
    expect(svc.getTransitionPolicy).toHaveBeenCalledWith(P)
    expect(within(card()).getByRole('switch', { name: 'Notify on every run' })).toHaveAttribute('aria-checked', 'false')
    expect(within(card()).getByRole('switch', { name: 'Notify on state changes' })).toHaveAttribute('aria-checked', 'true')
  })

  it('a stored row: no defaults line', async () => {
    svc.getTransitionPolicy.mockResolvedValue({ ...DEFAULTS, is_default: false })
    renderCard()
    await within(card()).findByRole('switch', { name: 'Notify on every run' })
    expect(within(card()).queryByText(POLICY_DEFAULTS_TEXT)).toBeNull()
  })

  it('Notify on every run → Save sends the whole policy, toasts, and re-reads (the defaults line goes)', async () => {
    svc.getTransitionPolicy.mockResolvedValueOnce(DEFAULTS)
    svc.getTransitionPolicy.mockResolvedValueOnce({ ...DEFAULTS, per_run_events_enabled: true, is_default: false })
    svc.updateTransitionPolicy.mockResolvedValue({ ...DEFAULTS, per_run_events_enabled: true, is_default: false })
    renderCard()
    fireEvent.click(await within(card()).findByRole('switch', { name: 'Notify on every run' }))
    fireEvent.click(saveButton())
    await waitFor(() => expect(svc.updateTransitionPolicy).toHaveBeenCalledTimes(1))
    expect(svc.updateTransitionPolicy).toHaveBeenCalledWith(P, {
      transitions_enabled: true,
      per_run_events_enabled: true,
      enabled_events: SEVEN,
      consecutive_failure_threshold: 2,
    })
    await waitFor(() => expect(toasts.success).toHaveBeenCalledWith('Notification policy saved'))
    await waitFor(() => expect(within(card()).queryByText(POLICY_DEFAULTS_TEXT)).toBeNull())
    expect(svc.getTransitionPolicy).toHaveBeenCalledTimes(2)
    expect(within(card()).getByRole('switch', { name: 'Notify on every run' })).toHaveAttribute('aria-checked', 'true')
  })

  it('state changes off disables the seven checkboxes and keeps their selection in the body', async () => {
    svc.getTransitionPolicy.mockResolvedValue({ ...DEFAULTS, enabled_events: ['test.recovered', 'test.newly_failing'] })
    svc.updateTransitionPolicy.mockResolvedValue({})
    renderCard()
    const recovered = (await within(card()).findByRole('checkbox', { name: 'Test recovered (transition)' })) as HTMLInputElement
    expect(recovered.disabled).toBe(false)
    fireEvent.click(within(card()).getByRole('switch', { name: 'Notify on state changes' }))
    const boxes = within(card()).getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes).toHaveLength(7)
    for (const box of boxes) expect(box.disabled).toBe(true)
    fireEvent.click(saveButton())
    await waitFor(() => expect(svc.updateTransitionPolicy).toHaveBeenCalledTimes(1))
    // The server's order, not the stored one.
    expect(svc.updateTransitionPolicy.mock.calls[0][1]).toEqual({
      transitions_enabled: false,
      per_run_events_enabled: false,
      enabled_events: ['test.newly_failing', 'test.recovered'],
      consecutive_failure_threshold: 2,
    })
  })

  it('unticking an event and changing the threshold reach the body', async () => {
    svc.getTransitionPolicy.mockResolvedValue(DEFAULTS)
    svc.updateTransitionPolicy.mockResolvedValue({})
    renderCard()
    fireEvent.click(await within(card()).findByRole('checkbox', { name: 'Test newly flaky (transition)' }))
    fireEvent.change(within(card()).getByLabelText(/Failed runs in a row/), { target: { value: '5' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(svc.updateTransitionPolicy).toHaveBeenCalledTimes(1))
    expect(svc.updateTransitionPolicy.mock.calls[0][1]).toEqual({
      transitions_enabled: true,
      per_run_events_enabled: false,
      enabled_events: SEVEN.filter(e => e !== 'test.newly_flaky'),
      consecutive_failure_threshold: 5,
    })
  })

  it('a threshold outside 1-20 blocks Save with the range as text', async () => {
    svc.getTransitionPolicy.mockResolvedValue(DEFAULTS)
    renderCard()
    fireEvent.change(await within(card()).findByLabelText(/Failed runs in a row/), { target: { value: '21' } })
    expect(within(card()).getByText('Between 1 and 20.')).toBeInTheDocument()
    expect(saveButton()).toBeDisabled()
    fireEvent.click(saveButton())
    expect(svc.updateTransitionPolicy).not.toHaveBeenCalled()
  })

  it("a refused save renders the server's detail as text under the form", async () => {
    svc.getTransitionPolicy.mockResolvedValue(DEFAULTS)
    svc.updateTransitionPolicy.mockRejectedValue({
      response: { status: 422, data: { detail: 'Unknown transition events: <b>x</b>' } },
    })
    renderCard()
    fireEvent.click(await within(card()).findByRole('switch', { name: 'Notify on every run' }))
    fireEvent.click(saveButton())
    const alert = await within(card()).findByRole('alert')
    // React text: the markup is shown, never parsed.
    expect(alert).toHaveTextContent('Unknown transition events: <b>x</b>')
    expect(alert.querySelector('b')).toBeNull()
    expect(toasts.success).not.toHaveBeenCalled()
  })

  it('a failed read: the sentence and Retry, and no form', async () => {
    svc.getTransitionPolicy.mockRejectedValueOnce({ response: { status: 500 } })
    svc.getTransitionPolicy.mockResolvedValueOnce(DEFAULTS)
    renderCard()
    expect(await within(card()).findByText(POLICY_UNREADABLE_TEXT)).toBeInTheDocument()
    expect(within(card()).queryByRole('button', { name: /^Save$/ })).toBeNull()
    expect(within(card()).queryByRole('switch')).toBeNull()
    fireEvent.click(within(card()).getByRole('button', { name: 'Retry' }))
    expect(await within(card()).findByText(POLICY_DEFAULTS_TEXT)).toBeInTheDocument()
  })

  it.each([
    ['an empty object', {}],
    ['a string', 'nope'],
    ['wrong-typed switches', { ...DEFAULTS, per_run_events_enabled: 'false' }],
    ['events not a list', { ...DEFAULTS, enabled_events: 'all' }],
  ])('a malformed answer (%s) reads as unreadable: no form', async (_name, answer) => {
    svc.getTransitionPolicy.mockResolvedValue(answer)
    renderCard()
    expect(await within(card()).findByText(POLICY_UNREADABLE_TEXT)).toBeInTheDocument()
    expect(within(card()).queryByRole('button', { name: /^Save$/ })).toBeNull()
  })
})
