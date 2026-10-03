/**
 * Suite detail's sections module failing to load (a deploy removed it and the
 * one reload did not help, or it throws) leaves the page whole: the Wave-3
 * additions vanish, nothing else does (integrator I, the composite split).
 */
import { act, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import SuiteDetailAdvanced from './SuiteDetailAdvanced'

vi.mock('./useCatalogueRollout', () => ({ useAdvancedRollout: () => true }))
vi.mock('./SuiteDetailAdvancedSections', () => ({
  default: () => {
    throw new Error('chunk gone')
  },
}))

describe('SuiteDetailAdvanced when its sections chunk fails', () => {
  it('renders nothing in its place, and the page around it stays', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = render(
      <main>
        <p>the page</p>
        <SuiteDetailAdvanced days={7} suiteName="Auth" />
      </main>,
    )
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20))
    })
    expect(container.querySelector('main')?.innerHTML).toBe('<p>the page</p>')
    error.mockRestore()
  })
})
