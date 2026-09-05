/**
 * Hermetic tests for the Retention & Purge settings page (PMF US-11.4 UI).
 *
 * Covers the load-bearing contract behaviours:
 *   - Non-admin → EmptyState, no form, zero fetches (in-component ADMIN gate).
 *   - All-Projects mode → select-a-project empty state, zero fetches.
 *   - Contract defaults render with the "Defaults" source badge; a custom
 *     policy shows "Customized".
 *   - Inline bounds validation blocks Save (per-field bounds AND the
 *     audit ≥ runs cross-rule).
 *   - PUT payload shape: partial — only the changed fields are sent.
 *   - Preview renders cutoffs + candidate counts incl. per-Mongo-collection.
 *   - Purge modal: confirm disabled until the typed name matches, sends
 *     confirmation_name; "Purge now" is disabled while the policy is disabled.
 *   - Failed GET → error state, NO form/Save (never render defaults over a
 *     failed fetch).
 *   - last_purge summary renders (when / mode / counts).
 *
 * The service layer and project store are mocked; the REAL SWR hook runs
 * under an isolated SWRConfig so seeding/revalidation behaviour is covered.
 */
import { createElement } from 'react'
import { act, render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { RetentionPolicy } from '@/types/retention'

// ── mocks ───────────────────────────────────────────────────────────────────

const mockGet = vi.fn()
const mockUpdate = vi.fn()
const mockPreview = vi.fn()
const mockPurge = vi.fn()

vi.mock('@/services/retentionService', () => ({
  retentionService: {
    get: (...a: unknown[]) => mockGet(...a),
    update: (...a: unknown[]) => mockUpdate(...a),
    preview: (...a: unknown[]) => mockPreview(...a),
    purge: (...a: unknown[]) => mockPurge(...a),
  },
}))

let mockIsAdmin = true
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAdmin: mockIsAdmin }),
}))

// Project store: mutable state so tests can flip into All-Projects mode.
// ALL_PROJECTS_ID is the REAL constant (importActual) so the page's sentinel
// comparison is tested against the production value, not an invented one.
const storeState: {
  activeProjectId: string | null
  activeProject: { id: string; name: string } | null
  // The All-Projects prompt now renders a project picker instead of telling
  // the reader to go and find the top bar, so it selects these two as well.
  // A mock that stops at the fields the page used to read makes the component
  // crash on `projects.map` — which is the mock being out of date, not the
  // component being unsafe.
  projects: { id: string; name: string }[]
  setActiveProject: (p: { id: string; name: string } | null) => void
} = {
  activeProjectId: 'proj-1',
  activeProject: { id: 'proj-1', name: 'Checkout' },
  projects: [{ id: 'proj-1', name: 'Checkout' }],
  setActiveProject: vi.fn(),
}
vi.mock('@/store/projectStore', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store/projectStore')>()
  const useProjectStore = (sel: (s: typeof storeState) => unknown) => sel(storeState)
  return { useProjectStore, ALL_PROJECTS_ID: actual.ALL_PROJECTS_ID }
})

const mockToastSuccess = vi.fn()
vi.mock('react-hot-toast', () => ({
  default: { success: (...a: unknown[]) => mockToastSuccess(...a), error: vi.fn() },
}))

// Lightweight stand-ins for shared UI so the test stays hermetic.
vi.mock('@/components/ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => createElement('h1', null, title),
}))
vi.mock('@/components/ui/LoadingSpinner', () => ({ default: () => createElement('div', null, 'loading') }))
vi.mock('@/components/ui/EmptyState', () => ({
  default: ({ title }: { title: string }) => createElement('div', null, title),
}))
vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => createElement('a', null, children),
}))

/** Contract defaults for an unconfigured project. */
function basePolicy(overrides: Partial<RetentionPolicy> = {}): RetentionPolicy {
  return {
    enabled: false,
    raw_events_days: 90,
    runs_days: 365,
    artifacts_days: 180,
    audit_days: 2555,
    source: 'default',
    last_purge: null,
    ...overrides,
  }
}

function dayInput(field: string): HTMLInputElement {
  const el = document.getElementById(`retention-${field}`)
  if (!(el instanceof HTMLInputElement)) throw new Error(`no input for ${field}`)
  return el
}

function saveButton(): HTMLButtonElement {
  return screen.getByText('Save policy').closest('button') as HTMLButtonElement
}

let RetentionPage: (typeof import('./RetentionPage'))['default']

beforeAll(async () => {
  // Import once outside individual tests' 5-second budgets. Router 7's larger
  // transform graph can otherwise consume that budget only in the full suite.
  const pageModule = await import('./RetentionPage')
  RetentionPage = pageModule.default
}, 30_000)

function renderPage() {
  return render(
    createElement(
      SWRConfig,
      {
        value: {
          provider: () => new Map(),
          dedupingInterval: 0,
          shouldRetryOnError: false,
          revalidateOnFocus: false,
        },
      },
      createElement(RetentionPage),
    ),
  )
}

describe('RetentionPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockIsAdmin = true
    storeState.activeProjectId = 'proj-1'
    storeState.activeProject = { id: 'proj-1', name: 'Checkout' }
    mockGet.mockResolvedValue(basePolicy())
    mockUpdate.mockResolvedValue(basePolicy({ source: 'custom' }))
    mockPreview.mockResolvedValue({
      cutoffs: {
        raw_events: '2026-05-04T00:00:00Z',
        runs: '2025-08-02T00:00:00Z',
        artifacts: '2026-02-03T00:00:00Z',
        audit: '2019-08-05T00:00:00Z',
      },
      candidates: {
        runs: 42,
        test_cases: 900,
        mongo_docs: { pipeline_events: 15, ingest_payloads: 7 },
        minio_objects: 12,
        event_archive_rows: 250,
        audit_rows: 3,
        provenance_rows: 5,
        compliance_packs_expired: 1,
      },
    })
    mockPurge.mockResolvedValue({ queued: true })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows the admin-required empty state (no form, zero fetches) for non-admins', async () => {
    mockIsAdmin = false
    await renderPage()

    expect(await screen.findByText('Admin access required')).toBeTruthy()
    expect(mockGet).not.toHaveBeenCalled()
    expect(screen.queryByText('Save policy')).toBeNull()
    expect(screen.queryByText('Purge now')).toBeNull()
  })

  it('renders the select-a-project empty state in All-Projects mode without fetching', async () => {
    const { ALL_PROJECTS_ID } = await import('@/store/projectStore')
    // Guard against the mock drifting from the production sentinel.
    expect(ALL_PROJECTS_ID).toBe('all')
    storeState.activeProjectId = ALL_PROJECTS_ID
    storeState.activeProject = null
    await renderPage()

    expect(await screen.findByText('Select a project')).toBeTruthy()
    expect(mockGet).not.toHaveBeenCalled()
    expect(screen.queryByText('Save policy')).toBeNull()
  })

  it('renders the contract defaults with the "Defaults" source badge', async () => {
    await renderPage()

    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())
    expect(screen.getByText('Defaults')).toBeTruthy()
    expect(screen.queryByText('Customized')).toBeNull()
    expect(dayInput('raw_events_days').value).toBe('90')
    expect(dayInput('runs_days').value).toBe('365')
    expect(dayInput('artifacts_days').value).toBe('180')
    expect(dayInput('audit_days').value).toBe('2555')
  })

  it('shows the "Customized" badge when the policy source is custom', async () => {
    mockGet.mockResolvedValue(basePolicy({ source: 'custom', runs_days: 400 }))
    await renderPage()

    await waitFor(() => expect(screen.getByText('Customized')).toBeTruthy())
    expect(dayInput('runs_days').value).toBe('400')
  })

  it('blocks Save on out-of-bounds values with an inline error', async () => {
    await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    // raw_events lower bound is 7.
    fireEvent.change(dayInput('raw_events_days'), { target: { value: '5' } })
    expect(screen.getByText('Raw events must be between 7 and 3650 days')).toBeTruthy()
    expect(saveButton().disabled).toBe(true)

    fireEvent.click(saveButton())
    expect(mockUpdate).not.toHaveBeenCalled()

    // Back in bounds → error clears, Save re-enables (the field changed).
    fireEvent.change(dayInput('raw_events_days'), { target: { value: '30' } })
    expect(screen.queryByText('Raw events must be between 7 and 3650 days')).toBeNull()
    expect(saveButton().disabled).toBe(false)
  })

  it('blocks Save when audit_days < runs_days (cross-rule)', async () => {
    await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    // Both within their own bounds, but audit (370) < runs (400).
    fireEvent.change(dayInput('runs_days'), { target: { value: '400' } })
    fireEvent.change(dayInput('audit_days'), { target: { value: '370' } })

    expect(
      screen.getByText('Audit trail must be kept at least as long as Runs & analysis'),
    ).toBeTruthy()
    expect(saveButton().disabled).toBe(true)
    fireEvent.click(saveButton())
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it('PUTs only the changed fields', async () => {
    mockUpdate.mockResolvedValue(
      basePolicy({ enabled: true, runs_days: 400, source: 'custom' }),
    )
    await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.change(dayInput('runs_days'), { target: { value: '400' } })

    await act(async () => {
      fireEvent.click(saveButton())
    })

    expect(mockUpdate).toHaveBeenCalledTimes(1)
    const [projectId, payload] = mockUpdate.mock.calls[0]
    expect(projectId).toBe('proj-1')
    expect(payload).toEqual({ enabled: true, runs_days: 400 })

    // The saved response re-seeds the form and badge.
    await waitFor(() => expect(screen.getByText('Customized')).toBeTruthy())
    expect(mockToastSuccess).toHaveBeenCalledWith('Retention policy saved')
  })

  it('disables Save when nothing changed', async () => {
    await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    expect(saveButton().disabled).toBe(true)
    expect(saveButton().title).toBe('No changes to save')
  })

  it('renders preview cutoffs and candidate counts incl. per-Mongo-collection docs', async () => {
    await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })

    expect(mockPreview).toHaveBeenCalledWith('proj-1')
    await waitFor(() => expect(screen.getByText('Test runs')).toBeTruthy())
    expect(screen.getByText('42')).toBeTruthy()
    expect(screen.getByText('900')).toBeTruthy()
    expect(screen.getByText('Expired compliance packs')).toBeTruthy()
    // Per-collection Mongo doc counts.
    expect(screen.getByText('pipeline_events')).toBeTruthy()
    expect(screen.getByText('15')).toBeTruthy()
    expect(screen.getByText('ingest_payloads')).toBeTruthy()
    expect(screen.getByText('7')).toBeTruthy()
    // Honest note: counts are point-in-time.
    expect(screen.getByText(/numbers may differ slightly/)).toBeTruthy()
  })

  it('renders "not measured" for a store the purge could not reach', async () => {
    // RET-D15. `analysis_cache_entries` and `search_index_documents` come from
    // stores that can be down independently of Postgres, and both used to
    // report 0 on an outage — indistinguishable from "nothing to delete" on
    // the screen an ADMIN authorises an irreversible cross-store purge from.
    mockPreview.mockResolvedValue({
      cutoffs: {
        raw_events: '2026-05-04T00:00:00Z',
        runs: '2025-08-02T00:00:00Z',
        artifacts: '2026-02-03T00:00:00Z',
        audit: '2019-08-05T00:00:00Z',
      },
      candidates: {
        runs: 42,
        test_cases: 900,
        mongo_docs: {},
        minio_objects: 12,
        event_archive_rows: 250,
        audit_rows: 3,
        provenance_rows: 5,
        compliance_packs_expired: 1,
        evidence_artifact_rows: 2,
        memory_entries_expired: 4,
        analysis_cache_entries: null,
        search_index_documents: null,
      },
      unmeasured: ['analysis_cache_entries', 'search_index_documents'],
    })
    await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })

    await waitFor(() => expect(screen.getByText('Analysis-cache entries')).toBeTruthy())
    expect(screen.getAllByText('not measured').length).toBe(2)
  })

  it('shows a real zero as 0, not as "not measured"', async () => {
    // The other half of the rule. If an outage and an empty store both
    // rendered "not measured" the distinction would be lost again, just in the
    // opposite direction.
    mockPreview.mockResolvedValue({
      cutoffs: {
        raw_events: '2026-05-04T00:00:00Z',
        runs: '2025-08-02T00:00:00Z',
        artifacts: '2026-02-03T00:00:00Z',
        audit: '2019-08-05T00:00:00Z',
      },
      candidates: {
        runs: 42,
        test_cases: 900,
        mongo_docs: {},
        minio_objects: 12,
        event_archive_rows: 250,
        audit_rows: 3,
        provenance_rows: 5,
        compliance_packs_expired: 1,
        evidence_artifact_rows: 2,
        memory_entries_expired: 4,
        analysis_cache_entries: 0,
        search_index_documents: 0,
      },
      unmeasured: [],
    })
    await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })

    await waitFor(() => expect(screen.getByText('Analysis-cache entries')).toBeTruthy())
    expect(screen.queryByText('not measured')).toBeNull()
  })

  it('surfaces a preview failure as an inline error', async () => {
    mockPreview.mockRejectedValue(new Error('boom'))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })

    await waitFor(() => expect(screen.getByText(/boom/)).toBeTruthy())
    expect(screen.queryByText('Test runs')).toBeNull()
  })

  it('disables "Purge now" (with an explanatory title) while the policy is disabled', async () => {
    mockGet.mockResolvedValue(basePolicy({ enabled: false }))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Purge now')).toBeTruthy())

    const purgeButton = screen.getByText('Purge now').closest('button') as HTMLButtonElement
    expect(purgeButton.disabled).toBe(true)
    expect(purgeButton.title).toBe(
      'Enable and save the retention policy first — purging is blocked while the policy is disabled',
    )
    fireEvent.click(purgeButton)
    expect(screen.queryByText('Purge old data now')).toBeNull()
  })

  it('purge modal: confirm disabled until the typed name matches, sends confirmation_name', async () => {
    mockGet.mockResolvedValue(basePolicy({ enabled: true }))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Purge now')).toBeTruthy())

    fireEvent.click(screen.getByText('Purge now'))
    expect(screen.getByText('Purge old data now')).toBeTruthy()

    const confirm = screen.getByText('Queue purge').closest('button') as HTMLButtonElement
    expect(confirm.disabled).toBe(true)

    // Wrong name keeps it disabled.
    const input = screen.getByPlaceholderText('Checkout')
    fireEvent.change(input, { target: { value: 'checkout' } })
    expect(confirm.disabled).toBe(true)
    fireEvent.click(confirm)
    expect(mockPurge).not.toHaveBeenCalled()

    // Exact match enables it; the typed name is sent as confirmation_name.
    fireEvent.change(input, { target: { value: 'Checkout' } })
    expect(confirm.disabled).toBe(false)
    await act(async () => {
      fireEvent.click(confirm)
    })

    expect(mockPurge).toHaveBeenCalledWith('proj-1', 'Checkout')
    expect(mockToastSuccess).toHaveBeenCalledWith(
      'Purge queued — check back for the audit record',
    )
    // Modal closed after the 202.
    expect(screen.queryByText('Purge old data now')).toBeNull()
  })

  it('renders an error state without the form when the GET fails', async () => {
    mockGet.mockRejectedValue(new Error('network down'))
    await renderPage()

    await waitFor(() =>
      expect(screen.getByText(/Could not load the retention policy/)).toBeTruthy(),
    )
    // The form is withheld: a Save from a defaults-seeded form would wipe a
    // working policy with defaults.
    expect(screen.queryByText('Save policy')).toBeNull()
    expect(screen.queryByText('Purge now')).toBeNull()
    expect(screen.getByText('Retry')).toBeTruthy()

    // Retry refetches; a successful response swaps in the real form.
    mockGet.mockResolvedValue(basePolicy({ source: 'custom' }))
    await act(async () => {
      fireEvent.click(screen.getByText('Retry'))
    })
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())
    expect(screen.getByText('Customized')).toBeTruthy()
  })

  it('renders the last_purge summary (when, mode, counts)', async () => {
    mockGet.mockResolvedValue(
      basePolicy({
        enabled: true,
        last_purge: {
          at: '2026-07-30T02:00:00Z',
          mode: 'scheduled',
          counts: { test_runs: 12, minio_objects: 4 },
        },
      }),
    )
    await renderPage()

    await waitFor(() => expect(screen.getByText(/Last purge:/)).toBeTruthy())
    expect(screen.getByText('(scheduled)')).toBeTruthy()
    expect(screen.getByText('test_runs')).toBeTruthy()
    expect(screen.getByText('minio_objects')).toBeTruthy()
  })
})
