/**
 * A run picker for Run Compare (FAIL-06): a search over EVERY run the reader
 * can see — build number, branch, CI job, a commit-hash prefix — newest first,
 * 20 a page with "Load older", optionally narrowed to a suite; plus a "Paste
 * run ID" field the server checks. It replaced a `<select>` of the newest 100
 * runs of all time, where an older baseline could not be picked at all.
 *
 * Reads: `useRunSearch` (one SWR entry per page) and `useRunSummary` (the
 * selected run's label). The paste check is the Use button's one-off read
 * (`runListService.getRun`), not a page read: a 403 and a 404 give the SAME
 * text, so the field says nothing about runs the reader cannot see.
 */
import { useEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { RUN_SEARCH_PAGE_SIZE, useRunSearch, useRunSummary } from '@/hooks/useRunSearch'
import { runListService } from '@/services/runListService'
import { formatRunOption } from '@/utils/runOption'

const SEARCH_DEBOUNCE_MS = 300
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const NOT_A_RUN_ID = 'Not a run ID (expected a UUID).'
const RUN_NOT_FOUND = 'Run not found or not accessible.'

export interface RunPickerProps {
  /** The field's caption and the popover's accessible name. */
  label: string
  /** The selected run id ('' = none). */
  value: string
  onChange: (runId: string) => void
  /** Narrow the search to one suite. */
  suiteName?: string | null
  /** Controlled open state (optional). */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  testId: string
  /** Which edge the 420 px popover lines up with ('right' for a picker near the page's right edge). */
  align?: 'left' | 'right'
  /**
   * W11 GATE-02: list THIS project's runs (the policy simulator's picker, bound
   * to the policy's project). Absent or null: the top bar's scope, as before.
   */
  projectId?: string | null
}

export default function RunPicker({
  label, value, onChange, suiteName, open, onOpenChange, testId, align = 'left', projectId,
}: RunPickerProps) {
  const [ownOpen, setOwnOpen] = useState(false)
  const isOpen = open ?? ownOpen
  const rootRef = useRef<HTMLDivElement>(null)
  const setOpen = (next: boolean) => {
    if (open === undefined) setOwnOpen(next)
    onOpenChange?.(next)
  }
  const { data: selected } = useRunSummary(value || null)
  const shown = value ? (selected ? formatRunOption(selected) : `Run ${value.slice(0, 8)}`) : 'Select a run'

  // Escape and a click outside close the popover.
  useEffect(() => {
    if (!isOpen) return
    const close = () => {
      if (open === undefined) setOwnOpen(false)
      onOpenChange?.(false)
    }
    const onPointer = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('mousedown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [isOpen, open, onOpenChange])

  return (
    <div ref={rootRef} className="relative text-xs" data-run-picker={testId}>
      <span className="text-[var(--color-text-muted)]">{label}</span>
      <button
        type="button"
        data-testid={testId}
        aria-haspopup="dialog"
        aria-expanded={isOpen}
        title={shown}
        onClick={() => setOpen(!isOpen)}
        className="mt-1 flex w-full items-center justify-between gap-2 px-2 py-1.5 text-sm text-left bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded font-mono"
      >
        <span className="min-w-0 truncate">{shown}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" aria-hidden />
      </button>
      {isOpen && (
        <RunPickerPanel
          label={label}
          align={align}
          suiteName={suiteName ?? null}
          projectId={projectId}
          onSelect={(runId) => {
            onChange(runId)
            setOpen(false)
          }}
        />
      )}
    </div>
  )
}

/** The popover: search, results (paged), the count, and the paste field. Mounted only while open. */
function RunPickerPanel({
  label, align, suiteName, projectId, onSelect,
}: {
  label: string
  align: 'left' | 'right'
  suiteName: string | null
  projectId?: string | null
  onSelect: (runId: string) => void
}) {
  const [text, setText] = useState('')
  const [pendingQ, setPendingQ] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [pages, setPages] = useState(1)
  const [paste, setPaste] = useState('')
  const [pasteError, setPasteError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  // The search asks SEARCH_DEBOUNCE_MS after the last key, from page 1.
  useEffect(() => {
    if (pendingQ === null) return
    const timer = window.setTimeout(() => {
      setPendingQ(null)
      setQ(pendingQ)
      setPages(1)
    }, SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [pendingQ])

  // Page 1 carries the total; the last page says how far the list got. Every
  // page in between is full (Load older is offered only while more exist).
  const first = useRunSearch({ q, suiteName, page: 1, enabled: true, projectId })
  const last = useRunSearch({ q, suiteName, page: pages, enabled: pages > 1, projectId })
  const total = first.data?.total ?? 0
  const loaded = pages === 1
    ? (first.data?.items.length ?? 0)
    : (pages - 1) * RUN_SEARCH_PAGE_SIZE + (last.data?.items.length ?? 0)

  const checkPasted = async () => {
    const id = paste.trim()
    if (!UUID_RE.test(id)) {
      setPasteError(NOT_A_RUN_ID)
      return
    }
    setPasteError(null)
    setChecking(true)
    try {
      await runListService.getRun(id, { suppressToast: true })
    } catch {
      // 403 and 404 alike: one text, so the field reveals nothing.
      setChecking(false)
      setPasteError(RUN_NOT_FOUND)
      return
    }
    onSelect(id)
  }

  return (
    <div
      role="dialog"
      aria-label={label}
      className={clsx(
        'absolute z-30 mt-1 w-[420px] rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] p-2.5 shadow-lg space-y-2',
        align === 'right' ? 'right-0' : 'left-0',
      )}
    >
      <input
        type="search"
        aria-label="Search runs"
        placeholder="Build, branch, job or commit…"
        value={text}
        maxLength={100}
        autoFocus
        onChange={(e) => {
          setText(e.target.value)
          setPendingQ(e.target.value.trim())
        }}
        className="w-full px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
      />
      <div className="max-h-[320px] overflow-y-auto" data-run-picker-results="">
        {Array.from({ length: pages }, (_, i) => (
          <RunSearchPage key={i + 1} q={q} suiteName={suiteName} projectId={projectId} page={i + 1} onSelect={onSelect} />
        ))}
        {first.data && total === 0 && (
          <p className="m-0 px-2 py-3 text-xs text-[var(--color-text-muted)]">No runs match.</p>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-[var(--color-text-muted)]">
        <span data-run-picker-count="">Showing {loaded} of {total} · all releases</span>
        {loaded < total && (
          <button
            type="button"
            onClick={() => setPages((p) => p + 1)}
            disabled={pages > 1 && !last.data}
            className="text-[var(--color-accent)] hover:underline disabled:opacity-50"
          >
            Load older
          </button>
        )}
      </div>
      <div className="border-t border-[var(--color-border)] pt-2">
        <div className="flex items-center gap-2">
          <input
            aria-label="Paste run ID"
            placeholder="Run UUID"
            value={paste}
            onChange={(e) => {
              setPaste(e.target.value)
              setPasteError(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                void checkPasted()
              }
            }}
            className="min-w-0 flex-1 px-2 py-1.5 text-sm font-mono bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded"
          />
          <button type="button" onClick={() => void checkPasted()} disabled={checking} className="btn-secondary text-xs disabled:opacity-50">
            Use
          </button>
        </div>
        {pasteError && (
          <p className="m-0 mt-1 text-xs text-[var(--status-failed)]" data-run-picker-error="">
            {pasteError}
          </p>
        )}
      </div>
    </div>
  )
}

/** One page of results: its own SWR entry, so the pages already shown stay put. */
function RunSearchPage({
  q, suiteName, projectId, page, onSelect,
}: {
  q: string
  suiteName: string | null
  projectId?: string | null
  page: number
  onSelect: (runId: string) => void
}) {
  const { data, isLoading } = useRunSearch({ q, suiteName, page, enabled: true, projectId })
  if (isLoading && !data) {
    return <p className="m-0 px-2 py-2 text-xs text-[var(--color-text-muted)]">Loading runs…</p>
  }
  return (
    <>
      {(data?.items ?? []).map((run) => (
        <button
          key={run.id}
          type="button"
          onClick={() => onSelect(run.id)}
          data-run-option={run.id}
          className="block w-full truncate rounded px-2 py-1.5 text-left font-mono text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
        >
          {formatRunOption(run)}
        </button>
      ))}
    </>
  )
}
