import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

const reportBoundaryErrorMock = vi.fn()

vi.mock('../utils/errorReporting', () => ({
  reportBoundaryError: (...args: unknown[]) => reportBoundaryErrorMock(...args),
}))

function Thrower(): React.ReactNode {
  throw new Error('boom')
}

describe('ErrorBoundary', () => {
  it('renders child tree when no error occurs', () => {
    render(
      <ErrorBoundary>
        <div>Healthy UI</div>
      </ErrorBoundary>,
    )

    expect(screen.getByText('Healthy UI')).toBeInTheDocument()
  })

  it('shows fallback UI and reports errors', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Thrower />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
    expect(reportBoundaryErrorMock).toHaveBeenCalled()

    spy.mockRestore()
  })

  it('supports custom fallback renderer', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary fallback={(error) => <p>Custom: {error.message}</p>}>
        <Thrower />
      </ErrorBoundary>,
    )

    expect(screen.getByText('Custom: boom')).toBeInTheDocument()
    spy.mockRestore()
  })

  it('renders built-in recovery actions', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Thrower />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeInTheDocument()
    spy.mockRestore()
  })
})
