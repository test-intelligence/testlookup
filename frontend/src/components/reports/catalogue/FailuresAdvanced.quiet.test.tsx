/**
 * The composite chunk failing (a deploy removed it and the one reload did not
 * help, or it throws) leaves the Failures page whole: the additions vanish,
 * nothing else does.
 */
import { act, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import FailuresAdvanced from './FailuresAdvanced'

vi.mock('./FailuresAdvancedSections', () => ({
  default: () => {
    throw new Error('chunk gone')
  },
}))

describe('FailuresAdvanced when its sections chunk fails', () => {
  it('renders nothing in its place, and the page around it stays', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = render(
      <main>
        <p>the page</p>
        <FailuresAdvanced days={7} suiteFilter={null} />
      </main>,
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(container.querySelector('main')?.innerHTML).toBe('<p>the page</p>')
    error.mockRestore()
  })
})
