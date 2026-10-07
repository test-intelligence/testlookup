import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import Disclosure from './Disclosure'

beforeEach(() => localStorage.clear())

describe('Disclosure', () => {
  it('starts closed: the content is not rendered until opened', () => {
    render(
      <Disclosure title="How this was decided" summary="3 checks">
        <p>Inner content</p>
      </Disclosure>,
    )
    const button = screen.getByRole('button', { name: /How this was decided/ })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Inner content')).toBeNull()
    expect(screen.getByText('3 checks')).toBeInTheDocument()
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Inner content')).toBeInTheDocument()
  })

  it('defaultOpen opens it', () => {
    render(
      <Disclosure title="Details" defaultOpen>
        <p>Inner content</p>
      </Disclosure>,
    )
    expect(screen.getByText('Inner content')).toBeInTheDocument()
  })

  it('remembers the reader’s choice under persistKey, and only there', () => {
    const first = render(
      <Disclosure title="Pipeline" persistKey="gate.pipeline">
        <p>Inner content</p>
      </Disclosure>,
    )
    fireEvent.click(screen.getByRole('button', { name: /Pipeline/ }))
    first.unmount()
    expect(localStorage.getItem('tl.disclosure.gate.pipeline')).toBe('1')
    render(
      <Disclosure title="Pipeline" persistKey="gate.pipeline">
        <p>Inner content</p>
      </Disclosure>,
    )
    expect(screen.getByText('Inner content')).toBeInTheDocument()
  })
})
