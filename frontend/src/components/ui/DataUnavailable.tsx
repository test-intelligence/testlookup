import { AlertTriangle, RefreshCw } from 'lucide-react'
import { describeLoadError } from '@/utils/loadError'

interface Props {
  /** The SWR `error` for the fetch that failed. */
  error: unknown
  /** Usually the page's SWR `mutate`; omitted when there is nothing to retry. */
  onRetry?: () => void
  /** Test hook so a page's own assertions can target its instance. */
  testId?: string
}

/**
 * The "we could not look" panel.
 *
 * Deliberately NOT an {@link ./EmptyState.tsx} variant. An empty state is a
 * claim about the data ("no runs in this window"); this is a claim about the
 * request ("we never got an answer"). Rendering the first when the second is
 * true is the defect this component exists to make impossible — Overview,
 * Coverage, Trends and Defects all read only `data` and `isLoading` from SWR,
 * so a total backend outage rendered as "No test runs yet … widening the time
 * window will not help": a confident, wrong, and actively misleading
 * instruction, since the one thing that would help is fixing the backend.
 *
 * Styled with the warning tokens rather than the muted empty-state grey so it
 * is visibly a problem, not a resting state.
 */
export default function DataUnavailable({ error, onRetry, testId }: Props) {
  const info = describeLoadError(error)

  return (
    <div
      role="alert"
      data-testid={testId ?? 'data-unavailable'}
      className="flex flex-col items-center justify-center gap-3 py-20 text-center"
    >
      <div
        className="p-4 rounded-2xl"
        style={{ background: 'var(--status-broken-bg)', color: 'var(--status-broken)' }}
      >
        <AlertTriangle className="h-8 w-8" aria-hidden />
      </div>
      <h3 className="text-lg font-semibold m-0" style={{ color: 'var(--status-broken)' }}>
        {info.title}
      </h3>
      <p className="text-sm max-w-md m-0 text-[var(--color-text-muted)]">{info.message}</p>
      {info.status != null && (
        <p className="text-xs m-0 text-[var(--color-text-faint)] tabular-nums">
          HTTP {info.status}
        </p>
      )}
      {info.retryable && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[13px] font-medium transition-colors"
          style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }}
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          Retry
        </button>
      )}
    </div>
  )
}
