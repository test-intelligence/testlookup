/**
 * R1B-9: the composites' boundary hid a failed block and logged nothing — a
 * render bug in a section chunk removed the whole advanced block with no trace.
 */
import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import QuietSectionBoundary from './QuietSectionBoundary'

const reportBoundaryError = vi.hoisted(() => vi.fn())
vi.mock('@/utils/errorReporting', () => ({ reportBoundaryError }))

function Boom(): never {
  throw new Error('section exploded')
}

let consoleError: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  reportBoundaryError.mockReset()
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => consoleError.mockRestore())

describe('QuietSectionBoundary', () => {
  it('renders its children while they render', () => {
    const { container } = render(
      <QuietSectionBoundary label="failures">
        <p>charts</p>
      </QuietSectionBoundary>,
    )
    expect(container.innerHTML).toBe('<p>charts</p>')
    expect(reportBoundaryError).not.toHaveBeenCalled()
  })

  it('a throwing section: nothing in its place, the page whole, and the failure logged and reported', () => {
    const { container } = render(
      <main>
        <p>the page</p>
        <QuietSectionBoundary label="failures">
          <Boom />
        </QuietSectionBoundary>
      </main>,
    )
    expect(container.querySelector('main')?.innerHTML).toBe('<p>the page</p>')
    expect(consoleError).toHaveBeenCalledWith(QuietSectionBoundary.message('failures'), expect.objectContaining({ message: 'section exploded' }))
    expect(reportBoundaryError).toHaveBeenCalledTimes(1)
    const [error, stack] = reportBoundaryError.mock.calls[0] as [Error, string]
    expect(error.message).toBe('section exploded')
    expect(stack).toContain('Boom')
  })

  it('telemetry that throws does not break the page', () => {
    reportBoundaryError.mockImplementation(() => {
      throw new Error('telemetry down')
    })
    const { container } = render(
      <div>
        <QuietSectionBoundary label="coverage">
          <Boom />
        </QuietSectionBoundary>
      </div>,
    )
    expect(container.querySelector('div')?.innerHTML).toBe('')
  })

  it('an error with no component stack is reported with an empty one', () => {
    const boundary = new QuietSectionBoundary({ label: 'failures', children: null })
    boundary.componentDidCatch(new Error('x'), { componentStack: null })
    expect(reportBoundaryError).toHaveBeenCalledWith(expect.objectContaining({ message: 'x' }), '')
  })

  it('names the composite in its console line', () => {
    expect(QuietSectionBoundary.message('suite-detail')).toBe('[catalogue] the suite-detail charts failed and were hidden; the page is unaffected.')
  })
})
