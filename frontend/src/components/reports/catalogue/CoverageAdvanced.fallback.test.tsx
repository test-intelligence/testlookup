/**
 * While the sections module loads, `CoverageAdvanced` holds ONE placeholder
 * of the block's height, so nothing below it jumps when the sections arrive.
 * With `sections` (UX redesign P3: one tab, one section) it holds only the
 * sections it will draw; with none given, exactly the old block height.
 *
 * The module never arrives here (the lazy loader is held forever), so every
 * render sits in the fallback.
 */
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/lazyWithRetry', async () => {
  const { lazy } = await import('react')
  return { lazyWithRetry: () => lazy(() => new Promise<never>(() => {})) }
})

import CoverageAdvanced, { COVERAGE_HEATMAP_SECTION_HEIGHT, COVERAGE_MAP_SECTION_HEIGHT } from './CoverageAdvanced'
import { COVERAGE_ADVANCED_HEIGHT } from './CoverageAdvanced.model'

/** The fallback's held height, px. */
function held(element: ReactElement): string {
  const { container, unmount } = render(element)
  const outer = container.firstElementChild as HTMLElement
  expect(outer.getAttribute('aria-hidden')).toBe('true')
  const height = outer.style.minHeight
  unmount()
  return height
}

describe('CoverageAdvanced while its sections module loads', () => {
  it('no `sections`, or both: the whole block, as before', () => {
    expect(held(<CoverageAdvanced days={30} suiteFilter={null} />)).toBe(`${COVERAGE_ADVANCED_HEIGHT}px`)
    expect(held(<CoverageAdvanced days={30} suiteFilter={null} sections={['coverage-map', 'heatmap-suite_environment']} />)).toBe(
      `${COVERAGE_ADVANCED_HEIGHT}px`,
    )
  })

  it('one section: its height and the block’s top margin, no gap', () => {
    expect(held(<CoverageAdvanced days={30} suiteFilter={null} sections={['coverage-map']} />)).toBe(`${14 + COVERAGE_MAP_SECTION_HEIGHT}px`)
    expect(held(<CoverageAdvanced days={30} suiteFilter={null} sections={['heatmap-suite_environment']} />)).toBe(
      `${14 + COVERAGE_HEATMAP_SECTION_HEIGHT}px`,
    )
  })
})
