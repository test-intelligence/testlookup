import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CoverageMapSectionProps, HeatmapSectionProps } from './sectionContracts'

/** The two flags, read through the REAL seam (`useCatalogueRollout.ts`); which keys were looked up. */
const flags = vi.hoisted(() => ({ values: {} as Record<string, boolean>, asked: [] as string[] }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => {
    flags.asked.push(key)
    return flags.values[key] ?? false
  },
  useFeatureFlagStatus: (key: string) => flags.values[key],
}))

// The sections are their owners' (pinned contracts): stand-ins that show the props they were given.
const mounted = vi.hoisted(() => ({ map: [] as unknown[], heatmap: [] as unknown[] }))
vi.mock('./CoverageMapSection', () => ({
  default: (props: CoverageMapSectionProps) => {
    mounted.map.push(props)
    return <div data-testid="map" />
  },
}))
vi.mock('./HeatmapSection', () => ({
  default: (props: HeatmapSectionProps) => {
    mounted.heatmap.push(props)
    return <div data-testid="heatmap" />
  },
}))

import CoverageAdvanced, {
  COVERAGE_HEATMAP_SECTION_HEIGHT,
  COVERAGE_MAP_SECTION_HEIGHT,
} from './CoverageAdvanced'
import { COVERAGE_ADVANCED_HEIGHT } from './CoverageAdvanced.model'

beforeEach(() => {
  flags.values = {}
  flags.asked = []
  mounted.map = []
  mounted.heatmap = []
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('CoverageAdvanced (Coverage page, Wave 3)', () => {
  it('every flag off: renders nothing, and the only lookup is the catalogue seam', () => {
    const { container } = render(<CoverageAdvanced days={30} suiteFilter={null} />)
    expect(container.innerHTML).toBe('')
    expect(new Set(flags.asked)).toEqual(new Set(['viz_chart_data_api']))
  })

  it('only viz_advanced_charts on: nothing, and the advanced flag is not even looked up', () => {
    flags.values = { viz_advanced_charts: true }
    const { container } = render(<CoverageAdvanced days={30} suiteFilter={null} />)
    expect(container.innerHTML).toBe('')
    expect(flags.asked).not.toContain('viz_advanced_charts')
  })

  it('only viz_chart_data_api on: nothing (no placeholder, no section chunk)', () => {
    flags.values = { viz_chart_data_api: true }
    const { container } = render(<CoverageAdvanced days={30} suiteFilter={null} />)
    expect(container.innerHTML).toBe('')
    expect(mounted.map).toEqual([])
  })

  it('both on, far from the reader: the map is a placeholder of its height; the heatmap section is mounted bare (it owns its LazySection)', async () => {
    flags.values = { viz_chart_data_api: true, viz_advanced_charts: true }
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
    const { container } = render(<CoverageAdvanced days={30} suiteFilter={null} />)
    // While the sections module loads, ONE placeholder holds the whole block's height.
    const outer = container.firstElementChild as HTMLElement
    expect(outer.getAttribute('aria-hidden')).toBe('true')
    expect(outer.style.minHeight).toBe(`${COVERAGE_ADVANCED_HEIGHT}px`)
    expect(COVERAGE_ADVANCED_HEIGHT).toBe(14 + COVERAGE_MAP_SECTION_HEIGHT + 14 + COVERAGE_HEATMAP_SECTION_HEIGHT)
    expect(await screen.findByTestId('heatmap')).toBeInTheDocument()
    const placeholders = [...container.querySelectorAll('[data-lazy-section]')] as HTMLElement[]
    expect(placeholders.map((el) => [el.getAttribute('data-lazy-section'), el.style.minHeight])).toEqual([
      ['coverage-map', `${COVERAGE_MAP_SECTION_HEIGHT}px`],
    ])
    expect(mounted.map).toEqual([])
  })

  it('both on and near: the map and the environment / release heatmaps, with the page scope', async () => {
    flags.values = { viz_chart_data_api: true, viz_advanced_charts: true }
    render(<CoverageAdvanced days={14} suiteFilter={['a', 'b']} />)
    expect(await screen.findByTestId('map')).toBeInTheDocument()
    expect(await screen.findByTestId('heatmap')).toBeInTheDocument()
    await waitFor(() => expect(mounted.map[mounted.map.length - 1]).toEqual({ days: 14, suiteFilter: ['a', 'b'] }))
    expect(mounted.heatmap[mounted.heatmap.length - 1]).toEqual({ days: 14, suiteFilter: ['a', 'b'], kinds: ['suite_environment', 'suite_release'] })

  })
})
