/**
 * Lightweight error boundary for individual dashboard sections (P4-7).
 *
 * Unlike the global ErrorBoundary, this renders a compact inline fallback
 * instead of a full-page error screen.  If one chart crashes, the rest
 * of the dashboard continues to work.
 */
import { Component, ErrorInfo, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  children: ReactNode
  /** Short message shown on crash (e.g. "Failed to load trend chart") */
  message?: string
}

interface State {
  error: Error | null
}

export class SectionErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Non-blocking telemetry — same pattern as global ErrorBoundary
    try {
      import('../../utils/errorReporting').then(m =>
        m.reportBoundaryError(error, info.componentStack ?? ''),
      )
    } catch {
      // Telemetry failure should never break the UI
    }
  }

  private handleRetry = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    if (!this.state.error) {
      return this.props.children
    }

    return (
      <div className="flex flex-col items-center justify-center h-48 rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-center p-6">
        <AlertTriangle className="h-8 w-8 text-[var(--status-broken)] mb-3" />
        <p className="text-sm font-medium text-[var(--color-text)]">
          {this.props.message || 'This section failed to load'}
        </p>
        <p className="text-xs text-[var(--color-text-muted)] mt-1 mb-3">
          {this.state.error.message}
        </p>
        <button
          onClick={this.handleRetry}
          className="px-3 py-1 text-xs rounded-md bg-[var(--color-bg-hover)] hover:bg-neutral-700 text-[var(--color-text)] transition-colors"
        >
          Try again
        </button>
      </div>
    )
  }
}
