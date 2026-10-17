/**
 * W09 FLK-02: the quarantine policy card (Flaky tests › Policy).
 *
 *   - the defaults sentence is built from the answer (never literals) and only
 *     while the project has no row;
 *   - a QA lead edits and saves the WHOLE policy (the flip rate sent 0-1), a
 *     toast, and the saved answer fills the shared cache;
 *   - a value out of range blocks Save with the range beside the field;
 *   - everyone else: disabled inputs, the sentence, no Save;
 *   - a failed or malformed read: the sentence and Retry, never the form;
 *   - a refused save: the server's detail as text.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  policy: null as unknown,
  error: undefined as unknown,
  isLoading: false,
  mutate: vi.fn(),
  calls: [] as Array<[unknown, unknown]>,
}))

vi.mock('@/hooks/useQuarantinePolicy', () => ({
  useQuarantinePolicy: (projectId: unknown, enabled: unknown) => {
    state.calls.push([projectId, enabled])
    return { policy: state.policy, error: state.error, isLoading: state.isLoading, mutate: state.mutate }
  },
}))

const svc = vi.hoisted(() => ({ get: vi.fn(), update: vi.fn() }))
vi.mock('@/services/quarantinePolicyService', async (importActual) => {
  const actual = await importActual<typeof import('@/services/quarantinePolicyService')>()
  return { ...actual, quarantinePolicyService: svc }
})

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import QuarantinePolicyCard from './QuarantinePolicyCard'

const DEFAULTS = {
  project_id: 'p1',
  sla_days: 14,
  auto_create_defect: false,
  auto_promote: false,
  promote_after_passes: 20,
  detection_flip_rate_threshold: 0.2,
  detection_min_runs: 10,
  max_active_quarantined: 8,
  is_default: true,
}

function renderCard(options: { canEdit?: boolean; activeCount?: number | undefined } = {}) {
  const activeCount = 'activeCount' in options ? options.activeCount : 3
  return render(<QuarantinePolicyCard projectId="p1" canEdit={options.canEdit ?? true} activeCount={activeCount} />)
}

const card = () => document.querySelector('[data-quarantine-policy]') as HTMLElement
const sla = () => screen.getByLabelText('SLA (days a test may stay quarantined)')
const save = () => screen.getByRole('button', { name: 'Save' })

beforeEach(() => {
  vi.clearAllMocks()
  state.policy = DEFAULTS
  state.error = undefined
  state.isLoading = false
  state.calls = []
  state.mutate.mockResolvedValue(undefined)
})

describe('QuarantinePolicyCard', () => {
  it('reads the project policy (enabled) and states the defaults from the answer', () => {
    state.policy = { ...DEFAULTS, sla_days: 30, promote_after_passes: 5, detection_flip_rate_threshold: 0.35, detection_min_runs: 4 }
    renderCard()
    expect(state.calls).toContainEqual(['p1', true])
    expect(card()).toHaveTextContent('Using defaults: SLA 30 days, promote after 5 passes, detection 35 % over 4 runs.')
    expect(sla()).toHaveValue(30)
    expect(screen.getByLabelText('Detection threshold (flip rate, %)')).toHaveValue(35)
    expect(screen.getByLabelText('Minimum runs')).toHaveValue(4)
    expect(screen.getByLabelText('Warn when more than this many tests are quarantined')).toHaveValue(8)
    expect(screen.getByRole('checkbox', { name: /^Release automatically after/ })).not.toBeChecked()
    expect(screen.getByRole('spinbutton', { name: /consecutive passes/ })).toHaveValue(5)
    expect(screen.getByText('An internal TestLookup defect. No Jira issue is created.')).toBeInTheDocument()
    expect(card()).toHaveTextContent('3 of 8 active now.')
  })

  it('says nothing about defaults once the project has its own row', () => {
    state.policy = { ...DEFAULTS, is_default: false }
    renderCard()
    expect(card()).not.toHaveTextContent('Using defaults')
  })

  it('a QA lead saves the whole policy, the flip rate as 0-1, and the answer fills the cache', async () => {
    const saved = { ...DEFAULTS, sla_days: 7, auto_promote: true, is_default: false }
    svc.update.mockResolvedValue(saved)
    renderCard()
    fireEvent.change(sla(), { target: { value: '7' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /^Release automatically after/ }))
    fireEvent.click(save())
    await waitFor(() => expect(svc.update).toHaveBeenCalledTimes(1))
    expect(svc.update).toHaveBeenCalledWith('p1', {
      sla_days: 7,
      auto_create_defect: false,
      auto_promote: true,
      promote_after_passes: 20,
      detection_flip_rate_threshold: 0.2,
      detection_min_runs: 10,
      max_active_quarantined: 8,
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Quarantine policy saved'))
    expect(state.mutate).toHaveBeenCalledWith(saved)
  })

  it('sends a percent as the rate the server stores (35 % → 0.35), and the cap and the defect switch', async () => {
    svc.update.mockResolvedValue({ ...DEFAULTS, is_default: false })
    renderCard()
    fireEvent.change(screen.getByLabelText('Detection threshold (flip rate, %)'), { target: { value: '35' } })
    fireEvent.change(screen.getByLabelText('Warn when more than this many tests are quarantined'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /^File an internal defect when a quarantine is approved/ }))
    fireEvent.click(save())
    await waitFor(() => expect(svc.update).toHaveBeenCalledTimes(1))
    expect(svc.update.mock.calls[0][1]).toMatchObject({
      detection_flip_rate_threshold: 0.35,
      max_active_quarantined: 0,
      auto_create_defect: true,
    })
  })

  it.each([
    ['SLA (days a test may stay quarantined)', '400', '1-365 days'],
    ['SLA (days a test may stay quarantined)', '0', '1-365 days'],
    ['SLA (days a test may stay quarantined)', '7.5', '1-365 days'],
    ['Detection threshold (flip rate, %)', '120', '0-100 %'],
    ['Minimum runs', '0', '1-1000 runs'],
    ['Warn when more than this many tests are quarantined', '-1', '0-1000 tests'],
    ['Release after this many consecutive passes', '', '1-1000 passes'],
  ])('%s = %j blocks Save with the range beside the field', (label, value, range) => {
    renderCard()
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
    expect(card()).toHaveTextContent(range)
    expect(save()).toBeDisabled()
    fireEvent.click(save())
    expect(svc.update).not.toHaveBeenCalled()
  })

  it('a QA engineer sees the same card read-only: disabled inputs, the sentence, no Save', () => {
    renderCard({ canEdit: false })
    expect(card()).toHaveTextContent('Only a QA lead can change the quarantine policy.')
    for (const input of within(card()).getAllByRole('spinbutton')) expect(input).toBeDisabled()
    for (const box of within(card()).getAllByRole('checkbox')) expect(box).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(sla()).toHaveValue(14)
  })

  it('a refused save shows the server\'s detail as text, not as markup', async () => {
    svc.update.mockRejectedValue({ response: { data: { detail: '<b>Not a member</b> of this project' } } })
    renderCard()
    fireEvent.click(save())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('<b>Not a member</b> of this project')
    expect(alert.querySelector('b')).toBeNull()
    expect(toast.success).not.toHaveBeenCalled()
  })

  it.each([
    ['a malformed answer', { sla_days: 'two weeks' }, undefined],
    ['an empty object', {}, undefined],
    ['no answer', null, undefined],
    ['a failed read', null, new Error('503')],
  ])('%s: the sentence and Retry, never the form', (_label, policy, error) => {
    state.policy = policy
    state.error = error
    renderCard()
    expect(card()).toHaveTextContent('The quarantine policy could not be read.')
    expect(within(card()).queryAllByRole('spinbutton')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(state.mutate).toHaveBeenCalledWith()
  })

  it('without the counts yet, says nothing about how many are active', () => {
    renderCard({ activeCount: undefined })
    expect(card()).not.toHaveTextContent('active now')
  })

  it('renders in the page: no dialog', () => {
    renderCard()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(card().tagName).toBe('SECTION')
  })
})
