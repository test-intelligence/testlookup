import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CompliancePackPanel from '@/components/compliance/CompliancePackPanel'
import type { ActivityEvent } from '@/services/activityService'
import type { ProjectCompliancePack } from '@/services/compliancePackService'
import { AgingSignals, CompliancePacks, RecentActivity, ShippingThisWeek } from './RightRail'
import type { DerivedRelease, ReleaseStage } from './types'

/**
 * The rail's two server-backed panels run on their REAL hooks
 * (`useProjectCompliancePacks`, `useActivityFeed`) over mocked services, so
 * what is asserted is what the page sends and renders, not what a mocked hook
 * was told to return.
 */
const svc = vi.hoisted(() => ({
  listForProject: vi.fn(),
  download: vi.fn(),
  listActivity: vi.fn(),
  toastError: vi.fn(),
}))

vi.mock('@/services/compliancePackService', async () => {
  const actual = await vi.importActual<typeof import('@/services/compliancePackService')>('@/services/compliancePackService')
  return {
    ...actual,
    compliancePackService: {
      ...actual.compliancePackService,
      listForProject: (...a: unknown[]) => svc.listForProject(...a),
      download: (...a: unknown[]) => svc.download(...a),
    },
  }
})

vi.mock('@/services/activityService', async () => {
  const actual = await vi.importActual<typeof import('@/services/activityService')>('@/services/activityService')
  return { ...actual, listActivity: (...a: unknown[]) => svc.listActivity(...a) }
})

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: (m: string) => svc.toastError(m) } }))

// The release panel, for the "same pack, same text" check: its own per-release list and role.
const panelPacks = vi.hoisted(() => ({ packs: [] as unknown[] }))
vi.mock('@/hooks/useCompliancePacks', () => ({
  useCompliancePacks: () => ({ packs: panelPacks.packs, isLoading: false, isError: false, refresh: vi.fn() }),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ canAccessManagement: false }) }))

const PROJECT = '11111111-1111-4111-8111-111111111111'
const SHA = 'ab12cd34' + 'ef567890'.repeat(7)

beforeEach(() => {
  vi.clearAllMocks()
  panelPacks.packs = []
})

function renderRail(node: ReactNode) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <MemoryRouter>{node}</MemoryRouter>
    </SWRConfig>,
  )
}

/**
 * A ``DerivedRelease`` with inert defaults; each test sets the fields its
 * panel reads.
 */
function makeRelease(overrides: Partial<DerivedRelease> & { stage: ReleaseStage }): DerivedRelease {
  return {
    source: { updated_at: '2026-09-18T11:00:00Z', created_at: '2026-09-01T00:00:00Z' } as DerivedRelease['source'],
    id: 'rel-1',
    name: 'Release',
    version: 'v1.0',
    ownerInitials: 'AB',
    ownerName: 'A B',
    hasOwner: true,
    isActive: false,
    externalUrl: null,
    sourceSystem: null,
    dueAt: null,
    gate: {
      decision: 'go', passRate: 98, denominator: 200, evidence: 200, runCount: 2,
      decidedAt: '2026-09-18T10:00:00Z', measured: true, insufficientReason: null, blockingTotal: 0,
    },
    totals: null,
    phases: [],
    blockers: [],
    blockerCount: 0,
    ...overrides,
  }
}

function pack(overrides: Partial<ProjectCompliancePack> = {}): ProjectCompliancePack {
  return {
    id: 'pack-1',
    release_id: 'rel-shipped',
    release_name: '2026.10',
    release_version: 'v10',
    project_id: PROJECT,
    project_name: 'Checkout',
    bytes: 422_195, // 412.3 KB
    file_count: 9,
    manifest_sha256: SHA,
    generated_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
    retention_expires_at: '2033-09-18T11:00:00Z',
    retired: false,
    ...overrides,
  }
}

function event(id: string, overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id,
    project_id: PROJECT,
    release_id: 'rel-1',
    occurred_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    category: 'release',
    event_type: 'release.activated',
    actor: { type: 'user', id: 'u-1', name: 'Anand', ref: 'anand' },
    entity: { type: 'release', id: 'rel-1', label: 'v2.5', href: '/releases/rel-1' },
    target: null,
    summary: 'Release v2.5 was activated',
    context: null,
    has_diff: false,
    source: null,
    group_key: null,
    ...overrides,
  }
}

const page = (items: ActivityEvent[]) => ({ items, next_cursor: null, ledger_started_at: '2026-09-01T00:00:00Z', window: { since: null, until: null } })

// ── Shipping this week ─────────────────────────────────────────────────────

describe('ShippingThisWeek', () => {
  it('keeps in-progress releases with a due date inside the 7-day window', () => {
    const today = new Date()
    render(
      <ShippingThisWeek
        releases={[makeRelease({ id: 'soon', name: 'Soon', stage: 'in_progress', dueAt: today.toISOString() })]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.getByText('Soon')).toBeInTheDocument()
    expect(screen.queryByText(/No releases due/i)).not.toBeInTheDocument()
  })

  it('drops releases whose dueAt is null (the narrowed-filter guard)', () => {
    render(
      <ShippingThisWeek
        releases={[makeRelease({ id: 'nodate', name: 'NoDate', stage: 'in_progress', dueAt: null })]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.queryByText('NoDate')).not.toBeInTheDocument()
    expect(screen.getByText(/No releases due/i)).toBeInTheDocument()
  })

  it('drops releases that are not in progress even when a due date is set', () => {
    const today = new Date()
    render(
      <ShippingThisWeek
        releases={[makeRelease({ id: 'done', name: 'Done', stage: 'released', dueAt: today.toISOString() })]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.queryByText('Done')).not.toBeInTheDocument()
    expect(screen.getByText(/No releases due/i)).toBeInTheDocument()
  })

  it('counts every blocker the server stored, not only the few the card keeps', () => {
    const blocker = { id: 'b', severity: 'red' as const, title: 'x is FAILED', context: '', action: { label: 'Open gate →', href: '/releases/r#gate' } }
    render(
      <ShippingThisWeek
        releases={[makeRelease({
          id: 'nogo', name: 'NoGo', stage: 'in_progress', dueAt: new Date().toISOString(),
          blockers: [blocker, blocker, blocker], blockerCount: 26,
        })]}
        onOpen={vi.fn()}
      />,
    )
    expect(screen.getByText('v1.0 · 26 blockers')).toBeInTheDocument()
  })
})

// ── Aging signals ──────────────────────────────────────────────────────────

describe('AgingSignals', () => {
  it('reads the UAT rule from the phase type, not from a phase-template key', () => {
    const old = { updated_at: '2026-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z' } as DerivedRelease['source']
    render(
      <AgingSignals
        releases={[makeRelease({
          id: 'uat', name: 'UatRelease', stage: 'in_progress', source: old,
          phases: [{ key: 'ph-1', label: 'Customer acceptance', phaseType: 'uat', state: 'active' }],
        })]}
        onOpen={vi.fn()}
      />,
    )
    expect(screen.getByText('UAT pending > 2 days')).toBeInTheDocument()
  })
})

// ── Compliance packs ───────────────────────────────────────────────────────

/**
 * REL-04: the rail lists the packs that EXIST.
 *
 * It used to derive its rows from release stage alone and render
 * `4.2 MB · sha256:d6b975f7…` [Download] where the size was a literal, the
 * checksum a 32-bit hash of the release id, and the buttons inert, for
 * releases with no pack at all. Every figure now comes from
 * `GET /api/v1/compliance-packs`.
 */
describe('CompliancePacks — the packs that exist', () => {
  const shipped = makeRelease({ stage: 'released', id: 'rel-shipped', name: '2026.10' })

  it('asks for the project’s packs and prints the real size, the first 8 hex and the age', async () => {
    svc.listForProject.mockResolvedValue({ items: [pack()], total: 1 })
    renderRail(<CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />)

    expect(await screen.findByText('2026.10 v10')).toBeInTheDocument()
    expect(svc.listForProject).toHaveBeenCalledWith({ projectId: PROJECT, limit: 10 })
    const row = screen.getByText('2026.10 v10').closest('li') as HTMLElement
    expect(row).toHaveTextContent('412.3 KB · sha256:ab12cd34 · 2h ago')
    expect(row).not.toHaveTextContent(/MB/)
  })

  it('prints the same size and the same digest as the release panel for one pack', async () => {
    svc.listForProject.mockResolvedValue({ items: [pack()], total: 1 })
    panelPacks.packs = [{ ...pack(), test_run_id: null, minio_key: 'k', generated_by_user_id: null, metadata_snapshot: null, notes: null }]
    renderRail(
      <>
        <CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />
        <CompliancePackPanel releaseId="rel-shipped" releaseName="2026.10" />
      </>,
    )

    const railRow = (await screen.findByText('2026.10 v10')).closest('li') as HTMLElement
    const railHex = /sha256:([0-9a-f]{8})/.exec(railRow.textContent ?? '')?.[1]
    const panelDigest = screen.getByText(/^sha256: [0-9a-f]{64}$/).textContent ?? ''
    expect(railHex).toBe(SHA.slice(0, 8))
    expect(panelDigest).toBe(`sha256: ${SHA}`)
    expect(panelDigest.startsWith(`sha256: ${railHex}`)).toBe(true)
    expect(screen.getByText(/412\.3 KB · 9 files/)).toBeInTheDocument()
    expect(railRow).toHaveTextContent('412.3 KB')
  })

  it('counts the server total, not the rows on screen, and shows at most five packs', async () => {
    const seven = Array.from({ length: 7 }, (_, i) => pack({ id: `pack-${i}`, release_id: `rel-${i}`, release_name: `R${i}`, release_version: null }))
    svc.listForProject.mockResolvedValue({ items: seven, total: 12 })
    renderRail(<CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />)

    await screen.findByText('R0')
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(document.querySelectorAll('[data-rail-pack]')).toHaveLength(5)
    expect(screen.queryByText('R5')).not.toBeInTheDocument()
    // Five packs leave no room for "Ready for a pack" rows.
    expect(screen.queryByText('Ready for a pack')).not.toBeInTheDocument()
  })

  it('Download fetches the pack itself, by its id', async () => {
    svc.listForProject.mockResolvedValue({ items: [pack()], total: 1 })
    svc.download.mockResolvedValue(undefined)
    renderRail(<CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }))

    await waitFor(() => expect(svc.download).toHaveBeenCalledTimes(1))
    expect(svc.download.mock.calls[0][0]).toBe('pack-1')
    expect(svc.download.mock.calls[0][1]).toMatch(/^testlookup-compliance-2026\.10-\d{4}-\d{2}-\d{2}\.zip$/)
  })

  it('a failed download toasts the reason, as the release panel does', async () => {
    svc.listForProject.mockResolvedValue({ items: [pack()], total: 1 })
    svc.download.mockRejectedValue(new Error('Request failed with status code 404'))
    renderRail(<CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }))

    await waitFor(() => expect(svc.toastError).toHaveBeenCalledWith('Download failed: Request failed with status code 404'))
  })

  it('names a pack whose release was deleted, and tags a retired one', async () => {
    svc.listForProject.mockResolvedValue({
      items: [
        pack({ id: 'orphan', release_id: null, release_name: null, release_version: null }),
        pack({ id: 'old', release_name: '2025.01', release_version: null, retired: true }),
      ],
      total: 2,
    })
    renderRail(<CompliancePacks releases={[]} onOpen={vi.fn()} projectId={PROJECT} />)

    expect(await screen.findByText('(release deleted)')).toBeInTheDocument()
    const retired = screen.getByText('2025.01').closest('li') as HTMLElement
    expect(within(retired).getByText('Retired')).toBeInTheDocument()
    const orphan = screen.getByText('(release deleted)').closest('li') as HTMLElement
    expect(within(orphan).queryByText('Retired')).not.toBeInTheDocument()
  })

  it('fills the rest with releases ready for a pack, never one that has a pack or a No-Go', async () => {
    svc.listForProject.mockResolvedValue({ items: [pack()], total: 1 })
    const onOpen = vi.fn()
    const nogo = makeRelease({ stage: 'in_progress', id: 'rel-nogo', name: 'NoGo', gate: { ...makeRelease({ stage: 'in_progress' }).gate, decision: 'no_go' } })
    const wip = makeRelease({ stage: 'in_progress', id: 'rel-wip', name: 'Wip', gate: { ...makeRelease({ stage: 'in_progress' }).gate, decision: 'never_evaluated' } })
    const planned = makeRelease({ stage: 'planning', id: 'rel-plan', name: 'Plan' })
    renderRail(<CompliancePacks releases={[shipped, nogo, wip, planned]} onOpen={onOpen} projectId={PROJECT} />)

    const ready = await screen.findByText('Ready for a pack')
    const section = ready.closest('[data-rail-ready]') as HTMLElement
    expect(within(section).getByText('Wip v1.0')).toBeInTheDocument()
    expect(within(section).queryByText(/2026\.10/)).not.toBeInTheDocument()
    expect(within(section).queryByText(/NoGo/)).not.toBeInTheDocument()
    expect(within(section).queryByText(/Plan/)).not.toBeInTheDocument()
    fireEvent.click(within(section).getByRole('button', { name: /View packs/ }))
    expect(onOpen).toHaveBeenCalledWith('rel-wip')
  })

  it('with no pack at all: the honest empty line, and the releases ready for one', async () => {
    svc.listForProject.mockResolvedValue({ items: [], total: 0 })
    renderRail(<CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />)

    expect(await screen.findByText(/no compliance packs yet/i)).toBeInTheDocument()
    expect(screen.getByText('2026.10 v1.0')).toBeInTheDocument()
    expect(screen.queryByText(/sha256:/)).not.toBeInTheDocument()
  })

  it('a failed read says so; it is not "no packs"', async () => {
    svc.listForProject.mockRejectedValue(new Error('503'))
    renderRail(<CompliancePacks releases={[shipped]} onOpen={vi.fn()} projectId={PROJECT} />)

    expect(await screen.findByText('Could not load compliance packs.')).toBeInTheDocument()
    expect(screen.queryByText(/no compliance packs yet/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Ready for a pack')).not.toBeInTheDocument()
  })

  it('in the All-projects view asks for every accessible project (no project id)', async () => {
    svc.listForProject.mockResolvedValue({ items: [], total: 0 })
    renderRail(<CompliancePacks releases={[]} onOpen={vi.fn()} projectId={null} />)

    await screen.findByText(/no compliance packs yet/i)
    expect(svc.listForProject).toHaveBeenCalledWith({ projectId: null, limit: 10 })
  })
})

// ── Recent activity ────────────────────────────────────────────────────────

/**
 * REL-04: the project's release events from the activity ledger. It used to
 * be synthesised from release `updated_at` stamps ("2026.11 updated"), which
 * said nothing about what happened or who did it.
 */
describe('RecentActivity — the ledger’s release events', () => {
  it('asks the project feed for 8 release events and prints the ledger’s words, actor and age', async () => {
    svc.listActivity.mockResolvedValue(page([event('ev-1')]))
    const stale = makeRelease({ stage: 'in_progress', name: 'Stale', source: { updated_at: '2026-09-18T11:59:00Z', created_at: '2026-09-01T00:00:00Z' } as DerivedRelease['source'] })
    renderRail(<RecentActivity releases={[stale]} projectId={PROJECT} />)

    const link = await screen.findByRole('link', { name: /Release v2\.5 was activated · by Anand/ })
    expect(svc.listActivity).toHaveBeenCalledWith(PROJECT, { category: ['release'], limit: 8 })
    expect(link).toHaveTextContent('5m ago')
    expect(link).toHaveAttribute('href', '/activity?category=release&event=ev-1')
    // Nothing is re-derived from the release rows.
    expect(screen.queryByText(/Stale/)).not.toBeInTheDocument()
    expect(screen.queryByText(/updated$/)).not.toBeInTheDocument()
  })

  it('names a person only: an API key or the system gets the summary alone', async () => {
    svc.listActivity.mockResolvedValue(page([
      event('ev-key', { summary: 'Release 2026.12 created', actor: { type: 'api_key', id: 'k', name: 'ci-bot', ref: null } }),
      event('ev-sys', { summary: 'Release 2026.11 changed: owner', actor: { type: 'system', id: null, name: null, ref: null } }),
    ]))
    renderRail(<RecentActivity releases={[]} projectId={PROJECT} />)

    expect(await screen.findByRole('link', { name: /^\S+ ago Release 2026\.12 created$|Release 2026\.12 created/ })).not.toHaveTextContent('by ci-bot')
    expect(screen.getByRole('link', { name: /Release 2026\.11 changed: owner/ })).not.toHaveTextContent(' · by ')
  })

  it('shows at most 8 rows', async () => {
    svc.listActivity.mockResolvedValue(page(Array.from({ length: 10 }, (_, i) => event(`ev-${i}`, { summary: `Event ${i}` }))))
    renderRail(<RecentActivity releases={[]} projectId={PROJECT} />)

    await screen.findByText(/Event 0/)
    expect(document.querySelectorAll('[data-rail-activity-event]')).toHaveLength(8)
  })

  it('an empty ledger: "No recent activity."', async () => {
    svc.listActivity.mockResolvedValue(page([]))
    renderRail(<RecentActivity releases={[]} projectId={PROJECT} />)
    expect(await screen.findByText('No recent activity.')).toBeInTheDocument()
  })

  it('a failed read says so; it is not a quiet project', async () => {
    svc.listActivity.mockRejectedValue(new Error('500'))
    renderRail(<RecentActivity releases={[]} projectId={PROJECT} />)
    expect(await screen.findByText('Could not load recent activity.')).toBeInTheDocument()
    expect(screen.queryByText('No recent activity.')).not.toBeInTheDocument()
  })

  it('All projects: says the ledger is per project and sends no request', async () => {
    renderRail(<RecentActivity releases={[makeRelease({ stage: 'in_progress' })]} projectId={null} />)

    expect(screen.getByText('Release activity is recorded per project — pick a project to see it.')).toBeInTheDocument()
    // Let any (wrong) fetch land before asserting there was none.
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(svc.listActivity).not.toHaveBeenCalled()
  })

  // HON-09: "View full audit log" pointed at `/audit`, which was never a route
  // (the catch-all sent it to Overview). It opens the Activity feed on its
  // Release category, as an in-app link.
  it('keeps the audit link: /activity on the Release category', () => {
    renderRail(<RecentActivity releases={[]} projectId={null} />)
    const link = screen.getByRole('link', { name: /View full audit log/ })
    expect(link).toHaveAttribute('href', '/activity?category=release')
  })
})
