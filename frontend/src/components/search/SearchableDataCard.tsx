/**
 * "Searchable data" (HON-08), moved out of `SearchPage.tsx` (W12 SRCH-01), plus
 * **Re-index now** on its semantic line.
 *
 * It was "Index health": ONE vector-index status, plus a 5-minute staleness
 * rule, copied onto all six entity rows, so an install without ChromaDB (or an
 * idle one) showed Tests, Runs, Suites, Defects, Flaky and Releases all
 * "Down". Five of them have no index at all: global search is a SQL keyword
 * match over each table (global_search_service) and answers whenever the API
 * does. Each row says that, with its row count; the ChromaDB collection, which
 * holds test cases only, gets a line of its own, and an index that has not
 * been written to lately is idle, not down. Its test ids, aria-labels and
 * sentences are the ones `search-rail.spec.ts` reads.
 *
 * Re-index now: a QA lead or an admin with ONE project pinned (never in All
 * Projects; never a QA engineer, tester or viewer) queues
 * `POST /search/reindex?project_id=&full=true`; the line then polls the index
 * status every 10 s until it was written after the queue time (or 10 minutes
 * pass). A refusal is the server's sentence, under the line.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import {
  AlertTriangle, Database, Layers, Package, ShieldCheck, TestTube, Zap,
} from 'lucide-react'
import { usePermissions } from '@/hooks/usePermissions'
import { useNow } from '@/hooks/useNow'
import { reindexProgress, useSearchIndexStatus } from '@/hooks/useSearchIndexStatus'
import { searchService } from '@/services/searchService'
import { formatNumber } from '@/utils/formatters'
import type { IndexStatus, SearchEntityType } from '@/types/search'
import { SearchCardShell } from './RecentSearchesCard'
import { errorSentence, relativeTime } from './searchModel'

interface SearchableEntity {
  entity: SearchEntityType
  label: string
  Icon: typeof TestTube
  iconBg: string
  iconFg: string
}

const tint = (token: string) => `color-mix(in srgb, var(${token}) 14%, transparent)`

const SEARCHABLE_ENTITIES: SearchableEntity[] = [
  { entity: 'test_case', label: 'Tests', Icon: TestTube, iconBg: tint('--color-accent'), iconFg: 'var(--color-accent)' },
  { entity: 'test_run', label: 'Runs', Icon: Zap, iconBg: tint('--ui-success'), iconFg: 'var(--ui-success)' },
  { entity: 'suite', label: 'Suites', Icon: Layers, iconBg: tint('--status-flaky'), iconFg: 'var(--status-flaky)' },
  { entity: 'defect', label: 'Defects', Icon: ShieldCheck, iconBg: 'var(--color-cyan-soft)', iconFg: 'var(--color-cyan)' },
  { entity: 'flaky_test', label: 'Flaky', Icon: AlertTriangle, iconBg: tint('--ui-warning'), iconFg: 'var(--ui-warning)' },
  { entity: 'release', label: 'Releases', Icon: Package, iconBg: 'var(--color-bg-secondary)', iconFg: 'var(--color-text-muted)' },
]

export interface SemanticIndexLine {
  /** Short state for the card header and the pill. */
  label: 'Ready' | 'Not configured' | 'Unavailable' | 'Unknown' | 'Checking'
  detail: string
  tone: 'passed' | 'failed' | 'neutral'
}

export const REINDEX_QUEUED_TOAST = 'Re-index queued.'
export const REINDEX_FAILED = 'Could not queue the re-index.'

function describeSemanticIndex(indexStatus: IndexStatus | null, now: number): SemanticIndexLine {
  if (!indexStatus) return { label: 'Checking', detail: 'Reading the semantic index status…', tone: 'neutral' }
  const status = indexStatus.status
  if (status === 'healthy') {
    const docs = indexStatus.document_count
    const when = indexStatus.last_indexed_at
      ? `last indexed ${relativeTime(new Date(indexStatus.last_indexed_at).getTime(), now)}`
      : 'no indexing time recorded'
    return { label: 'Ready', detail: `${formatNumber(docs)} doc${docs === 1 ? '' : 's'} · ${when}`, tone: 'passed' }
  }
  if (status === 'not_configured') {
    return {
      label: 'Not configured',
      detail: 'No ChromaDB server answers here. Semantic test search falls back to keyword.',
      tone: 'neutral',
    }
  }
  if (status === 'unavailable') {
    return {
      label: 'Unavailable',
      detail: 'ChromaDB answered with an error. Semantic test search falls back to keyword until it recovers.',
      tone: 'failed',
    }
  }
  return { label: 'Unknown', detail: 'The semantic index status could not be read.', tone: 'neutral' }
}

const PILL_TONES: Record<SemanticIndexLine['tone'], string> = {
  passed: 'border-[var(--ui-success-bd)] bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)]',
  failed: 'border-[var(--ui-danger-bd)] bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)]',
  neutral: 'border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}

function StatusPill({ label, tone, ariaLabel }: { label: string; tone: SemanticIndexLine['tone']; ariaLabel: string }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full border px-1.5 py-0.5 text-2xs font-semibold ${PILL_TONES[tone]}`}
      aria-label={ariaLabel}
    >
      {label}
    </span>
  )
}

/** "14:05 UTC" */
function utcClock(ms: number): string {
  return `${new Date(ms).toISOString().slice(11, 16)} UTC`
}

export default function SearchableDataCard({
  totals, indexStatus, projectId,
}: {
  /** Project-scoped row counts from /search/entity-counts; null until (or unless) they load. */
  totals: Record<SearchEntityType, number> | null
  indexStatus: IndexStatus | null
  /** The pinned project; null in All Projects (no Re-index button there). */
  projectId: string | null
}) {
  const { isQaLead } = usePermissions()
  const [queuedAt, setQueuedAt] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  // The poll: this card's own subscription to the page's index-status entry,
  // with a 10 s interval only while the re-index it queued is pending.
  const { indexStatus: polled } = useSearchIndexStatus(projectId, { queuedAt })
  const status = polled ?? indexStatus
  const now = useNow(queuedAt !== null ? 10_000 : undefined)
  const semantic = describeSemanticIndex(status, now)
  const progress = reindexProgress(status, queuedAt, now)
  const canReindex = isQaLead && projectId !== null

  const reindex = async () => {
    if (!projectId) return
    setBusy(true)
    setRefusal(null)
    try {
      await searchService.triggerReindex(projectId, true)
      setQueuedAt(Date.now())
      toast.success(REINDEX_QUEUED_TOAST)
    } catch (error) {
      setRefusal(errorSentence(error, REINDEX_FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SearchCardShell
      title={
        <span className="inline-flex items-center gap-2">
          <Database className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          Searchable data
        </span>
      }
      // Under the title, not beside it: at ~360 px (1280, DejaVu) the two side
      // by side each wrapped to two lines.
      subtitle={
        <span data-testid="searchable-data-summary">
          {SEARCHABLE_ENTITIES.length} entity types · semantic: {semantic.label.toLowerCase()}
        </span>
      }
    >
      <div className="flex flex-col gap-2 px-4 py-3">
        {SEARCHABLE_ENTITIES.map((r) => {
          const Icon = r.Icon
          const count = totals ? Number(totals[r.entity] ?? 0) : null
          return (
            <div
              key={r.entity}
              className="grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2"
            >
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full" style={{ background: r.iconBg, color: r.iconFg }}>
                <Icon className="h-3 w-3" />
              </span>
              <div className="min-w-0">
                <div className="text-sm font-medium text-[var(--color-text)]">{r.label}</div>
                <div className="truncate text-2xs text-[var(--color-text-muted)]">
                  {count === null ? '— rows' : `${formatNumber(count)} row${count === 1 ? '' : 's'}`} · keyword match
                </div>
              </div>
              <StatusPill label="Keyword" tone="passed" ariaLabel={`${r.label}: keyword search`} />
            </div>
          )
        })}
        <div
          className="mt-1 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2"
          data-testid="semantic-index-line"
        >
          <div className="min-w-0">
            <div className="text-sm font-medium text-[var(--color-text)]">Semantic index (tests)</div>
            <div className="text-2xs text-[var(--color-text-muted)]">{semantic.detail}</div>
            {queuedAt !== null && progress === 'pending' && (
              <div className="mt-0.5 text-2xs text-[var(--color-text-secondary)]" data-reindex-queued="">
                Re-index queued at {utcClock(queuedAt)} — this line updates when it finishes.
              </div>
            )}
            {queuedAt !== null && progress === 'timeout' && (
              <div className="mt-0.5 text-2xs text-[var(--color-text-secondary)]" data-reindex-queued="">
                Re-index queued at {utcClock(queuedAt)} has not finished yet.
              </div>
            )}
            {refusal && (
              <p role="alert" className="m-0 mt-0.5 text-xs text-[var(--ui-danger-ink)]">{refusal}</p>
            )}
          </div>
          {/* Stacked (pill over button): side by side they left the detail
              column ~110 px at 1280 on CI's DejaVu; stacked it keeps ~190. */}
          <div className="flex flex-col items-end gap-1.5">
            <StatusPill label={semantic.label} tone={semantic.tone} ariaLabel={`Semantic index: ${semantic.label}`} />
            {canReindex && (
              <button
                type="button"
                className="btn-secondary btn-sm whitespace-nowrap"
                disabled={busy || progress === 'pending'}
                onClick={() => void reindex()}
              >
                {progress === 'pending' ? 'Queued' : 'Re-index now'}
              </button>
            )}
          </div>
        </div>
      </div>
    </SearchCardShell>
  )
}
