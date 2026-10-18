/**
 * The bulk bar on the Cases tab (W28 TCM-03): change the status, owner,
 * priority, tags or suite of the selected cases in ONE request.
 *
 * In the page, between the alerts and the cases layout (the host places it),
 * never an overlay. Every row of it wraps (`flex-wrap`, no fixed widths): CI's
 * DejaVu fonts are wider than Windows', and at 1280 the controls take a second
 * line when a reason shows. The answer is per case: the bar says
 * "2 changed · 1 skipped" and lists each skipped case by the title the reader
 * saw, with the server's one sentence (React text). A failure of the whole
 * request is one inline sentence and keeps the selection.
 *
 * Deprecate / Archive / Reinstate are offered only when `canRetire` (QA leads
 * and admins: the contract's AMENDMENT; the server refuses them below QA_LEAD
 * for the whole request). The owner list is read only once Owner is chosen.
 */
import { useId, useMemo, useState, type Ref } from 'react'
import { useProjectMembers } from '@/hooks/useUserManagement'
import { errorSentence, testCaseEditingService } from '@/services/testCaseEditingService'
import type { ManagedTestCase, TestCaseBulkAction, TestCaseBulkResponse } from '@/types/test-management'
import {
  BULK_PRIORITIES,
  UNASSIGNED,
  applyBlocker,
  bulkActionOptions,
  bulkRequestFor,
  bulkTransitionOptions,
  needsReason,
  skippedLines,
  summarize,
  type BulkChoice,
  type BulkTransition,
} from './bulkActionModel'

interface BulkActionBarProps {
  projectId: string
  /** The selected rows, in list order (≥ 0). */
  selected: ManagedTestCase[]
  /** How many rows of this page can be selected ("Select all 25 on this page"). */
  pageSelectableCount: number
  /** false: no "Change status" action. */
  lifecycleV2: boolean
  /** Deprecate / Archive / Reinstate offered: QA_LEAD+ (the AMENDMENT; the host passes hasRole('QA_LEAD')). */
  canRetire: boolean
  suiteOptions: string[]
  onSelectPage: () => void
  onClear: () => void
  /** The host refreshes both rolls and prunes the selection (pruneSelection). */
  onApplied: (result: TestCaseBulkResponse) => void
  /** ⌘E / Ctrl+E focuses the Action select. */
  actionRef?: Ref<HTMLSelectElement>
}

const CONTROL = 'input w-auto max-w-full text-xs py-1.5'
const TEXT_BUTTON = 'btn-ghost text-xs px-2 py-1 hover:text-[var(--color-text)] disabled:opacity-50'

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export default function BulkActionBar({
  projectId,
  selected,
  pageSelectableCount,
  lifecycleV2,
  canRetire,
  suiteOptions,
  onSelectPage,
  onClear,
  onApplied,
  actionRef,
}: BulkActionBarProps) {
  const uid = useId()
  const [choice, setChoice] = useState<BulkChoice>({ action: '' })
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ response: TestCaseBulkResponse; selected: ManagedTestCase[] } | null>(null)

  const actionOptions = useMemo(() => bulkActionOptions(lifecycleV2), [lifecycleV2])
  const transitionOptions = useMemo(() => bulkTransitionOptions(canRetire), [canRetire])

  // What the controls hold, narrowed to what this viewer may choose now (a lost permission or a v2 switch drops it).
  const effective: BulkChoice = useMemo(() => {
    if (choice.action === 'transition' && !lifecycleV2) return { action: '' }
    if (choice.action === 'transition' && choice.transition && !transitionOptions.some((o) => o.value === choice.transition)) {
      return { ...choice, transition: '' }
    }
    return choice
  }, [choice, lifecycleV2, transitionOptions])

  // Read only when Owner is chosen (no request on page load).
  const { data: members, isLoading: membersLoading } = useProjectMembers(effective.action === 'set_owner' ? projectId : null)
  const owners = useMemo(
    () => (members ?? [])
      .map((member) => ({ id: member.user_id, name: member.full_name || member.username }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    [members],
  )

  const count = selected.length
  const blocker = applyBlocker(effective, count)
  const reasonShown = needsReason(effective)

  const update = (next: Partial<BulkChoice>) => {
    setChoice((current) => ({ ...current, ...next }))
    setError(null)
    setResult(null)
  }

  const apply = async () => {
    if (blocker || applying) return
    const snapshot = selected
    const body = bulkRequestFor(projectId, snapshot, effective)
    setApplying(true)
    setError(null)
    setResult(null)
    try {
      const response = await testCaseEditingService.bulkUpdate(body)
      setResult({ response, selected: snapshot })
      onApplied(response)
    } catch (err) {
      setError(errorSentence(err, 'The bulk edit could not be applied.'))
    } finally {
      setApplying(false)
    }
  }

  const lines = result ? skippedLines(result.response, result.selected) : []

  return (
    <div
      role="toolbar"
      aria-label="Bulk edit"
      data-bulk-bar=""
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-3 py-2 space-y-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        {count > 0 ? (
          <span className="text-sm font-medium text-[var(--color-text)]" data-bulk-count="">{count} selected</span>
        ) : (
          <span className="text-sm text-[var(--color-text-muted)]">Select cases with the checkboxes to edit them together.</span>
        )}
        {pageSelectableCount > 0 && (
          <button type="button" className={TEXT_BUTTON} onClick={onSelectPage} disabled={applying}>
            Select all {pageSelectableCount} on this page
          </button>
        )}
        <button type="button" className={TEXT_BUTTON} onClick={onClear} disabled={applying}>
          Clear
        </button>

        <select
          ref={actionRef}
          aria-label="Action"
          className={CONTROL}
          value={effective.action}
          disabled={applying}
          onChange={(e) => update({ action: e.target.value as TestCaseBulkAction | '' })}
        >
          <option value="">Choose an action…</option>
          {actionOptions.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>

        {effective.action === 'transition' && (
          <select
            aria-label="New status"
            className={CONTROL}
            value={effective.transition ?? ''}
            disabled={applying}
            onChange={(e) => update({ transition: e.target.value as BulkTransition | '' })}
          >
            <option value="">Choose a status change…</option>
            {transitionOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        )}

        {effective.action === 'set_owner' && (
          <select
            aria-label="Owner"
            className={CONTROL}
            value={effective.owner ?? ''}
            disabled={applying}
            onChange={(e) => update({ owner: e.target.value })}
          >
            <option value="">{membersLoading ? 'Loading members…' : 'Choose an owner…'}</option>
            <option value={UNASSIGNED}>Unassigned</option>
            {owners.map((owner) => (
              <option key={owner.id} value={owner.id}>{owner.name}</option>
            ))}
          </select>
        )}

        {effective.action === 'set_priority' && (
          <select
            aria-label="Priority"
            className={CONTROL}
            value={effective.priority ?? ''}
            disabled={applying}
            onChange={(e) => update({ priority: e.target.value })}
          >
            <option value="">Choose a priority…</option>
            {BULK_PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>{capitalize(priority)}</option>
            ))}
          </select>
        )}

        {(effective.action === 'add_tags' || effective.action === 'remove_tags') && (
          <input
            type="text"
            aria-label="Tags"
            className="input w-auto min-w-0 flex-1 basis-48 text-xs py-1.5"
            placeholder="Comma-separated, e.g. smoke, checkout"
            value={effective.tags ?? ''}
            disabled={applying}
            onChange={(e) => update({ tags: e.target.value })}
          />
        )}

        {effective.action === 'move_to_suite' && (
          <>
            <input
              type="text"
              aria-label="Suite"
              className="input w-auto min-w-0 flex-1 basis-48 text-xs py-1.5"
              placeholder="Suite name"
              list={`${uid}-suites`}
              value={effective.suiteName ?? ''}
              disabled={applying}
              onChange={(e) => update({ suiteName: e.target.value })}
            />
            <datalist id={`${uid}-suites`}>
              {suiteOptions.map((name) => <option key={name} value={name} />)}
            </datalist>
          </>
        )}

        {reasonShown && (
          <input
            type="text"
            aria-label="Reason"
            required
            maxLength={500}
            className="input w-auto min-w-0 flex-1 basis-48 text-xs py-1.5"
            placeholder="Reason (required, recorded in the audit trail)"
            value={effective.reason ?? ''}
            disabled={applying}
            onChange={(e) => update({ reason: e.target.value })}
          />
        )}

        <button
          type="button"
          className="btn-primary text-xs px-3 py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
          disabled={blocker !== null || applying}
          title={blocker ?? undefined}
          onClick={() => void apply()}
        >
          {applying ? 'Applying…' : 'Apply'}
        </button>
      </div>

      {error && (
        <p role="alert" className="m-0 text-xs text-[var(--status-failed)] [overflow-wrap:anywhere]">{error}</p>
      )}

      {result && (
        <div className="space-y-1" data-bulk-result="">
          <p role="status" className="m-0 text-xs font-medium text-[var(--color-text)]">{summarize(result.response)}</p>
          {lines.length > 0 && (
            <div data-bulk-skipped="">
              <p className="m-0 text-xs font-semibold text-[var(--color-text-muted)]">Skipped</p>
              <ul className="m-0 mt-0.5 space-y-0.5 pl-4 list-disc text-xs text-[var(--color-text-secondary)]">
                {lines.map((line) => (
                  <li key={line.id} className="[overflow-wrap:anywhere]">
                    {line.label} — {line.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
