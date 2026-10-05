/**
 * R1B-9, the composites' side: Coverage, Failures and Suite detail each hide a
 * failed Wave 3 block behind the SHARED boundary, so the failure is logged
 * under the page's name and reported, and the page around it stays whole.
 */
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CoverageAdvanced from './CoverageAdvanced'
import FailuresAdvanced from './FailuresAdvanced'
import SuiteDetailAdvanced from './SuiteDetailAdvanced'
import QuietSectionBoundary from './QuietSectionBoundary'

const reportBoundaryError = vi.hoisted(() => vi.fn())
vi.mock('@/utils/errorReporting', () => ({ reportBoundaryError }))
const gone = vi.hoisted(() => () => ({
  default: () => {
    throw new Error('chunk gone')
  },
}))
vi.mock('./CoverageAdvancedSections', gone)
vi.mock('./FailuresAdvancedSections', gone)
vi.mock('./SuiteDetailAdvancedSections', gone)

let consoleError: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  reportBoundaryError.mockReset()
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => consoleError.mockRestore())

describe('the composites hide a failed block through QuietSectionBoundary', () => {
  it.each([
    ['coverage', <CoverageAdvanced key="c" days={7} suiteFilter={null} />],
    ['failures', <FailuresAdvanced key="f" days={7} suiteFilter={null} />],
    ['suite-detail', <SuiteDetailAdvanced key="s" days={7} suiteName="Auth" />],
  ] as [string, ReactElement][])('%s: nothing in its place, the page whole, the failure logged and reported', async (label, composite) => {
    const { container } = render(
      <main>
        <p>the page</p>
        {composite}
      </main>,
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(container.querySelector('main')?.innerHTML).toBe('<p>the page</p>')
    expect(consoleError).toHaveBeenCalledWith(QuietSectionBoundary.message(label), expect.objectContaining({ message: 'chunk gone' }))
    expect(reportBoundaryError).toHaveBeenCalledWith(expect.objectContaining({ message: 'chunk gone' }), expect.any(String))
  })
})
