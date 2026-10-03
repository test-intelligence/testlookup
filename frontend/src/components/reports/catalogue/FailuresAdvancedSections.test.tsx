/**
 * The Failures composite's placeholders (R2-B F-15): while a section is not
 * near yet its placeholder holds the height the section DRAWS at, so nothing
 * below it moves when it mounts. They over-reserved (groups 980 for 840 drawn,
 * the ladder 520 for 372, the scatter 580 for 543), and the scatter moved up
 * 289 px while a reader scrolled. Measured: Failures at 1280 x 800, both flags
 * on, every section mounted (`docs/viz-work/w3/x3/groups-win.jsonl`, "heights":
 * groups 860 with the keyboard hint's reserved line, drill 372, scatter 543).
 */
import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import FailuresAdvancedSections from './FailuresAdvancedSections'

vi.mock('./FailureGroupsSection', () => ({ default: () => null }))
vi.mock('./FailuresDrill', () => ({ default: () => null }))
vi.mock('./ScatterSection', () => ({ default: () => null }))

/** An observer that never reports: every section stays a placeholder. */
class NeverNear {
  observe() {}
  disconnect() {}
  unobserve() {}
  takeRecords() {
    return []
  }
}

beforeEach(() => vi.stubGlobal('IntersectionObserver', NeverNear))
afterEach(() => vi.unstubAllGlobals())

describe('FailuresAdvancedSections placeholders', () => {
  it('hold the measured drawn heights, in page order', () => {
    const { container } = render(<FailuresAdvancedSections days={30} suiteFilter={null} />)
    const placeholders = [...container.querySelectorAll<HTMLElement>('[data-lazy-section]')].map((el) => [
      el.getAttribute('data-lazy-section'),
      el.style.minHeight,
    ])
    expect(placeholders).toEqual([
      ['failures-groups', '860px'],
      ['failures-drill', '372px'],
      ['failures-scatter', '543px'],
    ])
  })
})
