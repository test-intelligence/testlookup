/**
 * W08 TRND-07 "Fix tagging": the tests behind the Untagged-executions callout
 * — the distinct tests whose executions in the window carry no suite label —
 * in a 400 px side panel (fixed, right: it adds no page height), 50 a page.
 *
 * Moving files tests under another suite IN THE CATALOGUE (the existing
 * `POST /canonical-test-cases/bulk-link`). It never relabels an execution:
 * an execution's suite is only what the reporter sent, past and future. The
 * panel says so, always, and links the reporter docs; the untagged count only
 * drops once the reporter sends a label (D-9).
 *
 *   - QA_ENGINEER and above move; anyone else reads the list (no checkboxes,
 *     no move controls).
 *   - QA_LEAD and above may also make the target the project's default suite
 *     for new unlabelled tests (`POST /suites/{id}/set-default`).
 *   - A test with no catalogue row (`canonical_id: null`) is listed, not
 *     selectable.
 *
 * Every name (test, class, suite) is user / CI text and renders as React text.
 * A malformed page of the list degrades to "not available" (the guard), never
 * a crash.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSWRConfig } from 'swr'
import toast from 'react-hot-toast'
import { X } from 'lucide-react'
import { usePermissions } from '@/hooks/usePermissions'
import { useSuites } from '@/hooks/useSuites'
import { UNTAGGED_TESTS_KEY, useUntaggedTests } from '@/hooks/useCoverageHistory'
import { UNTAGGED_PAGE_SIZE } from '@/services/coverageHistoryService'
import { extractErrorMessage } from '@/services/apiErrors'
import { suitesService } from '@/services/suitesService'
import type { UntaggedTest } from '@/types/analytics'
import type { TestSuite } from '@/types/suites'

export const FIX_TAGGING_COPY =
  'Moving a test files it under another suite in the catalogue (Test Management, Suites, the coverage map). ' +
  'Its executions — past and future — keep the suite label your test reporter sends; to label future runs, set the suite in the reporter.'
export const REPORTER_DOCS_HREF = '/docs/ingestion#key-behaviours'
export const NOT_IN_CATALOGUE_TITLE = 'Not in the catalogue yet: it is added when its run is finalised'

const SELECT =
  'w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-xs text-[var(--color-text)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const BUTTON =
  'inline-flex min-h-7 items-center justify-center rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-60'

export interface FixTaggingPanelProps {
  projectId: string
  days: number
  onClose: () => void
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

function lastSeen(iso: string | null): string {
  if (!iso) return '—'
  const at = Date.parse(iso)
  return Number.isFinite(at) ? new Date(at).toLocaleString() : '—'
}

function suitesOf(raw: unknown): TestSuite[] {
  const items = raw && typeof raw === 'object' ? (raw as { items?: unknown }).items : undefined
  if (!Array.isArray(items)) return []
  return items.filter(
    (s): s is TestSuite => Boolean(s) && typeof (s as TestSuite).id === 'string' && typeof (s as TestSuite).name === 'string',
  )
}

/** One page of the list (a component per page keeps one hook per page; pages are cached by SWR). */
function UntaggedPage({
  projectId,
  days,
  page,
  canMove,
  selected,
  onToggle,
}: {
  projectId: string
  days: number
  page: number
  canMove: boolean
  selected: ReadonlySet<string>
  onToggle: (test: UntaggedTest) => void
}) {
  const { list, malformed, isLoading } = useUntaggedTests(projectId, days, page, true)
  if (malformed) {
    return <li className="py-2 text-xs text-[var(--color-text-muted)]">This page of untagged tests is not available.</li>
  }
  if (!list) {
    return isLoading ? <li className="py-2 text-xs text-[var(--color-text-muted)]">Loading…</li> : null
  }
  return (
    <>
      {list.items.map((test) => {
        const movable = test.canonical_id !== null
        const checked = movable && selected.has(test.canonical_id as string)
        return (
          <li
            key={test.test_fingerprint}
            data-untagged-test={test.test_fingerprint}
            className="flex items-start gap-2 border-b border-dashed border-[var(--color-border)] py-2"
            title={movable ? undefined : NOT_IN_CATALOGUE_TITLE}
          >
            {canMove && (
              <input
                type="checkbox"
                aria-label={`Select ${test.test_name}`}
                className="mt-0.5"
                checked={checked}
                disabled={!movable}
                title={movable ? undefined : NOT_IN_CATALOGUE_TITLE}
                onChange={() => onToggle(test)}
              />
            )}
            <div className="min-w-0 flex-1">
              <div className="truncate font-mono text-[11.5px] text-[var(--color-text)]" title={test.test_name}>
                {test.test_name}
              </div>
              {test.class_name && (
                <div className="truncate text-[11px] text-[var(--color-text-muted)]" title={test.class_name}>
                  {test.class_name}
                </div>
              )}
              <div className="mt-0.5 text-[11px] text-[var(--color-text-secondary)]">
                {plural(test.executions, 'execution')} · last {lastSeen(test.last_executed_at)} · catalogue:{' '}
                {movable ? (test.catalogue_suite ?? '—') : 'not in the catalogue yet'}
              </div>
            </div>
          </li>
        )
      })}
    </>
  )
}

export default function FixTaggingPanel({ projectId, days, onClose }: FixTaggingPanelProps) {
  const { isQaEngineer, isQaLead } = usePermissions()
  const { mutate } = useSWRConfig()
  // The first page's answer gives the total (the same SWR key as page 1's list: one request).
  const first = useUntaggedTests(projectId, days, 1, true)
  const { data: suitesData } = useSuites()
  const suites = useMemo(() => suitesOf(suitesData), [suitesData])

  const [pages, setPages] = useState(1)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [targetId, setTargetId] = useState('')
  const [makeDefault, setMakeDefault] = useState(false)
  const [saving, setSaving] = useState(false)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const target = suites.find((s) => s.id === targetId) ?? null
  const total = first.list?.total ?? 0
  const shown = Math.min(total, pages * UNTAGGED_PAGE_SIZE)
  const canMove = isQaEngineer

  const toggle = (test: UntaggedTest) => {
    if (!test.canonical_id) return
    const id = test.canonical_id
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const move = async () => {
    if (!target || selected.size === 0) return
    setSaving(true)
    setFailure(null)
    setOutcome(null)
    try {
      const result = await suitesService.bulkLinkCanonicals(target.id, [...selected])
      const moved = Number.isFinite(result?.moved) ? result.moved : 0
      const missing = Array.isArray(result?.missing_ids) ? result.missing_ids.length : 0
      const parts = [`Moved ${plural(moved, 'test')} to ${target.name}`]
      if (missing > 0) parts.push(`${missing} ${missing === 1 ? 'was' : 'were'} no longer in the catalogue`)
      if (makeDefault && isQaLead) {
        try {
          await suitesService.setDefault(target.id)
          parts.push(`${target.name} is now the default suite for new unlabelled tests`)
        } catch (cause: unknown) {
          const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
          parts.push(extractErrorMessage(detail, 'the default suite was not changed'))
        }
      }
      const message = parts.join(' · ')
      setOutcome(message)
      toast.success(message)
      setSelected(new Set())
      setPages(1)
      // The list, the coverage read and every suite view revalidate.
      await mutate(
        (key) =>
          Array.isArray(key) &&
          (key[0] === UNTAGGED_TESTS_KEY || key[0] === 'analytics-coverage' || key[0] === 'suites' || key[0] === 'suite'),
      )
    } catch (cause: unknown) {
      const detail = (cause as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      setFailure(extractErrorMessage(detail, 'The tests were not moved.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <aside
      aria-label="Fix tagging"
      data-fix-tagging-panel=""
      className="fixed right-0 top-16 z-40 flex h-[calc(100vh-4rem)] w-[400px] flex-col gap-3 overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 shadow-xl"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-[var(--color-text)]">Fix tagging</h2>
          <p className="text-xs text-[var(--color-text-secondary)]">
            {first.list ? `${plural(total, 'test')} ran without a suite label in the last ${days} days` : `Last ${days} days`}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close the fix tagging panel"
          className="inline-flex h-8 w-8 items-center justify-center rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <p data-fix-tagging-copy="" className="m-0 text-xs text-[var(--color-text-secondary)]" style={{ lineHeight: 1.5 }}>
        {FIX_TAGGING_COPY}{' '}
        <Link to={REPORTER_DOCS_HREF} className="hover:underline" style={{ color: 'var(--color-accent)' }}>
          How the reporter labels a run
        </Link>
      </p>

      {canMove && (
        <div className="flex flex-col gap-2 rounded border border-[var(--color-border)] p-2.5">
          <label className="text-xs text-[var(--color-text-secondary)]" htmlFor="fix-tagging-target">
            Target suite
          </label>
          <select
            id="fix-tagging-target"
            data-fix-tagging-target=""
            className={SELECT}
            value={targetId}
            onChange={(event) => setTargetId(event.target.value)}
          >
            <option value="">Choose a suite…</option>
            {suites.map((suite) => (
              <option key={suite.id} value={suite.id}>
                {suite.name}
              </option>
            ))}
          </select>
          {isQaLead && target && (
            <label className="flex items-center gap-2 text-xs text-[var(--color-text)]">
              <input type="checkbox" checked={makeDefault} onChange={(event) => setMakeDefault(event.target.checked)} />
              Also make {target.name} the default suite for new unlabelled tests
            </label>
          )}
          <button
            type="button"
            data-fix-tagging-move=""
            className={BUTTON}
            disabled={saving || !target || selected.size === 0}
            onClick={() => void move()}
          >
            {saving ? 'Moving…' : target ? `Move ${plural(selected.size, 'test')} to ${target.name}` : `Move ${plural(selected.size, 'test')}`}
          </button>
          {outcome && (
            <p role="status" data-fix-tagging-outcome="" className="m-0 text-xs text-[var(--color-text)]">
              {outcome}
            </p>
          )}
          {failure && (
            <p role="alert" data-fix-tagging-error="" className="m-0 text-xs text-[var(--status-failed)]">
              {failure}
            </p>
          )}
        </div>
      )}

      {first.malformed ? (
        <p data-fix-tagging-unavailable="" className="m-0 text-xs text-[var(--color-text-muted)]">
          The untagged tests are not available.
        </p>
      ) : first.list && first.list.total === 0 && first.list.items.length === 0 ? (
        <p className="m-0 text-xs text-[var(--color-text-muted)]">No untagged tests in this window.</p>
      ) : (
        <>
          <ul data-fix-tagging-list="" className="m-0 list-none p-0">
            {Array.from({ length: pages }, (_, i) => (
              <UntaggedPage
                key={i + 1}
                projectId={projectId}
                days={days}
                page={i + 1}
                canMove={canMove}
                selected={selected}
                onToggle={toggle}
              />
            ))}
          </ul>
          {first.list && shown < total && (
            <button type="button" className={BUTTON} onClick={() => setPages((n) => n + 1)}>
              Load more ({plural(total - shown, 'more test')})
            </button>
          )}
        </>
      )}
    </aside>
  )
}
