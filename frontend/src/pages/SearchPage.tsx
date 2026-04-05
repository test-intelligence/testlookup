import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import StatusBadge from '@/components/ui/StatusBadge'
import Pagination from '@/components/ui/Pagination'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { searchService } from '@/services/searchService'
import type { SearchType } from '@/services/searchService'
import type { SearchResponse } from '@/types/search'
import { fromNow } from '@/utils/formatters'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildSearchWorkflow } from '@/components/workflow/workflowPresets'

const SEARCH_MODES: { value: SearchType; label: string }[] = [
  { value: 'keyword', label: 'Keyword' },
  { value: 'semantic', label: 'Semantic' },
  { value: 'hybrid', label: 'Hybrid' },
]

export default function SearchPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const projectId = useProjectStore(s => s.activeProjectId)
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [searchType, setSearchType] = useState<SearchType>(
    (searchParams.get('search_type') as SearchType) || 'keyword',
  )
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  const doSearch = async (q: string, p = 1) => {
    if (!q.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await searchService.search({
        q,
        project_id: (projectId && projectId !== ALL_PROJECTS_ID) ? projectId : undefined,
        search_type: searchType,
        page: p,
        size: 25,
      })
      setResults(data)
      setPage(p)
    } catch {
      setError('Search failed. Please try again or use a different search mode.')
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  const workflow = useMemo(
    () => buildSearchWorkflow(query, searchType, results),
    [query, searchType, results],
  )

  // Re-run search whenever the URL ?q= param changes (e.g. navigating from TopBar)
  useEffect(() => {
    const q = searchParams.get('q') ?? ''
    const st = searchParams.get('search_type') as SearchType | null
    if (st && SEARCH_MODES.some(m => m.value === st)) {
      setSearchType(st)
    }
    if (q) {
      setQuery(q)
      doSearch(q)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get('q'), searchParams.get('search_type')])

  return (
    <div className="space-y-4">
      <PageHeader title="Search" subtitle="Full-text search across all test cases and history" />

      <WorkflowTimeline
        title="Search workflow"
        subtitle="Capture the query, choose retrieval mode, rank matches, and drill into the best result"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      {/* Search mode toggle */}
      <div className="flex items-center gap-1.5">
        {SEARCH_MODES.map((m) => (
          <button
            key={m.value}
            onClick={() => setSearchType(m.value)}
            className={clsx(
              'px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors',
              searchType === m.value
                ? 'bg-white/10 text-[var(--color-text-secondary)] border-[var(--color-border-light)]'
                : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border-[var(--color-border)] hover:text-[var(--color-text)]',
            )}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Search bar */}
      <div className="flex gap-3">
        <div className="relative flex-1 max-w-2xl">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-text-muted)]" />
          <input
            type="text"
            className="input pl-9 h-11 text-base"
            placeholder="Search test names, error messages, suites…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch(query)}
            autoFocus
          />
        </div>
        <button className="btn-primary px-6" onClick={() => doSearch(query)} disabled={loading}>
          {loading ? <LoadingSpinner size="sm" /> : 'Search'}
        </button>
      </div>

      {/* Results */}
      {results && results.total === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)]">
          <Search className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
          <p className="font-medium">No results found</p>
          <p className="text-sm mt-1">
            Try a different search term{searchType !== 'hybrid' ? ' or switch to Hybrid mode' : ''}.
          </p>
        </div>
      )}

      {results && results.total > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-[var(--color-text-muted)]">
            {results.total} results for <span className="text-[var(--color-text)] font-medium">"{results.query}"</span>
            <span className="text-[var(--color-text-faint)] ml-1">({results.search_type} mode)</span>
          </p>
          <div className="card p-0 overflow-hidden">
            <table className="w-full">
              <thead className="border-b border-[var(--color-border)]">
                <tr>
                  {['Test Name', 'Suite', 'Status', 'Failures', 'Relevance', 'Last Run'].map(h => (
                    <th key={h} className="th">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.items.map((r) => (
                  <tr
                    key={r.test_case_id}
                    className="table-row"
                    onClick={() => navigate(`/runs/${r.test_run_id}/tests/${r.test_case_id}`)}
                  >
                    <td className="td">
                      <p className="text-[var(--color-text)] text-sm font-medium truncate max-w-[260px]">{r.test_name}</p>
                      {/* Match reasons badges */}
                      {r.match_reasons && r.match_reasons.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {r.match_reasons.map((reason, i) => (
                            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">
                              {reason}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="td text-[var(--color-text-muted)] text-sm truncate max-w-[140px]">{r.suite_name ?? '\u2014'}</td>
                    <td className="td"><StatusBadge status={r.status} /></td>
                    <td className="td text-red-400 font-medium">{r.failure_count}</td>
                    <td className="td">
                      {r.relevance_score != null ? (
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
                            <div
                              className="h-full bg-neutral-300 rounded-full"
                              style={{ width: `${Math.round(r.relevance_score * 100)}%` }}
                            />
                          </div>
                          <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
                            {Math.round(r.relevance_score * 100)}%
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-[var(--color-text-faint)]">\u2014</span>
                      )}
                    </td>
                    <td className="td text-[var(--color-text-muted)] text-sm">{fromNow(r.last_run_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination page={page} pages={results.pages} total={results.total}
              onChange={p => doSearch(query, p)} />
          </div>
        </div>
      )}

      {error && (
        <div className="card border border-red-700/30 bg-red-900/10 py-3">
          <p className="text-sm text-red-400">{error}</p>
        </div>
      )}

      {!results && !loading && !error && (
        <div className="flex flex-col items-center justify-center py-20 text-[var(--color-text-muted)]">
          <Search className="h-12 w-12 mb-3 text-[var(--color-text-faint)]" />
          <p>Enter a search term and press Enter</p>
        </div>
      )}
    </div>
  )
}
