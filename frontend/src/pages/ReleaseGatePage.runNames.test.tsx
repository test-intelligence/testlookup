/**
 * S-E-3: the Release Gate names a run by its build label or `Run #N`, never by
 * a slice of its id: the All Projects run picker's Run column and the
 * subtitle when a run has no decision.
 */
import { render, screen, within } from '@testing-library/react'
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { Release } from '@/types/releases'

import ReleaseGatePage from './ReleaseGatePage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useReleaseCouncil', () => ({
  useReleaseCouncil: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

// The risk ring is real Recharts; jsdom lays nothing out, so only the
// container is given the gauge's own 120 px box (everything else is real).
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 120, height: 120 })
        : null,
  }
})

// The catalogue's two requests go through `chartGet`; only it is replaced.
const chartGet = vi.hoisted(() => vi.fn())
vi.mock('@/services/chartApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/chartApi')>()),
  chartGet,
}))

const releaseList = vi.hoisted(() => ({ items: [] as Release[] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: releaseList.items }, error: undefined, mutate: () => {} }),
}))

// The real Context group, wrapped in a spy so a test can read the props the
// page hands it (rule 4: never the verdict).
vi.mock('@/components/reports/catalogue/GateCatalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/reports/catalogue/GateCatalogue')>()
  return { ...actual, default: vi.fn(actual.default) }
})

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

// No request leaves the test (src/test/networkGuard.ts): the dimension
// descriptions, the AI mode, and the telemetry an error boundary sends when a
// test makes a group throw.
const { mockUseScoringModel } = vi.hoisted(() => ({
  mockUseScoringModel: vi.fn(() => ({ scoringModel: undefined, isLoading: false, isError: false, getDescription: () => '' })),
}))
vi.mock('@/hooks/useRunIntelligence', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useRunIntelligence')>(),
  useScoringModel: mockUseScoringModel,
}))
// D-129: one test turns deep investigation off; every other test reads "still loading" (undefined).
const aiModeState = vi.hoisted(() => ({ current: undefined as undefined | Record<string, unknown> }))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useAIConfig')>(),
  useAIConfig: () => ({ data: aiModeState.current, error: undefined, isLoading: false }),
}))
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
// The share dialog's list of the run's links (GATE-06), read only while open.
vi.mock('@/hooks/useShareLinks', () => ({
  useShareLinks: () => ({ links: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))
// W11 GATE-08a (set-up only): the top bar's effective release and its gate
// read. No release unless a test sets one, which is what the page always saw.
const releaseScope = vi.hoisted(() => ({ releaseId: null as string | null }))
vi.mock('@/hooks/useReleaseScope', () => ({ useReleaseScope: () => releaseScope.releaseId }))
const releaseGateRead = vi.hoisted(() => ({
  useReleaseGate: vi.fn((_id: string | null) => ({ data: undefined as unknown, error: undefined as unknown })),
}))
vi.mock('@/hooks/useReleaseGate', () => ({ useReleaseGate: releaseGateRead.useReleaseGate }))


const RUN_ID = '8d7073a5-0000-4000-8000-000000000001'

async function mount(url: string, items: Record<string, unknown>[]) {
  const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
  const { useRuns } = await import('@/hooks/useRuns')
  ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({ council: null, isLoading: false, isError: true, refresh: vi.fn() })
  ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items } })
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/release-gate" element={<ReleaseGatePage />} />
        <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ReleaseGatePage run names (S-E-3)', () => {
  it('the All Projects picker shows Run #N in the Run column, never 8 hex characters', async () => {
    mockProjectState.activeProjectId = '__ALL__'
    await mount('/release-gate', [
      { id: RUN_ID, status: 'failed', build_number: 'main-1865', run_seq: 12, project_id: 'p-1', project_name: null, created_at: '2026-11-20T00:00:00Z' },
    ])
    const row = (await screen.findByText('main-1865')).closest('tr') as HTMLElement
    expect(within(row).getByText('Run #12')).toHaveAttribute('title', RUN_ID)
    expect(row.textContent).not.toMatch(/8d7073a5|p-1/)
  })

  it('a run with no decision is named by its build label in the subtitle', async () => {
    mockProjectState.activeProjectId = 'proj-1'
    await mount(`/release-gate/${RUN_ID}`, [{ id: RUN_ID, status: 'failed', build_number: 'main-1865', run_seq: 12 }])
    expect(await screen.findByText('No release decision found')).toBeInTheDocument()
    expect(screen.getByText('main-1865')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/8d7073a5/)
  })

  it('a run outside the recent list reads "this run", never "Unnamed run" or its id', async () => {
    mockProjectState.activeProjectId = 'proj-1'
    await mount(`/release-gate/${RUN_ID}`, [])
    expect(await screen.findByText('this run')).toBeInTheDocument()
    expect(screen.queryByText('Unnamed run')).toBeNull()
    expect(document.body.textContent).not.toMatch(/8d7073a5/)
  })
})
