/**
 * TestLookup — React Error Boundary.
 *
 * Catches any uncaught render-phase or lifecycle errors in its child tree.
 * Reports them to the backend telemetry endpoint via reportBoundaryError().
 * Shows a minimal fallback UI instead of a blank screen.
 */
import React, { Component, ErrorInfo, ReactNode } from 'react'
import { reportBoundaryError } from '../utils/errorReporting'

interface Props {
  children: ReactNode
  /** Optional custom fallback. Receives the error if you want to display details. */
  fallback?: (error: Error) => ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportBoundaryError(error, info.componentStack ?? '')
  }

  private handleReload = (): void => {
    window.location.reload()
  }

  private handleReset = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    const { error } = this.state

    if (!error) {
      return this.props.children
    }

    if (this.props.fallback) {
      return this.props.fallback(error)
    }

    return (
      <div className="min-h-screen bg-[var(--color-bg-card)] flex items-center justify-center p-6">
        <div className="max-w-md w-full bg-[var(--color-bg-secondary)] rounded-xl border border-[var(--color-border)] p-8 text-center space-y-4">
          <div className="text-4xl">⚠️</div>
          <h1 className="text-xl font-semibold text-[var(--color-text)]">Something went wrong</h1>
          <p className="text-sm text-[var(--color-text-muted)]">
            An unexpected error occurred in the application. The error has been reported
            automatically.
          </p>
          <p className="text-xs text-red-400 font-mono bg-[var(--color-bg-card)] rounded p-2 text-left break-all">
            {error.message}
          </p>
          <div className="flex gap-3 justify-center pt-2">
            <button
              onClick={this.handleReset}
              className="px-4 py-2 text-sm rounded-lg bg-[var(--color-bg-hover)] hover:bg-neutral-700 text-[var(--color-text)] transition-colors"
            >
              Try again
            </button>
            <button
              onClick={this.handleReload}
              className="px-4 py-2 text-sm rounded-lg bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] text-[var(--color-btn-primary-text)] transition-colors"
            >
              Reload page
            </button>
          </div>
        </div>
      </div>
    )
  }
}
