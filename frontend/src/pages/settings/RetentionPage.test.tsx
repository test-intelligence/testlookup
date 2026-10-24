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
 *   - last_purge summary renders (when / mode / counts), nested counts as text.
 *   - W03 AIH-06 AI-history caps: defaults 20/10 and the summary line, bounds
 *     1–100, only a changed cap is sent, an older server's missing caps are
 *     "not reported" and never sent; the preview's "AI history versions" row.
 *
 * The service layer and project store are mocked; the REAL SWR hook runs
 * under an isolated SWRConfig so seeding/revalidation behaviour is covered.
 */
import { createElement } from 'react'
import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { SWRConfig } from 'swr'

import type { RetentionPolicy } from '@/types/retention'
import { helpTopicParam } from '@/components/help/helpTopics'

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

// The storage panel and the activation nudge read their own endpoints (each has
// its own tests); answered here so no request leaves the test (src/test/networkGuard.ts).
vi.mock('@/hooks/useStorage', () => ({
  useProjectStorage: () => ({ data: undefined, error: undefined, isLoading: false, mutate: vi.fn() }),
  useDeletedProjectStorage: () => ({ data: undefined, mutate: vi.fn() }),
}))
vi.mock('@/hooks/useUIDismissals', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useUIDismissals')>(),
  useUIDismissals: () => ({ data: undefined, mutate: vi.fn() }),
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
// Forwards the template props the page must pass (UX redesign P5).
vi.mock('@/components/ui/PageHeader', () => ({
  default: ({ title, compact, helpTopic }: { title: string; compact?: boolean; helpTopic?: string }) =>
    createElement('h1', { 'data-compact': compact ? 'true' : 'false', 'data-help-topic': helpTopic }, title),
}))
vi.mock('@/components/ui/LoadingSpinner', () => ({ default: () => createElement('div', null, 'loading') }))
vi.mock('@/components/ui/EmptyState', () => ({
  default: ({ title }: { title: string }) => createElement('div', null, title),
}))
vi.mock('react-router-dom', () => ({
  Link: ({ children }: { children: React.ReactNode }) => createElement('a', null, children),
}))
// The activation nudge and the storage footprint have their own tests. Real
// here, they read the user's dismissals, the nudge's kill-switch flag and the
// project's storage over HTTP (a real request from a unit test).
vi.mock('@/components/retention/RetentionActivationNudge', () => ({ default: () => null }))
vi.mock('@/components/retention/StoragePanel', () => ({ default: () => null }))

/** Contract defaults for an unconfigured project. */
function basePolicy(overrides: Partial<RetentionPolicy> = {}): RetentionPolicy {
  return {
    enabled: false,
    raw_events_days: 90,
    runs_days: 365,
    artifacts_days: 180,
    audit_days: 2555,
    ai_versions_per_run: 20,
    ai_versions_per_test: 10,
    source: 'default',
    last_purge: null,
    ...overrides,
  }
}

/** A policy read from a server that predates the W03 AI-history caps. */
function olderServerPolicy(overrides: Partial<RetentionPolicy> = {}): RetentionPolicy {
  const { ai_versions_per_run: _run, ai_versions_per_test: _test, ...rest } = basePolicy(overrides)
  return rest
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
        ai_history_versions: 3,
        search_usage_rows: 0,
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
        ai_history_versions: 0,
        search_usage_rows: 0,
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

  // W29a UIX-01 (L2): the destructive button is the shared .btn-danger (solid
  // red + white on the light themes; white on the 15 % tint was 1.26:1 on Lab).
  it('purge: "Queue purge" is the shared destructive button, with no hand-written colours', async () => {
    mockGet.mockResolvedValue(basePolicy({ enabled: true }))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Purge now')).toBeTruthy())
    fireEvent.click(screen.getByText('Purge now'))

    const confirm = screen.getByText('Queue purge').closest('button') as HTMLButtonElement
    expect(confirm.classList.contains('btn-danger')).toBe(true)
    expect(confirm.className).not.toMatch(/text-white|status-failed|rounded\b|px-3/)
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

  // ── The settings page template (UX redesign P5) ─────────────────────────
  function expectCompactHeader() {
    const heading = screen.getByRole('heading', { level: 1, name: 'Retention & Purge' })
    expect(heading).toHaveAttribute('data-compact', 'true')
    expect(heading).toHaveAttribute('data-help-topic', helpTopicParam('/settings/retention'))
  }

  it('uses the compact header with the route help topic on the policy page', async () => {
    const { container } = await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    expectCompactHeader()
    // The full content column: the max-w-3xl cap is gone, and the policy form
    // keeps its two-column field grid.
    expect(container.querySelector('.max-w-3xl')).toBeNull()
    expect(dayInput('raw_events_days').closest('.grid')).toHaveClass('sm:grid-cols-2')
  })

  it('uses the same header in All-Projects mode and in the load-error state', async () => {
    const { ALL_PROJECTS_ID } = await import('@/store/projectStore')
    storeState.activeProjectId = ALL_PROJECTS_ID
    storeState.activeProject = null
    const first = await renderPage()
    await screen.findByText('Select a project')
    expectCompactHeader()
    first.unmount()

    storeState.activeProjectId = 'proj-1'
    storeState.activeProject = { id: 'proj-1', name: 'Checkout' }
    mockGet.mockRejectedValue(new Error('network down'))
    await renderPage()
    await waitFor(() => expect(screen.getByText(/Could not load the retention policy/)).toBeTruthy())
    expectCompactHeader()
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

  it('renders the purge counts the server nests by store as text, never "[object Object]"', async () => {
    mockGet.mockResolvedValue(
      basePolicy({
        enabled: true,
        last_purge: {
          at: '2026-07-30T02:00:00Z',
          mode: 'execute',
          counts: {
            postgres: { runs: 3, test_case_audit_rows: 1200 },
            ai_history: { analysis_versions_cascaded: 40, analysis_versions_over_cap: 2, run_versions_over_cap: 1 },
            analysis_cache: { redis: null, semantic: 4 },
            minio: {},
          },
        },
      }),
    )
    const { container } = await renderPage()
    await waitFor(() => expect(screen.getByText(/Last purge:/)).toBeTruthy())
    expect(container.textContent).not.toContain('[object Object]')
    const items = Array.from(container.querySelectorAll('li')).map((li) => li.textContent)
    expect(items).toContain('postgres: runs: 3, test_case_audit_rows: 1,200')
    expect(items).toContain('ai_history: analysis_versions_cascaded: 40, analysis_versions_over_cap: 2, run_versions_over_cap: 1')
    expect(items).toContain('analysis_cache: redis: not measured, semantic: 4')
    expect(items).toContain('minio: none')
  })

  // ── W03 AIH-06: the AI-history caps ─────────────────────────────────────
  it('shows the AI-history caps (defaults 20 / 10) and says what they keep', async () => {
    const { container } = await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    expect(container.querySelector('[data-retention-ai-summary]')?.textContent).toBe(
      'AI history: keep 20 versions per run, 10 per test.',
    )
    expect(dayInput('ai_versions_per_run').value).toBe('20')
    expect(dayInput('ai_versions_per_test').value).toBe('10')
    expect(dayInput('ai_versions_per_run').min).toBe('1')
    expect(dayInput('ai_versions_per_run').max).toBe('100')
    expect(screen.getByText('AI versions kept per run (per kind)')).toBeTruthy()
    expect(screen.getByText('AI versions kept per test result')).toBeTruthy()
    expect(
      screen.getByText(
        'Oldest are pruned first; version 1 and any version cited by a published decision report are never pruned.',
      ),
    ).toBeTruthy()
    // The caps never depend on scheduled purging being on.
    expect(screen.getByText(/even while scheduled purging is off/)).toBeTruthy()
  })

  it('bounds each cap to 1–100 whole versions and blocks Save outside them', async () => {
    await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    for (const bad of ['0', '101', '2.5']) {
      fireEvent.change(dayInput('ai_versions_per_run'), { target: { value: bad } })
      expect(
        screen.getByText(
          bad === '2.5'
            ? 'AI versions kept per run (per kind) must be a whole number of versions'
            : 'AI versions kept per run (per kind) must be between 1 and 100 versions',
        ),
      ).toBeTruthy()
      expect(saveButton().disabled).toBe(true)
    }
    fireEvent.click(saveButton())
    expect(mockUpdate).not.toHaveBeenCalled()

    fireEvent.change(dayInput('ai_versions_per_run'), { target: { value: '1' } })
    fireEvent.change(dayInput('ai_versions_per_test'), { target: { value: '100' } })
    expect(screen.queryByText(/must be between 1 and 100 versions/)).toBeNull()
    expect(saveButton().disabled).toBe(false)
  })

  it('PUTs only the cap that changed', async () => {
    mockUpdate.mockResolvedValue(basePolicy({ ai_versions_per_run: 3, source: 'custom' }))
    const { container } = await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    fireEvent.change(dayInput('ai_versions_per_run'), { target: { value: '3' } })
    await act(async () => {
      fireEvent.click(saveButton())
    })

    expect(mockUpdate).toHaveBeenCalledTimes(1)
    expect(mockUpdate.mock.calls[0][1]).toEqual({ ai_versions_per_run: 3 })
    // The summary reads the SAVED policy.
    await waitFor(() =>
      expect(container.querySelector('[data-retention-ai-summary]')?.textContent).toBe(
        'AI history: keep 3 versions per run, 10 per test.',
      ),
    )
  })

  it('an older server without caps: "not reported", the inputs disabled, the caps never sent', async () => {
    mockGet.mockResolvedValue(olderServerPolicy())
    mockUpdate.mockResolvedValue(olderServerPolicy({ runs_days: 400, source: 'custom' }))
    const { container } = await renderPage()
    await waitFor(() => expect(screen.getByText('Save policy')).toBeTruthy())

    expect(container.querySelector('[data-retention-ai-summary]')?.textContent).toBe(
      'AI history caps: not reported by this server.',
    )
    expect(dayInput('ai_versions_per_run').disabled).toBe(true)
    expect(dayInput('ai_versions_per_run').value).toBe('')
    expect(dayInput('ai_versions_per_test').disabled).toBe(true)
    // Nothing reported is nothing changed: no spurious Save.
    expect(saveButton().disabled).toBe(true)

    fireEvent.change(dayInput('runs_days'), { target: { value: '400' } })
    await act(async () => {
      fireEvent.click(saveButton())
    })
    expect(mockUpdate.mock.calls[0][1]).toEqual({ runs_days: 400 })
  })

  it('the preview lists the AI history versions, and "not measured" from an older server', async () => {
    const PREVIEW = {
      cutoffs: {
        raw_events: '2026-05-04T00:00:00Z',
        runs: '2025-08-02T00:00:00Z',
        artifacts: '2026-02-03T00:00:00Z',
        audit: '2019-08-05T00:00:00Z',
      },
      candidates: {
        runs: 1, test_cases: 2, mongo_docs: {}, minio_objects: 3, event_archive_rows: 4, audit_rows: 5,
        provenance_rows: 6, compliance_packs_expired: 7, evidence_artifact_rows: 8, memory_entries_expired: 9,
        analysis_cache_entries: 10, search_index_documents: 11, ai_history_versions: 12,
      },
      unmeasured: [],
    }
    mockPreview.mockResolvedValue(PREVIEW)
    const first = await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })
    await waitFor(() => expect(screen.getByText('AI history versions')).toBeTruthy())
    const row = screen.getByText('AI history versions').closest('tr') as HTMLElement
    expect(row.textContent).toBe('AI history versions12')
    first.unmount()

    const { ai_history_versions: _dropped, ...older } = PREVIEW.candidates
    mockPreview.mockResolvedValue({ ...PREVIEW, candidates: older })
    await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })
    await waitFor(() => expect(screen.getByText('AI history versions')).toBeTruthy())
    expect((screen.getByText('AI history versions').closest('tr') as HTMLElement).textContent).toBe(
      'AI history versionsnot measured',
    )
  })

  it('W12 SRCH-05: the preview lists the search usage rows, and "not measured" from an older server', async () => {
    const PREVIEW = {
      cutoffs: {
        raw_events: '2026-05-04T00:00:00Z',
        runs: '2025-08-02T00:00:00Z',
        artifacts: '2026-02-03T00:00:00Z',
        audit: '2019-08-05T00:00:00Z',
      },
      candidates: {
        runs: 1, test_cases: 2, mongo_docs: {}, minio_objects: 3, event_archive_rows: 4, audit_rows: 5,
        provenance_rows: 6, compliance_packs_expired: 7, evidence_artifact_rows: 8, memory_entries_expired: 9,
        analysis_cache_entries: 10, search_index_documents: 11, ai_history_versions: 12, search_usage_rows: 1234,
      },
      unmeasured: [],
    }
    const label = 'Search usage rows (kept 30 days)'
    mockPreview.mockResolvedValue(PREVIEW)
    const first = await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })
    await waitFor(() => expect(screen.getByText(label)).toBeTruthy())
    const rows = Array.from(first.container.querySelectorAll('tbody tr')).map(tr => tr.textContent)
    expect(rows.indexOf(`${label}1,234`)).toBe(rows.indexOf('AI history versions12') + 1)
    expect(screen.queryByText('not measured')).toBeNull()
    first.unmount()

    const { search_usage_rows: _dropped, ...older } = PREVIEW.candidates
    mockPreview.mockResolvedValue({ ...PREVIEW, candidates: older })
    await renderPage()
    await waitFor(() => expect(screen.getByText('Preview purge')).toBeTruthy())
    await act(async () => {
      fireEvent.click(screen.getByText('Preview purge'))
    })
    await waitFor(() => expect(screen.getByText(label)).toBeTruthy())
    expect((screen.getByText(label).closest('tr') as HTMLElement).textContent).toBe(`${label}not measured`)
  })

  /**
   * W27b PAN-06 (R1): the typed confirmation opens in the page, inside the
   * Danger zone right under the Manual purge row and above the last-purge
   * summary — never a modal. It keeps its typed field; Escape closes it (focus
   * back on Purge now) except while the purge is being queued.
   */
  it('purge: the typed form opens inside the Danger zone, under Manual purge, above the last purge', async () => {
    mockGet.mockResolvedValue(
      basePolicy({ enabled: true, last_purge: { at: '2026-07-30T02:00:00Z', mode: 'scheduled', counts: {} } }),
    )
    await renderPage()
    await waitFor(() => expect(screen.getByText('Purge now')).toBeTruthy())
    const trigger = screen.getByText('Purge now').closest('button') as HTMLButtonElement
    trigger.focus()
    fireEvent.click(trigger)

    const form = screen.getByRole('region', { name: 'Purge old data now' })
    const zone = document.querySelector('section[aria-labelledby="retention-danger-heading"]') as HTMLElement
    expect(zone.contains(form)).toBe(true)
    expect(trigger.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(form.compareDocumentPosition(screen.getByText(/Last purge:/)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(screen.getByPlaceholderText('Checkout'))

    fireEvent.keyDown(screen.getByPlaceholderText('Checkout'), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Purge old data now' })).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(mockPurge).not.toHaveBeenCalled()
  })

  it('purge: while the purge is being queued, Escape is swallowed and the X is disabled', async () => {
    let answer: () => void = () => undefined
    mockPurge.mockReturnValue(new Promise<void>((resolve) => { answer = () => resolve() }))
    mockGet.mockResolvedValue(basePolicy({ enabled: true }))
    await renderPage()
    await waitFor(() => expect(screen.getByText('Purge now')).toBeTruthy())
    fireEvent.click(screen.getByText('Purge now'))
    const form = screen.getByRole('region', { name: 'Purge old data now' })
    fireEvent.change(screen.getByPlaceholderText('Checkout'), { target: { value: 'Checkout' } })
    fireEvent.click(screen.getByText('Queue purge').closest('button') as HTMLButtonElement)
    await waitFor(() => expect(mockPurge).toHaveBeenCalledTimes(1))

    fireEvent.keyDown(form, { key: 'Escape' })
    expect(screen.getByRole('region', { name: 'Purge old data now' })).toBe(form)
    expect(within(form).getByRole('button', { name: 'Close' })).toBeDisabled()

    await act(async () => { answer() })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Purge old data now' })).toBeNull())
    expect(mockPurge).toHaveBeenCalledTimes(1)
    expect(mockPurge).toHaveBeenCalledWith('proj-1', 'Checkout')
    expect(mockToastSuccess).toHaveBeenCalledWith('Purge queued — check back for the audit record')
  })
})
