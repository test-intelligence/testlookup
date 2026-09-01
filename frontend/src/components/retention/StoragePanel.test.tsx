/**
 * S3 — the storage panel.
 *
 * The panel exists to answer "how much is this project holding, and what would
 * a purge give back". Its acceptance criteria are about honesty rather than
 * layout, so these tests are almost entirely about the two distinctions the
 * backend encodes and the UI could easily throw away:
 *
 *  - a store that could not be reached must render "not measured", never 0
 *  - an estimate must be visibly an estimate
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StoragePanel from './StoragePanel'
import type { DeletedProjectsStorage, ProjectStorage } from '@/types/storage'

vi.mock('@/hooks/useStorage', () => ({
  useProjectStorage: vi.fn(),
  useDeletedProjectStorage: vi.fn(),
}))

import { useDeletedProjectStorage, useProjectStorage } from '@/hooks/useStorage'

const REACHED: ProjectStorage = {
  project_id: 'p1',
  computed_at: '2026-09-01T00:00:00Z',
  stores: [
    {
      store: 'object_storage',
      measured: true,
      exact: true,
      bytes: 5_368_709_120, // 5 GiB
      items: 120,
      estimate_basis: null,
      unreachable_reason: null,
    },
    {
      store: 'postgres',
      measured: true,
      exact: true,
      bytes: null,
      items: 4200,
      estimate_basis: 'Row counts are exact. Bytes are not reported.',
      unreachable_reason: null,
    },
    {
      store: 'mongo',
      measured: true,
      exact: false,
      bytes: 1_073_741_824, // 1 GiB
      items: 30,
      estimate_basis: 'avgObjSize x document count',
      unreachable_reason: null,
    },
  ],
  total_bytes: 6_442_450_944, // 6 GiB — distinct from every row
  total_is_estimate: true,
  fully_measured: true,
}

function withStores(stores: ProjectStorage['stores'], overrides: Partial<ProjectStorage> = {}) {
  return { ...REACHED, stores, ...overrides }
}

function mockProject(data: ProjectStorage | undefined, extra: Record<string, unknown> = {}) {
  vi.mocked(useProjectStorage).mockReturnValue({
    data,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
    ...extra,
  } as unknown as ReturnType<typeof useProjectStorage>)
}

function mockDeleted(data: DeletedProjectsStorage | undefined) {
  vi.mocked(useDeletedProjectStorage).mockReturnValue({
    data,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useDeletedProjectStorage>)
}

function renderPanel() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <StoragePanel projectId="p1" />
    </SWRConfig>,
  )
}

describe('StoragePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockDeleted(undefined)
  })

  it('renders actual bytes per store, separately from the total', () => {
    mockProject(REACHED)
    renderPanel()
    // Distinct values on purpose: a fixture where the row and the total format
    // alike would let either assertion be satisfied by the wrong element.
    expect(screen.getByText('5.0 GiB')).toBeInTheDocument() // object storage
    expect(screen.getByText('1.0 GiB')).toBeInTheDocument() // document store
    expect(screen.getByText('6.0 GiB')).toBeInTheDocument() // total
  })

  it('renders "not measured" for an unreachable store — never 0', () => {
    // The acceptance criterion of the whole slice. A store that was down and a
    // store holding nothing are opposite findings; "0 B" for both tells an
    // operator their project is free when nothing actually looked.
    mockProject(
      withStores(
        [
          {
            store: 'object_storage',
            measured: false,
            exact: true,
            bytes: null,
            items: null,
            estimate_basis: null,
            unreachable_reason: 'ConnectionError',
          },
        ],
        { total_bytes: null, fully_measured: false },
      ),
    )
    renderPanel()

    // Scoped to the STORE row. Asserting against the whole panel passed even
    // with the unreachable branch deleted, because the total also renders
    // "not measured" — the assertion was being satisfied by the wrong element.
    expect(screen.getByTestId('store-value-object_storage')).toHaveTextContent(
      'not measured',
    )
    expect(screen.queryByText('0 B')).not.toBeInTheDocument()
  })

  it('renders a genuine zero as 0 B, not as "not measured"', () => {
    // The other direction. If an outage and an empty store both said "not
    // measured" the distinction would be lost again.
    mockProject(
      withStores(
        [
          {
            store: 'object_storage',
            measured: true,
            exact: true,
            bytes: 0,
            items: 0,
            estimate_basis: null,
            unreachable_reason: null,
          },
        ],
        { total_bytes: 0, total_is_estimate: false, fully_measured: true },
      ),
    )
    renderPanel()

    // Scoped for the same reason: the total is 0 here too, so a panel-wide
    // assertion passed even when the store row rendered "0 rows" instead.
    expect(screen.getByTestId('store-value-object_storage')).toHaveTextContent('0 B')
    expect(screen.queryByText('not measured')).not.toBeInTheDocument()
  })

  it('marks an estimated figure as an estimate', () => {
    mockProject(REACHED)
    renderPanel()
    // Mongo is avgObjSize-derived; the total inherits the estimate.
    expect(screen.getByText('est.')).toBeInTheDocument()
    expect(screen.getByText('(estimate)')).toBeInTheDocument()
  })

  it('warns that a partial total is a floor, not a measurement', () => {
    mockProject(withStores(REACHED.stores, { fully_measured: false }))
    renderPanel()
    expect(screen.getByTestId('storage-partial-warning')).toBeInTheDocument()
  })

  it('states the VACUUM caveat rather than implying rows free disk', () => {
    mockProject(REACHED)
    renderPanel()
    expect(screen.getByText(/VACUUM FULL/)).toBeInTheDocument()
  })

  it('surfaces deleted projects nothing will ever reclaim', () => {
    mockProject(REACHED)
    mockDeleted({
      computed_at: '2026-09-01T00:00:00Z',
      projects_total: 12,
      projects_measured: 12,
      truncated: false,
      projects: [],
      total_bytes: 10_737_418_240, // 10 GiB
      total_is_estimate: false,
      unreachable_by_retention: 11,
    })
    renderPanel()

    expect(screen.getByTestId('deleted-projects-line')).toBeInTheDocument()
    expect(screen.getByText(/10.0 GiB held by deleted projects/)).toBeInTheDocument()
    expect(screen.getByText(/11 of 12/)).toBeInTheDocument()
  })

  it('says a capped deleted-project scan is a floor', () => {
    mockProject(REACHED)
    mockDeleted({
      computed_at: '2026-09-01T00:00:00Z',
      projects_total: 90,
      projects_measured: 25,
      truncated: true,
      projects: [],
      total_bytes: 1024,
      total_is_estimate: false,
      unreachable_by_retention: 25,
    })
    renderPanel()
    expect(screen.getByText(/Only 25 were measured/)).toBeInTheDocument()
  })

  it('hides the deleted-projects line when there are none', () => {
    mockProject(REACHED)
    mockDeleted({
      computed_at: '2026-09-01T00:00:00Z',
      projects_total: 0,
      projects_measured: 0,
      truncated: false,
      projects: [],
      total_bytes: null,
      total_is_estimate: false,
      unreachable_by_retention: 0,
    })
    renderPanel()
    expect(screen.queryByTestId('deleted-projects-line')).not.toBeInTheDocument()
  })

  it('refreshes on demand rather than on window focus', async () => {
    const mutate = vi.fn()
    mockProject(REACHED, { mutate })
    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: /refresh storage figures/i }))

    await waitFor(() => expect(mutate).toHaveBeenCalled())
  })
})
