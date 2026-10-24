/**
 * W12 SRCH-01 — the Similar failures tab of a FAILED / BROKEN result (the full
 * test page only): up to five failed or broken results of the SAME project
 * that sit nearest to this one in the semantic index
 * (`GET /search/similar/{id}?limit=5`, asked when the tab opens — the tab is
 * mounted only while open).
 *
 * An empty answer says WHY, by `semantic_status`: nothing near it, no ChromaDB
 * server, or an index that answered with an error; a refused request says it
 * could not load. Names are React text; each row links to that result.
 */
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useNow } from '@/hooks/useNow'
import { useSimilarFailures } from '@/hooks/useSimilarFailures'
import { relativeTime } from './searchModel'
import { SIMILAR_LOAD_ERROR, emptyStateText } from './similarFailuresModel'

export default function SimilarFailuresPanel({ testId }: { testId: string }) {
  const { data, error, isLoading } = useSimilarFailures(testId, true)
  const now = useNow()

  if (error) {
    return <p className="m-0 text-sm text-[var(--color-text-secondary)]" data-similar-failures="error">{SIMILAR_LOAD_ERROR}</p>
  }
  if (isLoading || !data) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]" data-similar-failures="loading">
        <LoadingSpinner size="sm" />
        Finding similar failures…
      </div>
    )
  }
  const items = data.items ?? []
  if (items.length === 0) {
    return (
      <p className="m-0 text-sm text-[var(--color-text-secondary)]" data-similar-failures={data.semantic_status ?? 'ok'}>
        {emptyStateText(data.semantic_status)}
      </p>
    )
  }
  return (
    <section aria-label="Similar failures" className="card !p-0 overflow-hidden" data-similar-failures="list">
      <ul className="m-0 list-none p-0">
        {items.map((item) => {
          const last = item.last_run_date ? Date.parse(item.last_run_date) : Number.NaN
          const relevance = typeof item.relevance_score === 'number' && Number.isFinite(item.relevance_score)
            ? item.relevance_score.toFixed(2)
            : '—'
          return (
            <li
              key={item.test_case_id}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-[var(--color-border)] px-4 py-2.5 last:border-b-0"
            >
              <div className="min-w-0">
                <Link
                  to={`/runs/${encodeURIComponent(item.test_run_id)}/tests/${encodeURIComponent(item.test_case_id)}`}
                  className="block truncate font-medium text-[var(--color-text)] hover:text-[var(--color-accent)] hover:underline"
                  title={item.test_name}
                >
                  {item.test_name}
                </Link>
                <div className="truncate text-xs text-[var(--color-text-muted)]">
                  {item.suite_name || 'No suite'} · {item.status} · rel {relevance} · {Number.isFinite(last) ? relativeTime(last, now) : '—'}
                </div>
              </div>
              <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 text-[var(--color-text-faint)]" />
            </li>
          )
        })}
      </ul>
    </section>
  )
}
