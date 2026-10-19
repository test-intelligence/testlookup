import { AlertTriangle, RefreshCw } from 'lucide-react'
import { describeLoadError } from '@/utils/loadError'
import EmptyState from './EmptyState'

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
 * A different CLAIM from an empty state, drawn in the same look. An empty
 * state is a claim about the data ("no runs in this window"); this is a claim
 * about the request ("we never got an answer"). Rendering the first when the
 * second is true is the defect this component exists to make impossible —
 * Overview, Coverage, Trends and Defects all read only `data` and `isLoading`
 * from SWR, so a total backend outage rendered as "No test runs yet …
 * widening the time window will not help": a confident, wrong, and actively
 * misleading instruction, since the one thing that would help is fixing the
 * backend.
 *
 * W29a UIX-11: one look for loading, empty and error — it renders through
 * `EmptyState` in its `warning` tone (the warning icon tile, a neutral
 * headline), keeps `role="alert"`, the reason, "HTTP {status}" and Retry.
 */
export default function DataUnavailable({ error, onRetry, testId }: Props) {
  const info = describeLoadError(error)
  const canRetry = info.retryable && onRetry
  const details =
    info.status != null || canRetry ? (
      <div className="flex flex-col items-center gap-3">
        {info.status != null && (
          <p className="text-xs m-0 text-[var(--color-text-faint)] tabular-nums">
            HTTP {info.status}
          </p>
        )}
        {canRetry && (
          <button type="button" onClick={onRetry} className="btn-secondary btn-sm inline-flex items-center gap-1.5">
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Retry
          </button>
        )}
      </div>
    ) : undefined

  return (
    <EmptyState
      tone="warning"
      role="alert"
      testId={testId ?? 'data-unavailable'}
      icon={<AlertTriangle className="h-8 w-8" aria-hidden />}
      title={info.title}
      description={info.message}
      action={details}
    />
  )
}
