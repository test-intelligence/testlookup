/**
 * ReviewStateControl — replaces the static "Pending Human Review" badge
 * on the AI Analysis panel with a dropdown that lets the reviewer record
 * their verdict. Backed by the migration-0081 ``test_execution_reviews``
 * table; absence of a row maps to ``pending_review``.
 */
import { useState } from 'react'
import useSWR from 'swr'
import { CheckCircle2, AlertTriangle, BugPlay, XCircle, ChevronDown } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  testExecutionReviewService,
  type TestExecutionReviewRead,
  type TestExecutionReviewState,
  type TestExecutionReviewUpdate,
} from '@/services/testExecutionReviewService'

const STATE_OPTIONS: Array<{
  value: Exclude<TestExecutionReviewState, 'pending_review'>
  label: string
  Icon: typeof CheckCircle2
  badgeCls: string
  helper?: string
}> = [
  {
    value: 'reviewed',
    label: 'Reviewed',
    Icon: CheckCircle2,
    badgeCls: 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)] border border-[var(--status-passed-bd)]/50',
    helper: 'Human confirmed the AI verdict.',
  },
  {
    value: 'defect_filed',
    label: 'Defect filed',
    Icon: BugPlay,
    badgeCls: 'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)] border border-[var(--status-failed-bd)]/50',
    helper: 'Tracker URL required.',
  },
  {
    value: 'false_positive',
    label: 'False positive',
    Icon: XCircle,
    badgeCls: 'bg-[var(--status-flaky-bg)]/40 text-[var(--status-flaky)] border border-[var(--status-flaky-bd)]/50',
    helper: 'Flake or test bug — not a real failure.',
  },
  {
    value: 'reproducible',
    label: 'Reproducible',
    Icon: AlertTriangle,
    badgeCls: 'bg-[var(--status-broken-bg)]/40 text-[var(--status-broken)] border border-[var(--status-broken-bd)]/50',
    helper: 'Failure confirmed locally; awaiting fix.',
  },
]

const PENDING_BADGE = 'bg-[var(--status-broken-bg)]/40 text-[var(--status-broken)] border border-[var(--status-broken-bd)]/50'

interface Props {
  testCaseId: string
}

export default function ReviewStateControl({ testCaseId }: Props) {
  // 404 from the backend = no transition yet → treat as pending_review.
  // SWR's default error retry would spam — disable it here since 404 is
  // the documented "implicit initial state" path.
  const { data, mutate, isLoading } = useSWR<TestExecutionReviewRead | null>(
    ['test-execution-review', testCaseId],
    async () => {
      try {
        return await testExecutionReviewService.get(testCaseId)
      } catch (e: unknown) {
        if ((e as { response?: { status?: number } })?.response?.status === 404) return null
        throw e
      }
    },
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  const [open, setOpen] = useState(false)
  const [pendingState, setPendingState] = useState<Exclude<TestExecutionReviewState, 'pending_review'> | null>(null)
  const [defectLink, setDefectLink] = useState('')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  const currentState: TestExecutionReviewState = data?.state ?? 'pending_review'
  const currentOption = STATE_OPTIONS.find(o => o.value === currentState)

  async function commit(state: Exclude<TestExecutionReviewState, 'pending_review'>, link?: string, n?: string) {
    setSaving(true)
    try {
      const payload: TestExecutionReviewUpdate = { state }
      if (link) payload.defect_link = link
      if (n) payload.note = n
      const updated = await testExecutionReviewService.upsert(testCaseId, payload)
      await mutate(updated, { revalidate: false })
      toast.success(`Marked as ${STATE_OPTIONS.find(o => o.value === state)?.label ?? state}`)
      setOpen(false)
      setPendingState(null)
      setDefectLink('')
      setNote('')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(msg || 'Failed to update review')
    } finally {
      setSaving(false)
    }
  }

  function handlePick(value: Exclude<TestExecutionReviewState, 'pending_review'>) {
    // Defect filed requires a URL — show the inline form. All other states
    // commit immediately so the common case is one click.
    if (value === 'defect_filed') {
      setPendingState(value)
      return
    }
    void commit(value)
  }

  if (isLoading) {
    return (
      <span className={`badge ${PENDING_BADGE} animate-pulse`}>
        ⏳ Loading review…
      </span>
    )
  }

  const currentBadgeCls = currentOption?.badgeCls ?? PENDING_BADGE
  const currentLabel = currentOption?.label ?? 'Pending Human Review'
  const CurrentIcon = currentOption?.Icon

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`badge ${currentBadgeCls} inline-flex items-center gap-1 hover:opacity-90 cursor-pointer`}
        title="Change review state"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {CurrentIcon
          ? <CurrentIcon className="h-3 w-3" />
          : <span>⏳</span>}
        {currentLabel}
        {data?.reviewed_by_username && (
          <span className="ml-1 text-[10px] opacity-70">
            · by {data.reviewed_by_full_name || data.reviewed_by_username}
          </span>
        )}
        <ChevronDown className="h-3 w-3 opacity-70" />
      </button>

      {open && !pendingState && (
        <div
          role="listbox"
          className="absolute z-20 mt-1 right-0 min-w-[260px] rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] shadow-lg"
        >
          <div className="px-3 py-2 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
            Transition review state
          </div>
          {STATE_OPTIONS.map(opt => {
            const Icon = opt.Icon
            const isCurrent = opt.value === currentState
            return (
              <button
                key={opt.value}
                type="button"
                disabled={saving || isCurrent}
                onClick={() => handlePick(opt.value)}
                className="w-full flex items-start gap-2 px-3 py-2 text-left text-xs hover:bg-[var(--color-bg-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Icon className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <span className="flex-1">
                  <span className="font-medium text-[var(--color-text)]">{opt.label}</span>
                  {opt.helper && (
                    <span className="block text-[10.5px] text-[var(--color-text-muted)] mt-0.5">
                      {opt.helper}
                    </span>
                  )}
                </span>
                {isCurrent && <span className="text-[10px] text-[var(--color-text-faint)] mt-0.5">current</span>}
              </button>
            )
          })}
          {currentState !== 'pending_review' && (
            <>
              <div className="border-t border-[var(--color-border)]" />
              <button
                type="button"
                onClick={async () => {
                  setSaving(true)
                  try {
                    await testExecutionReviewService.clear(testCaseId)
                    await mutate(null, { revalidate: false })
                    toast.success('Review cleared — back to Pending')
                    setOpen(false)
                  } catch {
                    toast.error('Failed to clear review')
                  } finally {
                    setSaving(false)
                  }
                }}
                className="w-full text-left text-xs px-3 py-2 hover:bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] disabled:opacity-50"
                disabled={saving}
              >
                Clear review (re-open as Pending)
              </button>
            </>
          )}
        </div>
      )}

      {open && pendingState === 'defect_filed' && (
        <div
          className="absolute z-20 mt-1 right-0 min-w-[320px] rounded-md border border-[var(--color-border)] bg-[var(--color-bg-card)] shadow-lg p-3 space-y-2"
        >
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            Defect filed — capture the tracker link
          </div>
          <input
            type="url"
            value={defectLink}
            onChange={e => setDefectLink(e.target.value)}
            placeholder="https://jira.example.com/browse/PROJ-123"
            className="w-full text-xs rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 focus:outline-none focus:border-[var(--color-accent)]"
            autoFocus
          />
          <textarea
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder="Optional note (visible to other reviewers)"
            rows={2}
            className="w-full text-xs rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1.5 focus:outline-none focus:border-[var(--color-accent)]"
          />
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => { setPendingState(null); setDefectLink(''); setNote('') }}
              className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-2 py-1"
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => commit('defect_filed', defectLink.trim(), note.trim() || undefined)}
              disabled={saving || !defectLink.trim()}
              className="text-xs px-3 py-1 rounded bg-[var(--color-btn-primary-bg)] hover:bg-[var(--color-btn-primary-hover)] disabled:opacity-50 text-[var(--color-btn-primary-text)] font-medium"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
