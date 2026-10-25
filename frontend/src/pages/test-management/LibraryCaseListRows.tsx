/**
 * W13 TCL-03 — the cases behind a Strategy-gaps row ("Flaky and active",
 * "No owner"), opened IN the row.
 *
 * **Show N** lists the health answer's first five (no request: they rode the
 * page's one health read). **Show all {total}** pages every match through
 * `GET /cases/evidence-gaps?kind=&page=&size=25` (`useEvidenceGapPage`, which
 * asks only once that list is opened), with Previous / Next. A list the
 * server could not measure reads its reason — never a 0. A title opens its
 * case (`?case=`). Titles, owners and details are React TEXT.
 */
import { useState } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useEvidenceGapPage } from '@/hooks/useTestLibrary'
import type { TestCaseEvidenceGapItem } from '@/types/test-management'
import type { LibraryCaseList } from '@/types/test-library'
import { formatNumber } from '@/utils/formatters'

export interface LibraryCaseListRowsProps {
  projectId: string
  kind: 'flaky_active' | 'no_owner'
  list: LibraryCaseList
  onOpenCase: (caseId: string) => void
}

const PAGE_SIZE = 25
const LINK_BUTTON = 'text-xs text-[var(--color-accent-ink)] hover:underline disabled:opacity-50 disabled:no-underline'
const TITLE_BUTTON = 'min-w-0 text-left text-sm text-[var(--color-accent-ink)] hover:underline [overflow-wrap:anywhere]'

/** A page row's detail, worded as the health answer words its preview rows. */
function pageDetail(kind: LibraryCaseListRowsProps['kind'], item: TestCaseEvidenceGapItem): string | null {
  if (kind === 'flaky_active') {
    if (item.flaky_score == null) return null
    const score = formatNumber(item.flaky_score, { maximumFractionDigits: 2 })
    return item.flaky_confidence ? `flaky score ${score} (${item.flaky_confidence} confidence)` : `flaky score ${score}`
  }
  if (item.owner_state === 'unassigned') return 'unassigned'
  if (item.owner_state === 'deactivated') return 'owner deactivated'
  return null
}

function Row({ id, title, detail, owner, onOpenCase }: { id: string; title: string; detail?: string | null; owner?: string | null; onOpenCase: (caseId: string) => void }) {
  return (
    <li data-library-case={id} className="flex flex-col">
      <button type="button" className={TITLE_BUTTON} onClick={() => onOpenCase(id)}>
        {title}
      </button>
      {(owner || detail) && (
        <span className="text-xs text-[var(--color-text-muted)] [overflow-wrap:anywhere]">
          {[owner, detail].filter(Boolean).join(' · ')}
        </span>
      )}
    </li>
  )
}

export default function LibraryCaseListRows({ projectId, kind, list, onOpenCase }: LibraryCaseListRowsProps) {
  const [open, setOpen] = useState<'none' | 'preview' | 'all'>('none')
  const [page, setPage] = useState(1)
  const showAll = open === 'all'
  const { data, error, isLoading, mutate } = useEvidenceGapPage(projectId, kind, page, showAll)

  if (list.measured === false) {
    return <p data-library-list-unmeasured={kind} className="m-0 text-xs text-[var(--color-text-muted)]">{list.reason}</p>
  }
  if (list.total === 0) return null

  const lastPage = Math.max(1, Math.ceil((data?.total ?? list.total) / PAGE_SIZE))
  const from = (page - 1) * PAGE_SIZE + 1
  const to = from + (data?.items.length ?? 0) - 1

  return (
    <div data-library-case-list={kind} className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-3">
        {list.items.length > 0 && (
          <button
            type="button"
            className={LINK_BUTTON}
            aria-expanded={open === 'preview'}
            onClick={() => setOpen(open === 'preview' ? 'none' : 'preview')}
          >
            {open === 'preview' ? 'Hide' : `Show ${formatNumber(list.items.length)}`}
          </button>
        )}
        {list.total > list.items.length && (
          <button
            type="button"
            className={LINK_BUTTON}
            aria-expanded={showAll}
            onClick={() => {
              setPage(1)
              setOpen(showAll ? 'none' : 'all')
            }}
          >
            {showAll ? 'Hide all' : `Show all ${formatNumber(list.total)}`}
          </button>
        )}
      </div>
      {open === 'preview' && (
        <ul className="m-0 list-none space-y-1 p-0">
          {list.items.map((item) => (
            <Row key={item.id} id={item.id} title={item.title} owner={item.owner} detail={item.detail} onOpenCase={onOpenCase} />
          ))}
        </ul>
      )}
      {showAll && (
        isLoading && !data ? (
          <div role="status" className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
            <LoadingSpinner size="sm" />
            Loading the cases…
          </div>
        ) : error && !data ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-[var(--ui-danger-ink)]">
            <span>Could not load the cases.</span>
            <button type="button" className="btn-secondary btn-sm" onClick={() => void mutate()}>Retry</button>
          </div>
        ) : data ? (
          <>
            <ul className="m-0 list-none space-y-1 p-0" data-library-case-page={page}>
              {data.items.map((item) => (
                <Row key={item.id} id={item.id} title={item.title} detail={pageDetail(kind, item)} onOpenCase={onOpenCase} />
              ))}
            </ul>
            <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
              <span data-library-case-range="">{data.items.length ? `${formatNumber(from)}-${formatNumber(to)}` : '0'} of {formatNumber(data.total)}</span>
              <button type="button" className={LINK_BUTTON} disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
              <button type="button" className={LINK_BUTTON} disabled={page >= lastPage} onClick={() => setPage(page + 1)}>Next</button>
            </div>
          </>
        ) : null
      )}
    </div>
  )
}
