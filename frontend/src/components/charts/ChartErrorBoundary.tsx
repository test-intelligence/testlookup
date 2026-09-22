/**
 * One error boundary per chart frame (VIZ-107): a renderer that throws takes
 * down its own frame, never the page. A missing lazy engine chunk (a tab open
 * across a deploy) reads "A new version is available — Reload" instead.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { isStaleBuildError, STALE_BUILD_ACTION, STALE_BUILD_MESSAGE } from './engines/lazyChartEngine'

interface Props {
  children: ReactNode
  /** Changing this resets a caught error (e.g. new data arrived). */
  resetKey?: unknown
}

interface State {
  error: Error | null
  resetKey: unknown
}

export const CHART_DRAW_ERROR = 'This chart could not be drawn.'

export default class ChartErrorBoundary extends Component<Props, State> {
  state: State = { error: null, resetKey: this.props.resetKey }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) return { resetKey: props.resetKey, error: null }
    return null
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Non-blocking telemetry, as SectionErrorBoundary does.
    import('../../utils/errorReporting')
      .then((m) => m.reportBoundaryError(error, info.componentStack ?? ''))
      .catch(() => {})
  }

  private retry = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    const stale = isStaleBuildError(error)
    // Static text, no live role: a page of frames must not become a stack of
    // alerts (ChartAnnouncer owns announcing a change).
    return (
      <div
        data-chart-draw-error={stale ? 'stale-build' : 'error'}
        className="flex min-h-[inherit] flex-col items-center justify-center gap-2 p-4 text-center text-sm text-[var(--color-text-secondary)]"
      >
        <span>{stale ? STALE_BUILD_MESSAGE : CHART_DRAW_ERROR}</span>
        <button
          type="button"
          onClick={stale ? () => window.location.reload() : this.retry}
          className="rounded border border-[var(--color-border-light)] px-3 py-1 text-[var(--color-text)]"
        >
          {stale ? STALE_BUILD_ACTION : 'Try again'}
        </button>
      </div>
    )
  }
}
