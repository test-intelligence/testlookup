/**
 * W22a ACT-05 — the release page's hold strip (contract §4.1, §4.4 G-C).
 *
 * A QA lead sees `Place hold`; a viewer sees nothing on an unheld release and
 * the `On hold` chip + `Held: <reason>` on a held one; placing sends
 * `{entity_type: 'release', entity_id, reason}` and re-reads the detail; the
 * server's sentence shows inline. Reasons are React text.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ReleaseHoldStrip from './ReleaseHoldStrip'
import { HOLD_PROJECT_ID, HOLD_RELEASE_ID, SENTINEL_REASON, holdRef } from '@/test/retentionHoldMocks'

const perms = vi.hoisted(() => ({ isQaLead: false }))
const mocks = vi.hoisted(() => ({ place: vi.fn(), refresh: vi.fn(), success: vi.fn(), appMutate: vi.fn() }))

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaLead: perms.isQaLead }) }))
vi.mock('@/services/retentionHoldService', () => ({ placeRetentionHold: mocks.place }))
vi.mock('@/hooks/useRetentionHolds', () => ({ refreshRetentionHolds: mocks.refresh }))
vi.mock('react-hot-toast', () => ({ default: { success: mocks.success, error: vi.fn() } }))
vi.mock('@/utils/swrCacheMutate', () => ({ appMutate: mocks.appMutate }))

function strip(props: Partial<Parameters<typeof ReleaseHoldStrip>[0]> = {}) {
  const onChanged = vi.fn()
  const view = render(
    <ReleaseHoldStrip
      projectId={HOLD_PROJECT_ID}
      releaseId={HOLD_RELEASE_ID}
      releaseName="Payments 2026-Q4"
      holds={[]}
      onChanged={onChanged}
      {...props}
    />,
  )
  return { ...view, onChanged }
}

describe('ReleaseHoldStrip', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    perms.isQaLead = false
  })

  it('renders nothing for a viewer on an unheld release (and with no holds field at all)', () => {
    expect(strip().container).toBeEmptyDOMElement()
    expect(strip({ holds: undefined }).container).toBeEmptyDOMElement()
  })

  it('shows a viewer the chip and every live hold, with no Place button', () => {
    const { container } = strip({
      holds: [
        holdRef({ reason: SENTINEL_REASON, expires_at: null }),
        holdRef({ entity_type: 'project', entity_id: null, entity_label: 'the whole project', reason: 'Litigation sentinel' }),
      ],
    })
    expect(container.querySelector('[data-release-hold="held"]')).not.toBeNull()
    expect(screen.getByText('On hold')).toBeInTheDocument()
    expect(screen.getByText(`Held: ${SENTINEL_REASON}`)).toBeInTheDocument()
    expect(screen.getByText('Held: Litigation sentinel')).toBeInTheDocument()
    expect(screen.getByText('the whole project · No expiry')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Place hold' })).toBeNull()
  })

  it('ignores an expired hold and another release’s hold, and folds more than three into +n more', () => {
    const other = holdRef({ entity_id: '00000000-0000-4000-8000-0000000000ff', reason: 'Other release' })
    const expired = holdRef({ reason: 'Old matter', expires_at: '2000-01-01T00:00:00Z' })
    expect(strip({ holds: [other, expired] }).container).toBeEmptyDOMElement()

    strip({ holds: [1, 2, 3, 4, 5].map((n) => holdRef({ reason: `Matter ${n} sentinel` })) })
    expect(screen.getByText('Held: Matter 3 sentinel')).toBeInTheDocument()
    expect(screen.queryByText('Held: Matter 4 sentinel')).toBeNull()
    expect(screen.getByText('+2 more')).toBeInTheDocument()
  })

  it('renders a reason as text, never markup', () => {
    const { container } = strip({ holds: [holdRef({ reason: '<img src=x onerror=alert(1)> sentinel' })] })
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('Held: <img src=x onerror=alert(1)> sentinel')).toBeInTheDocument()
  })

  it('lets a QA lead place a release hold: the body names the release, the detail is re-read', async () => {
    perms.isQaLead = true
    mocks.place.mockResolvedValue({ id: 'hold-new' })
    const { onChanged } = strip()
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: `  ${SENTINEL_REASON}  ` } })
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(mocks.place).toHaveBeenCalledWith(HOLD_PROJECT_ID, {
      entity_type: 'release', entity_id: HOLD_RELEASE_ID, reason: SENTINEL_REASON,
    })
    expect(mocks.refresh).toHaveBeenCalledWith(HOLD_PROJECT_ID)
    expect(mocks.appMutate).toHaveBeenCalledWith(['compliance-packs', HOLD_RELEASE_ID])
    expect(mocks.success).toHaveBeenCalledWith('Hold placed on Payments 2026-Q4')
    expect(screen.queryByLabelText('Reason')).toBeNull()
  })

  it('sends an expiry as the end of that day, and keeps a short reason client-side', async () => {
    perms.isQaLead = true
    mocks.place.mockResolvedValue({ id: 'hold-new' })
    strip()
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'ab' } })
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A reason of 3 to 500 characters is required.')
    expect(mocks.place).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: SENTINEL_REASON } })
    fireEvent.change(screen.getByLabelText('Expires (optional)'), { target: { value: '2031-03-04' } })
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    await waitFor(() => expect(mocks.place).toHaveBeenCalledTimes(1))
    const body = mocks.place.mock.calls[0][1]
    expect(body.expires_at).toBe(new Date(2031, 2, 4, 23, 59, 59).toISOString())
  })

  it('shows the server’s sentence inline and keeps the form open', async () => {
    perms.isQaLead = true
    mocks.place.mockRejectedValue({
      response: { status: 404, data: { detail: { code: 'entity_not_found', message: 'That release, run or compliance pack is not in this project.' } } },
    })
    const { onChanged } = strip()
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: SENTINEL_REASON } })
    fireEvent.click(screen.getByRole('button', { name: 'Place hold' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That release, run or compliance pack is not in this project.')
    expect(onChanged).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Reason')).toBeInTheDocument()
  })
})
