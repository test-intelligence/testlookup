/**
 * NTF-03 / NTF-04: a subscription row's in-place scope fix. The badge for a
 * row that cannot deliver, the release / suite fix (the release select only
 * when the top bar shows the row's project), Change view (which asks for the
 * views only when clicked), and the 422 text in the row.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DigestSubscription } from '@/services/digestService'

const svc = vi.hoisted(() => ({ updateSubscription: vi.fn() }))
vi.mock('@/services/digestService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/digestService')>()),
  updateSubscription: svc.updateSubscription,
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import SubscriptionScopeFix from './SubscriptionScopeFix'
import type { ViewSource } from './DigestViewPicker'

const P = 'p1'
const RELEASES = [
  { id: 'r1', name: '2026.08', status: 'released' },
  { id: 'r2', name: '2026.10', status: 'in_progress', is_active: true },
]

function sub(over: Partial<DigestSubscription> & Record<string, unknown> = {}): DigestSubscription {
  return {
    id: 's1', user_id: 'u', project_id: P, saved_view_id: null, name: 'n', schedule: 'PER_RELEASE', channel: 'email',
    is_active: true, is_paused: false, scope_type: null, scope_value: null, trigger_filter: null, send_when_unchanged: true,
    report_attachment: false, last_delivered_at: null, next_delivery_at: null, delivery_count: 0,
    created_at: '2026-10-01T00:00:00Z', updated_at: null, ...over,
  } as DigestSubscription
}

function viewSource(over: Partial<ViewSource> = {}): ViewSource {
  return { requested: false, views: [], loading: false, error: false, request: vi.fn(), ...over }
}

const REPORT = {
  id: 'v1', user_id: 'u', project_id: P, name: 'Checkout regressions', description: null, page: 'trends' as const,
  filters: { kind: 'report_view', page: 'trends', window: 7 }, is_shared: false, is_default: false,
  created_at: '2026-10-01T00:00:00Z', updated_at: null,
}

describe('SubscriptionScopeFix', () => {
  beforeEach(() => {
    svc.updateSubscription.mockReset().mockResolvedValue({})
  })

  it('an unscoped Per Release row of the top bar project: badge, release select, PATCH on Save', async () => {
    const onSaved = vi.fn()
    render(<SubscriptionScopeFix sub={sub({ scope_problem: 'unscoped' })} releases={RELEASES} topBarProjectId={P} views={viewSource()} onSaved={onSaved} />)
    expect(screen.getByText('Not delivering — choose a release')).toHaveAttribute('data-scope-problem', 'unscoped')
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map(o => o.textContent)).toEqual(['Choose a release…', '2026.10', '2026.08'])
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    fireEvent.change(select, { target: { value: 'r2' } })
    fireEvent.click(save)
    await waitFor(() => expect(svc.updateSubscription).toHaveBeenCalledWith('s1', { scope_type: 'release', scope_value: 'r2' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
  })

  it('a row of another project (or All Projects): no release select, a sentence instead', () => {
    render(<SubscriptionScopeFix sub={sub({ scope_problem: 'release_missing' })} releases={RELEASES} topBarProjectId={null} views={viewSource()} onSaved={vi.fn()} />)
    expect(screen.getByText('Not delivering — its release no longer exists')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByText(/Pick this digest's project in the top bar/)).toBeInTheDocument()
  })

  it('a 422 renders in the row as text', async () => {
    svc.updateSubscription.mockRejectedValue({ response: { status: 422, data: { detail: 'Choose a release of this project.' } } })
    const onSaved = vi.fn()
    render(<SubscriptionScopeFix sub={sub({ scope_problem: 'unscoped' })} releases={RELEASES} topBarProjectId={P} views={viewSource()} onSaved={onSaved} />)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'r1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose a release of this project.')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('an unscoped Per Suite row: the suite, trimmed', async () => {
    render(<SubscriptionScopeFix sub={sub({ schedule: 'PER_SUITE', scope_problem: 'unscoped' })} releases={[]} topBarProjectId={P} views={viewSource()} onSaved={vi.fn()} />)
    expect(screen.getByText('Not delivering — choose a suite')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Suite name'), { target: { value: ' smoke ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(svc.updateSubscription).toHaveBeenCalledWith('s1', { scope_type: 'suite', scope_value: 'smoke' }))
  })

  it('a healthy Per Release row renders nothing', () => {
    const { container } = render(<SubscriptionScopeFix sub={sub({ scope_label: '2026.10' })} releases={RELEASES} topBarProjectId={P} views={viewSource()} onSaved={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('Change view asks for the views only when clicked, then PATCHes the chosen view', async () => {
    const request = vi.fn()
    const weekly = sub({ schedule: 'WEEKLY', scope_type: 'project' })
    const { rerender } = render(<SubscriptionScopeFix sub={weekly} releases={[]} topBarProjectId={P} views={viewSource({ request })} onSaved={vi.fn()} />)
    expect(request).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Change view' }))
    expect(request).toHaveBeenCalledTimes(1)
    rerender(<SubscriptionScopeFix sub={weekly} releases={[]} topBarProjectId={P} views={viewSource({ requested: true, views: [REPORT], request })} onSaved={vi.fn()} />)
    const picker = screen.getByRole('combobox') as HTMLSelectElement
    expect([...picker.options].map(o => o.textContent)).toEqual(['No view (whole project)', 'Checkout regressions'])
    fireEvent.change(picker, { target: { value: 'v1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(svc.updateSubscription).toHaveBeenCalledWith('s1', { saved_view_id: 'v1' }))
  })

  it('a deleted view says so; No view clears it with a null', async () => {
    const deleted = sub({ schedule: 'DAILY', scope_type: 'project', saved_view_id: null, saved_view_name: 'Gone', scope_problem: 'saved_view_deleted' })
    render(<SubscriptionScopeFix sub={deleted} releases={[]} topBarProjectId={P} views={viewSource({ requested: true, views: [REPORT] })} onSaved={vi.fn()} />)
    const fix = within(document.querySelector('[data-scope-fix="s1"]') as HTMLElement)
    expect(fix.getByText('View deleted — this digest covers the whole project')).toBeInTheDocument()
    fireEvent.click(fix.getByRole('button', { name: 'Change view' }))
    fireEvent.click(fix.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(svc.updateSubscription).toHaveBeenCalledWith('s1', { saved_view_id: null }))
  })

  it('a row of another project than the top bar: no Change view; a view problem says where to change it', () => {
    const { container, rerender } = render(
      <SubscriptionScopeFix sub={sub({ schedule: 'WEEKLY', scope_type: 'project', saved_view_id: 'v1', saved_view_name: 'X' })} releases={[]} topBarProjectId="p2" views={viewSource()} onSaved={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
    rerender(
      <SubscriptionScopeFix sub={sub({ schedule: 'WEEKLY', scope_type: 'project', saved_view_name: 'X', scope_problem: 'saved_view_unavailable' })} releases={[]} topBarProjectId="p2" views={viewSource()} onSaved={vi.fn()} />,
    )
    expect(screen.getByText('View no longer available to you — the whole project')).toBeInTheDocument()
    expect(screen.getByText(/Pick this digest's project in the top bar to change its view/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Change view' })).toBeNull()
  })

  it('the picker offers only report views of the row\'s project', () => {
    const own = { ...REPORT, id: 'v-none', name: 'Mine, no project', project_id: null }
    const other = { ...REPORT, id: 'v-other', name: 'Other project', project_id: 'p2' }
    render(<SubscriptionScopeFix sub={sub({ schedule: 'WEEKLY', scope_type: 'project' })} releases={[]} topBarProjectId={P} views={viewSource({ requested: true, views: [REPORT, own, other] })} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Change view' }))
    const picker = screen.getByRole('combobox') as HTMLSelectElement
    expect([...picker.options].map(o => o.textContent)).toEqual(['No view (whole project)', 'Checkout regressions'])
  })

  it('Per Run and project-less rows offer no Change view', () => {
    const { container, rerender } = render(<SubscriptionScopeFix sub={sub({ schedule: 'PER_RUN', scope_type: null })} releases={[]} topBarProjectId={P} views={viewSource()} onSaved={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
    rerender(<SubscriptionScopeFix sub={sub({ schedule: 'WEEKLY', project_id: null })} releases={[]} topBarProjectId={null} views={viewSource()} onSaved={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('garbage enrichment renders no badge and never crashes', () => {
    render(<SubscriptionScopeFix sub={sub({ scope_problem: 42, scope_label: {}, saved_view_name: [] } as never)} releases={RELEASES} topBarProjectId={P} views={viewSource()} onSaved={vi.fn()} />)
    expect(document.querySelector('[data-scope-problem]')).toBeNull()
  })
})
