/**
 * While each section's own chunk downloads, its placeholder holds that
 * section's height, so the Test Cases table below does not jump twice
 * (integrator I, the composite split). The section modules never arrive here.
 */
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SuiteDetailAdvancedSections from './SuiteDetailAdvancedSections'
import { SUITE_HEATMAP_MIN_HEIGHT, SUITE_SCATTER_MIN_HEIGHT } from './SuiteDetailAdvanced.model'

const never = vi.hoisted(() => new Promise<never>(() => {}))
vi.mock('./HeatmapSection', async () => never)
vi.mock('./ScatterSection', async () => never)

describe('SuiteDetailAdvancedSections while the sections load', () => {
  it('holds each section’s height, in page order, and nothing else', () => {
    const { container } = render(<SuiteDetailAdvancedSections days={30} suiteFilter={['Auth']} />)
    const block = container.querySelector('[data-suite-advanced]') as HTMLElement
    const placeholders = [...block.querySelectorAll('div[aria-hidden="true"]')] as HTMLElement[]
    expect(placeholders.map((el) => el.style.minHeight)).toEqual([`${SUITE_HEATMAP_MIN_HEIGHT}px`, `${SUITE_SCATTER_MIN_HEIGHT}px`])
  })
})
