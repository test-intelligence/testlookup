/**
 * K1: the ONE seam between the report pages and the two catalogue flags.
 *
 * Rendered for real under an isolated SWR cache with a spy status service, so
 * "false until loaded" and "false on failure" are what the hook does with a
 * real pending and a real rejected request, not what a mocked hook returns.
 */
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { VIZ_FLAGS } from '@/config/vizFlags'

const status = vi.fn()

vi.mock('@/services/featureFlagService', () => ({
  featureFlagService: {
    list: vi.fn(async () => []),
    status: (...args: unknown[]) => status(...args),
  },
}))

let activeProjectId: string | null = 'proj-1'
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId }),
}))

import { useAdvancedRollout, useCatalogueRollout, useCatalogueRolloutStatus } from './useCatalogueRollout'

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>{children}</SWRConfig>
)

/** Answers per flag key; a function lets a test hold one pending or reject it. */
function answer(byKey: Record<string, () => Promise<unknown>>) {
  status.mockImplementation((key: string) => (byKey[key] ?? (async () => ({ enabled: false })))())
}

describe('useCatalogueRollout (K1)', () => {
  beforeEach(() => {
    status.mockReset()
    activeProjectId = 'proj-1'
  })

  it('is false while the status request is pending, then follows the flag', async () => {
    let release!: (value: unknown) => void
    answer({ [VIZ_FLAGS.chartDataApi]: () => new Promise((resolve) => (release = resolve)) })
    const { result } = renderHook(() => useCatalogueRollout(), { wrapper })
    expect(result.current).toBe(false)
    await waitFor(() => expect(status).toHaveBeenCalled())
    release({ enabled: true })
    await waitFor(() => expect(result.current).toBe(true))
  })

  it('asks for viz_chart_data_api for the active project, through the constant', async () => {
    answer({ [VIZ_FLAGS.chartDataApi]: async () => ({ enabled: true }) })
    renderHook(() => useCatalogueRollout(), { wrapper })
    await waitFor(() => expect(status).toHaveBeenCalledWith('viz_chart_data_api', 'proj-1'))
  })

  it('is false when the status request fails: a gate that cannot be read stays closed', async () => {
    answer({ [VIZ_FLAGS.chartDataApi]: async () => Promise.reject(new Error('500')) })
    const { result } = renderHook(() => ({ on: useCatalogueRollout(), known: useCatalogueRolloutStatus() }), {
      wrapper,
    })
    await waitFor(() => expect(result.current.known).toBe(false))
    expect(result.current.on).toBe(false)
  })

  it('keeps "not known yet" distinct from "off" for a page that must not flash a card', async () => {
    let release!: (value: unknown) => void
    answer({ [VIZ_FLAGS.chartDataApi]: () => new Promise((resolve) => (release = resolve)) })
    const { result } = renderHook(() => useCatalogueRolloutStatus(), { wrapper })
    expect(result.current).toBeUndefined()
    await waitFor(() => expect(status).toHaveBeenCalled())
    release({ enabled: false })
    await waitFor(() => expect(result.current).toBe(false))
  })

  it('is false when the server answers without an enabled field', async () => {
    answer({ [VIZ_FLAGS.chartDataApi]: async () => ({}) })
    const { result } = renderHook(() => useCatalogueRolloutStatus(), { wrapper })
    await waitFor(() => expect(result.current).toBe(false))
  })
})

describe('useAdvancedRollout (Wave 3 sections)', () => {
  beforeEach(() => status.mockReset())

  it.each([
    [false, false, false],
    [true, false, false],
    [false, true, false],
    [true, true, true],
  ])('catalogue %s, advanced %s -> %s', async (catalogue, advanced, expected) => {
    answer({
      [VIZ_FLAGS.chartDataApi]: async () => ({ enabled: catalogue }),
      [VIZ_FLAGS.advancedCharts]: async () => ({ enabled: advanced }),
    })
    const { result } = renderHook(() => ({ on: useAdvancedRollout(), cat: useCatalogueRolloutStatus() }), { wrapper })
    await waitFor(() => expect(result.current.cat).toBe(catalogue))
    await waitFor(() => expect(status).toHaveBeenCalledWith('viz_advanced_charts', 'proj-1'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    await waitFor(() => expect(result.current.on).toBe(expected))
  })

  it('is false while either lookup is still pending', async () => {
    answer({
      [VIZ_FLAGS.chartDataApi]: async () => ({ enabled: true }),
      [VIZ_FLAGS.advancedCharts]: () => new Promise(() => {}),
    })
    const { result } = renderHook(() => ({ on: useAdvancedRollout(), cat: useCatalogueRollout() }), { wrapper })
    await waitFor(() => expect(result.current.cat).toBe(true))
    expect(result.current.on).toBe(false)
  })
})
