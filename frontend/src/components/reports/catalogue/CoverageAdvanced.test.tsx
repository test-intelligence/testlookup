import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CoverageMapSectionProps, HeatmapSectionProps } from './sectionContracts'

/** Which flag keys were looked up: none, since Phase D, S4 (the block mounts unconditionally). */
const flags = vi.hoisted(() => ({ asked: [] as string[] }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => {
    flags.asked.push(key)
    return false
  },
  useFeatureFlagStatus: (key: string) => {
    flags.asked.push(key)
    return false
  },
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
  flags.asked = []
  mounted.map = []
  mounted.heatmap = []
})
afterEach(() => {
  vi.unstubAllGlobals()
})

/** Lazy section chunks can outlast findBy's default 1 s on a loaded machine. */
const LAZY_TIMEOUT = 5_000

describe('CoverageAdvanced (Coverage page, Wave 3)', () => {
  it('far from the reader: the map is a placeholder of its height; the heatmap section is mounted bare (it owns its LazySection)', async () => {
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
    expect(await screen.findByTestId('heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    const placeholders = [...container.querySelectorAll('[data-lazy-section]')] as HTMLElement[]
    expect(placeholders.map((el) => [el.getAttribute('data-lazy-section'), el.style.minHeight])).toEqual([
      ['coverage-map', `${COVERAGE_MAP_SECTION_HEIGHT}px`],
    ])
    expect(mounted.map).toEqual([])
  })

  it('near: the map and the environment / release heatmaps, with the page scope, and no flag is looked up', async () => {
    render(<CoverageAdvanced days={14} suiteFilter={['a', 'b']} />)
    expect(await screen.findByTestId('map', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(await screen.findByTestId('heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    await waitFor(() => expect(mounted.map[mounted.map.length - 1]).toEqual({ days: 14, suiteFilter: ['a', 'b'] }))
    expect(mounted.heatmap[mounted.heatmap.length - 1]).toEqual({ days: 14, suiteFilter: ['a', 'b'], kinds: ['suite_environment', 'suite_release'] })
    expect(flags.asked).toEqual([])
  })
})

describe('CoverageAdvanced — `sections` picks which sections render (UX redesign P3)', () => {
  it('both listed (in either order): the same block as no prop, the map first', async () => {
    render(<CoverageAdvanced days={14} suiteFilter={null} sections={['heatmap-suite_environment', 'coverage-map']} />)
    const map = await screen.findByTestId('map', {}, { timeout: LAZY_TIMEOUT })
    const heatmap = await screen.findByTestId('heatmap', {}, { timeout: LAZY_TIMEOUT })
    expect(map.compareDocumentPosition(heatmap) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('the map alone: the map, with the page scope; the heatmap section is never mounted', async () => {
    render(<CoverageAdvanced days={14} suiteFilter="checkout" sections={['coverage-map']} />)
    expect(await screen.findByTestId('map', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    await waitFor(() => expect(mounted.map[mounted.map.length - 1]).toEqual({ days: 14, suiteFilter: 'checkout' }))
    expect(screen.queryByTestId('heatmap')).toBeNull()
    expect(mounted.heatmap).toEqual([])
  })

  it('the heatmap alone: the environment / release heatmaps; the map is never mounted, not even as a placeholder', async () => {
    class FarAway {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal('IntersectionObserver', FarAway)
    const { container } = render(<CoverageAdvanced days={30} suiteFilter={null} sections={['heatmap-suite_environment']} />)
    expect(await screen.findByTestId('heatmap', {}, { timeout: LAZY_TIMEOUT })).toBeInTheDocument()
    expect(mounted.heatmap[mounted.heatmap.length - 1]).toEqual({ days: 30, suiteFilter: null, kinds: ['suite_environment', 'suite_release'] })
    expect(container.querySelector('[data-lazy-section="coverage-map"]')).toBeNull()
    expect(mounted.map).toEqual([])
  })

  it('none: an empty block, nothing mounted', async () => {
    const { container } = render(<CoverageAdvanced days={30} suiteFilter={null} sections={[]} />)
    await waitFor(() => expect(container.querySelector('[data-coverage-advanced]')).not.toBeNull(), { timeout: LAZY_TIMEOUT })
    expect((container.querySelector('[data-coverage-advanced]') as HTMLElement).children).toHaveLength(0)
    expect(mounted.map).toEqual([])
    expect(mounted.heatmap).toEqual([])
  })
})
