/**
 * DeepInvestigationPage jumps to the newest run of a suite when the user
 * CHANGES the suite and the focused run is not in it. It must not jump on a
 * deep link: with the suite filter now global (VIZ-303, flag on), a saved
 * suite is already applied at mount, and `/deep-investigate/<runId>` for a
 * run outside the six newest of that suite was redirected to `recentItems[0]`
 * while the direct fetch of the linked run was still loading — the link
 * silently opened a different run (E3 review, major).
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { create } from 'zustand'

vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn() }) }))
vi.mock('@/hooks/useDeepInvestigation', () => ({
  useFailureClusters: vi.fn(() => ({ data: [], isLoading: false, mutate: vi.fn() })),
  useDeepFindings: vi.fn(() => ({ data: [], isLoading: false, mutate: vi.fn() })),
  usePipelineStatus: vi.fn(() => ({ data: null })),
}))
const runsMock = vi.hoisted(() => ({
  useRuns: vi.fn(),
  useRun: vi.fn(),
}))
vi.mock('@/hooks/useRuns', () => runsMock)
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: ['payments', 'cart'], isLoading: false })) }))
vi.mock('@/hooks/useIntegrationHealth', () => ({
  useIntegrationStatus: vi.fn(() => ({ statuses: [], isLoading: false, isError: false })),
}))
vi.mock('@/hooks/useLlmBudget', () => ({
  useProjectUsage: vi.fn(() => ({ usage: null, isLoading: false, isError: false, refresh: vi.fn() })),
  useProjectQuota: vi.fn(() => ({ quota: null, isLoading: false, isError: false, refresh: vi.fn() })),
}))
vi.mock('@/hooks/useInvestigation', () => ({
  useInvestigation: vi.fn(() => ({ data: undefined, mutate: vi.fn() })),
  useInvestigations: vi.fn(() => ({ data: { items: [], total: 0 }, mutate: vi.fn() })),
}))
vi.mock('@/hooks/useAgentGovernance', () => ({
  useAgentPolicies: vi.fn(() => ({ policies: [], investigatorPolicy: null })),
}))
vi.mock('@/hooks/useDecisionTrail', () => ({ useDecisionTrail: vi.fn(() => ({ data: undefined, isLoading: false })) }))

import DeepInvestigationPage from './DeepInvestigationPage'
import { useMultiFiltersFlagStore } from '@/store/multiFiltersFlag'
import { useProjectStore } from '@/store/projectStore'
import { useSuiteStore } from '@/store/suiteStore'
import { settleScopeNow } from '@/store/settledScope'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'

type Run = { id: string; suite_names?: string[]; primary_suite_name?: string }
/** Newest runs per suite filter (`''` = unfiltered). */
let recentBySuite: Record<string, Run[]>
/** Direct-fetch answers per run id; `'loading'` (or absent) = still in
 *  flight. A store, so a test can land an answer and the page re-renders. */
const useDirect = create<Record<string, Run | 'loading'>>(() => ({}))
const answer = (runId: string, value: Run | 'loading') =>
  act(async () => { useDirect.setState({ [runId]: value }) })

function suiteKeyOf(params: Record<string, unknown> | undefined): string {
  const s = params?.suite_name
  if (Array.isArray(s)) return [...s].sort().join(',')
  return typeof s === 'string' ? s : ''
}

function Where() {
  const loc = useLocation()
  return <output data-testid="where">{loc.pathname}</output>
}

function mount(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/deep-investigate/:runId" element={<><DeepInvestigationPage /><Where /></>} />
        <Route path="/deep-investigate" element={<><DeepInvestigationPage /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

const where = () => screen.getByTestId('where').textContent
const tick = (ms = 30) => act(async () => { await new Promise((r) => setTimeout(r, ms)) })

beforeEach(() => {
  localStorage.clear()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useSuiteStore.setState({ activeSuiteNames: [], scopedProjectId: null })
  useMultiFiltersFlagStore.setState({ enabled: true, resolved: true })
  settleScopeNow()
  recentBySuite = {
    '': [{ id: 'run-new', suite_names: ['payments'] }, { id: 'run-linked', suite_names: ['cart'] }],
    payments: [{ id: 'run-pay-1', suite_names: ['payments'] }],
    cart: [{ id: 'run-cart-1', suite_names: ['cart'] }],
  }
  useDirect.setState(Object.fromEntries(Object.keys(useDirect.getState()).map((k) => [k, 'loading' as const])))
  runsMock.useRuns.mockImplementation((params?: Record<string, unknown>) => ({
    data: { items: recentBySuite[suiteKeyOf(params)] ?? [] },
    isLoading: false,
    isValidating: false,
  }))
  runsMock.useRun.mockImplementation((runId?: string) => {
    const value = useDirect((s) => (runId ? s[runId] : undefined))
    if (!runId) return { data: undefined, isLoading: false }
    if (value === 'loading' || value === undefined) return { data: undefined, isLoading: true }
    return { data: value, isLoading: false }
  })
})

describe('DeepInvestigationPage suite redirect', () => {
  it('a deep link with a SAVED global suite is not redirected while the linked run loads', async () => {
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT)
    settleScopeNow()
    mount('/deep-investigate/run-old')
    await tick()
    expect(where()).toBe('/deep-investigate/run-old')
    // Nor once it has loaded (the link names that run; the suite did not change).
    await answer('run-old', { id: 'run-old', suite_names: ['cart'] })
    await tick()
    expect(where()).toBe('/deep-investigate/run-old')
  })

  it('the flag resolving ON after mount (saved suite appears) is not a suite change', async () => {
    useMultiFiltersFlagStore.setState({ enabled: false, resolved: false })
    useSuiteStore.getState().setActiveSuites(['payments'], PROJECT)
    settleScopeNow()
    mount('/deep-investigate/run-linked')
    await tick()
    await act(async () => { useMultiFiltersFlagStore.getState().setEnabled(true) })
    await tick()
    expect(where()).toBe('/deep-investigate/run-linked')
  })

  it('CHANGING the suite to one the focused run is not in jumps to that suite\'s newest run', async () => {
    mount('/deep-investigate/run-linked')
    await tick()
    expect(where()).toBe('/deep-investigate/run-linked')
    await answer('run-linked', { id: 'run-linked', suite_names: ['cart'] })
    await act(async () => {
      useSuiteStore.getState().setActiveSuites(['payments'], PROJECT)
      settleScopeNow()
    })
    await waitFor(() => expect(where()).toBe('/deep-investigate/run-pay-1'))
  })

  it('changing the suite to one that CONTAINS the focused run keeps it', async () => {
    mount('/deep-investigate/run-linked')
    await tick()
    await answer('run-linked', { id: 'run-linked', suite_names: ['cart'] })
    await act(async () => {
      useSuiteStore.getState().setActiveSuites(['cart'], PROJECT)
      settleScopeNow()
    })
    await tick()
    expect(where()).toBe('/deep-investigate/run-linked')
  })

  it('never redirects while the direct fetch of the focused run is loading', async () => {
    mount('/deep-investigate/run-linked')
    await tick()
    await answer('run-linked', 'loading')
    await act(async () => {
      useSuiteStore.getState().setActiveSuites(['payments'], PROJECT)
      settleScopeNow()
    })
    await tick()
    expect(where()).toBe('/deep-investigate/run-linked')
    await answer('run-linked', { id: 'run-linked', suite_names: ['cart'] })
    await waitFor(() => expect(where()).toBe('/deep-investigate/run-pay-1'))
  })
})
