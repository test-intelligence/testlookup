/**
 * W22a — Hold / Retire / Delete on a compliance-pack row (contract §4.1, §4.4 G-C).
 *
 * Role gates (Hold QA_LEAD+, Retire / Delete ADMIN); held → Retire / Delete
 * disabled with the `Held:` title; an open window → Delete disabled; retire
 * sends `{reason, confirmation_id}` only when the typed id matches; a 409's
 * `detail.blockers` (the wire shape) are listed inline.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PackLifecycleControls, { DELETE_PACK_QUESTION, PackHoldChip } from './PackLifecycleControls'
import type { CompliancePackRead } from '@/services/compliancePackService'
import { HOLD_PROJECT_ID, HOLD_RELEASE_ID, SENTINEL_REASON, holdRef } from '@/test/retentionHoldMocks'

const perms = vi.hoisted(() => ({ isQaLead: true, isAdmin: true }))
const mocks = vi.hoisted(() => ({ retire: vi.fn(), remove: vi.fn(), place: vi.fn(), success: vi.fn() }))

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ ...perms }) }))
vi.mock('@/services/compliancePackService', () => ({
  compliancePackService: { retire: mocks.retire, remove: mocks.remove },
}))
vi.mock('@/services/retentionHoldService', () => ({ placeRetentionHold: mocks.place }))
vi.mock('@/hooks/useRetentionHolds', () => ({ refreshRetentionHolds: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: { success: mocks.success, error: vi.fn() } }))

const PACK_ID = '00000000-0000-4000-8000-0000000c0c01'

function pack(overrides: Partial<CompliancePackRead> = {}): CompliancePackRead {
  return {
    id: PACK_ID, release_id: HOLD_RELEASE_ID, project_id: HOLD_PROJECT_ID, test_run_id: null,
    minio_key: 'packs/sentinel.zip', manifest_sha256: 'c'.repeat(64), file_count: 3, bytes: 1024,
    retention_expires_at: '2001-01-01T00:00:00Z', generated_at: '2000-01-01T00:00:00Z',
    generated_by_user_id: null, metadata_snapshot: null, notes: null, holds: [],
    ...overrides,
  }
}

function controls(p: CompliancePackRead = pack()) {
  const onChanged = vi.fn()
  render(<div className="flex flex-wrap"><PackLifecycleControls pack={p} onChanged={onChanged} /></div>)
  return onChanged
}

const button = (name: string) => screen.queryByRole('button', { name })

describe('PackLifecycleControls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    perms.isQaLead = true
    perms.isAdmin = true
  })

  it('gates by role: a tester nothing, a QA lead Hold only, an admin all three', () => {
    perms.isQaLead = false
    perms.isAdmin = false
    controls()
    expect(button('Hold')).toBeNull()

    perms.isQaLead = true
    controls()
    expect(button('Hold')).not.toBeNull()
    expect(button('Retire')).toBeNull()
    expect(button('Delete')).toBeNull()
  })

  it('disables Retire and Delete with the Held sentence while a hold covers the pack (the release’s too)', () => {
    controls(pack({ holds: [holdRef({ reason: SENTINEL_REASON })] }))
    for (const name of ['Retire', 'Delete']) {
      expect(button(name)).toBeDisabled()
      expect(button(name)).toHaveAttribute('title', `Held: ${SENTINEL_REASON}`)
    }
    expect(button('Hold')).toBeEnabled()
  })

  it('shows the On hold chip only for a live covering hold', () => {
    const { container, rerender } = render(<PackHoldChip pack={pack({ holds: [holdRef()] })} />)
    expect(screen.getByText('On hold')).toHaveAttribute('title', `Held: ${SENTINEL_REASON}`)
    rerender(<PackHoldChip pack={pack({ holds: [holdRef({ expires_at: '2000-01-01T00:00:00Z' })] })} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<PackHoldChip pack={pack({ holds: undefined })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('disables Delete while the retention window is open, naming the date; Retire stays', () => {
    controls(pack({ retention_expires_at: '2999-06-30T00:00:00Z' }))
    expect(button('Delete')).toBeDisabled()
    expect(button('Delete')?.getAttribute('title')).toMatch(/^Retire the pack first — its retention window runs to /)
    expect(button('Retire')).toBeEnabled()
  })

  it('retires only once the typed id matches, sending {reason, confirmation_id}', async () => {
    mocks.retire.mockResolvedValue({ pack_id: PACK_ID, retention_expires_at: '2026-10-28T00:00:00Z', purgeable_now: true })
    const onChanged = controls()
    fireEvent.click(button('Retire') as HTMLElement)
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: SENTINEL_REASON } })
    fireEvent.change(screen.getByLabelText('Type the pack id to confirm'), { target: { value: PACK_ID.slice(0, 20) } })
    expect(button('Retire pack')).toBeDisabled()
    fireEvent.submit(screen.getByLabelText('Reason').closest('form') as HTMLFormElement)
    expect(mocks.retire).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Type the pack id to confirm'), { target: { value: PACK_ID } })
    fireEvent.click(button('Retire pack') as HTMLElement)
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(mocks.retire).toHaveBeenCalledWith(PACK_ID, { reason: SENTINEL_REASON, confirmation_id: PACK_ID })
  })

  it('asks inline before a delete, then sends it', async () => {
    mocks.remove.mockResolvedValue(undefined)
    const onChanged = controls()
    fireEvent.click(button('Delete') as HTMLElement)
    expect(screen.getByText(DELETE_PACK_QUESTION)).toBeInTheDocument()
    expect(mocks.remove).not.toHaveBeenCalled()
    fireEvent.click(button('Delete pack') as HTMLElement)
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(mocks.remove).toHaveBeenCalledWith(PACK_ID)
  })

  it('lists a 409’s detail.blockers inline (a hold placed after the page loaded)', async () => {
    mocks.remove.mockRejectedValue({
      response: { status: 409, data: { detail: { blockers: [`Held: ${SENTINEL_REASON}`, 'retention window open'] } } },
    })
    const onChanged = controls()
    fireEvent.click(button('Delete') as HTMLElement)
    fireEvent.click(button('Delete pack') as HTMLElement)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(`Held: ${SENTINEL_REASON}`)
    expect(alert).toHaveTextContent('retention window open')
    expect(onChanged).not.toHaveBeenCalled()

    mocks.retire.mockRejectedValue({ response: { status: 409, data: { detail: { blockers: 'not a list' } } } })
    fireEvent.click(button('Retire') as HTMLElement)
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: SENTINEL_REASON } })
    fireEvent.change(screen.getByLabelText('Type the pack id to confirm'), { target: { value: PACK_ID } })
    fireEvent.click(button('Retire pack') as HTMLElement)
    expect(await screen.findByText('The server refused the request.')).toBeInTheDocument()
  })

  it('places a hold on the pack itself', async () => {
    mocks.place.mockResolvedValue({ id: 'hold-new' })
    const onChanged = controls()
    fireEvent.click(button('Hold') as HTMLElement)
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: SENTINEL_REASON } })
    fireEvent.click(button('Place hold') as HTMLElement)
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(mocks.place).toHaveBeenCalledWith(HOLD_PROJECT_ID, {
      entity_type: 'compliance_pack', entity_id: PACK_ID, reason: SENTINEL_REASON,
    })
  })
})
