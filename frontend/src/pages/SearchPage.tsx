import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Search, TestTube, GitBranch, Layers, Bug, AlertTriangle,
  Package, ChevronRight,
} from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/ui/PageHeader'
import StatusBadge from '@/components/ui/StatusBadge'
import Pagination from '@/components/ui/Pagination'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { searchService } from '@/services/searchService'
import type { SearchType } from '@/services/searchService'
import type { SearchResponse, GlobalSearchResponse, SearchEntityType } from '@/types/search'
import { fromNow } from '@/utils/formatters'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline'
import { buildSearchWorkflow } from '@/components/workflow/workflowPresets'

const SEARCH_MODES: { value: SearchType | 'global'; label: string }[] = [
  { value: 'global', label: 'Global' },
  { value: 'keyword', label: 'Test Cases' },
  { value: 'semantic', label: 'Semantic' },
  { value: 'hybrid', label: 'Hybrid' },
]

const ENTITY_FILTERS: { value: SearchEntityType | 'all'; label: string; icon: React.ReactNode }[] = [
  { value: 'all', label: 'All', icon: <Search className="h-3 w-3" /> },
  { value: 'test_case', label: 'Test Cases', icon: <TestTube className="h-3 w-3" /> },
  { value: 'test_run', label: 'Runs', icon: <GitBranch className="h-3 w-3" /> },
  { value: 'suite', label: 'Suites', icon: <Layers className="h-3 w-3" /> },
  { value: 'defect', label: 'Defects', icon: <Bug className="h-3 w-3" /> },
  { value: 'flaky_test', label: 'Flaky', icon: <AlertTriangle className="h-3 w-3" /> },
  { value: 'release', label: 'Releases', icon: <Package className="h-3 w-3" /> },
]

const ENTITY_BADGE_COLORS: Record<string, string> = {
  test_case: 'bg-blue-900/40 text-blue-400 border-blue-700/40',
  test_run: 'bg-emerald-900/40 text-emerald-400 border-emerald-700/40',
  suite: 'bg-violet-900/40 text-violet-400 border-violet-700/40',
  defect: 'bg-red-900/40 text-red-400 border-red-700/40',
  flaky_test: 'bg-amber-900/40 text-amber-400 border-amber-700/40',
  release: 'bg-cyan-900/40 text-cyan-400 border-cyan-700/40',
}

export default function SearchPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const projectId = useProjectStore(s => s.activeProjectId)
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [searchType, setSearchType] = useState<SearchType | 'global'>('global')
  const [entityFilter, setEntityFilter] = useState<SearchEntityType | 'all'>('all')
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [globalResults, setGlobalResults] = useState<GlobalSearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  const isGlobal = searchType === 'global'

  const doSearch = async (q: string, p = 1) => {
    if (!q.trim()) return
    setLoading(true)
    setError(null)
    try {
      const pid = (projectId && projectId !== ALL_PROJECTS_ID) ? projectId : undefined

      if (isGlobal) {
        const data = await searchService.globalSearch({
          q,
          project_id: pid,
          entity_types: entityFilter !== 'all' ? [entityFilter] : undefined,
          page: p,
          size: 25,
        })
        setGlobalResults(data)
        setResults(null)
      } else {
        const data = await searchService.search({
          q,
          project_id: pid,
          search_type: searchType as SearchType,
          page: p,
          size: 25,
        })
        setResults(data)
        setGlobalResults(null)
      }
      setPage(p)
    } catch {
      setError('Search failed. Please try again or use a different search mode.')
      setResults(null)
      setGlobalResults(null)
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
      <PageHeader title="Search" subtitle={isGlobal ? 'Search across tests, runs, suites, defects, and more' : 'Full-text search across all test cases and history'} />

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

      {/* Entity type filters (global mode only) */}
      {isGlobal && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {ENTITY_FILTERS.map(f => (
            <button
              key={f.value}
              onClick={() => { setEntityFilter(f.value); if (query.trim()) doSearch(query) }}
              className={clsx(
                'flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-lg border transition-colors',
                entityFilter === f.value
                  ? 'bg-white/10 text-[var(--color-text-secondary)] border-[var(--color-border-light)]'
                  : 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] border-[var(--color-border)] hover:text-[var(--color-text)]',
              )}
            >
              {f.icon}
              {f.label}
              {globalResults?.entity_counts && f.value !== 'all' && globalResults.entity_counts[f.value] != null && (
                <span className="text-[10px] bg-white/10 px-1 rounded">{globalResults.entity_counts[f.value]}</span>
              )}
            </button>
          ))}
        </div>
      )}

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

      {/* Global Results */}
      {isGlobal && globalResults && globalResults.total === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)]">
          <Search className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
          <p className="font-medium">No results found</p>
          <p className="text-sm mt-1">Try a different search term or broaden entity filters.</p>
        </div>
      )}

      {isGlobal && globalResults && globalResults.total > 0 && (
        <div className="space-y-2">
          <p className="text-sm text-[var(--color-text-muted)]">
            {globalResults.total} results for <span className="text-[var(--color-text)] font-medium">&quot;{globalResults.query}&quot;</span>
          </p>
          <div className="space-y-2">
            {globalResults.items.map((r) => (
              <div
                key={`${r.entity_type}-${r.entity_id}`}
                className="card p-3 cursor-pointer hover:bg-[var(--color-bg-hover)]/50 transition-colors flex items-start gap-3"
                onClick={() => navigate(r.navigation_url)}
              >
                {/* Entity badge */}
                <span className={clsx('shrink-0 px-2 py-0.5 rounded text-[10px] font-medium border mt-0.5', ENTITY_BADGE_COLORS[r.entity_type] ?? 'bg-slate-800 text-slate-400 border-slate-700')}>
                  {r.entity_type.replace('_', ' ')}
                </span>
                {/* Content */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-[var(--color-text)] truncate">{r.title}</p>
                  {r.subtitle && <p className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">{r.subtitle}</p>}
                  {r.match_reasons.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {r.match_reasons.map((reason, i) => (
                        <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">
                          {reason}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                {/* Relevance + nav arrow */}
                <div className="flex items-center gap-2 shrink-0">
                  {r.relevance_score > 0 && (
                    <div className="flex items-center gap-1.5">
                      <div className="w-12 h-1.5 bg-[var(--color-bg-secondary)] rounded-full overflow-hidden">
                        <div className="h-full bg-neutral-300 rounded-full" style={{ width: `${Math.round(r.relevance_score * 100)}%` }} />
                      </div>
                      <span className="text-[10px] text-[var(--color-text-faint)] tabular-nums w-7">{Math.round(r.relevance_score * 100)}%</span>
                    </div>
                  )}
                  <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-faint)]" />
                </div>
              </div>
            ))}
          </div>
          <Pagination page={page} pages={globalResults.pages} total={globalResults.total}
            onChange={p => doSearch(query, p)} />
        </div>
      )}

      {/* Test Case Results (legacy modes) */}
      {!isGlobal && results && results.total === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-[var(--color-text-muted)]">
          <Search className="h-10 w-10 mb-3 text-[var(--color-text-faint)]" />
          <p className="font-medium">No results found</p>
          <p className="text-sm mt-1">
            Try a different search term{searchType !== 'hybrid' ? ' or switch to Hybrid mode' : ''}.
          </p>
        </div>
      )}

      {!isGlobal && results && results.total > 0 && (
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
