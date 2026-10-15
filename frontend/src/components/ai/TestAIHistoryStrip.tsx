/**
 * W03 AIH-05: "AI analysis across runs" on the test History tab (contract W03
 * §9.8): one chip per run whose result has an AI analysis, oldest -> newest,
 * coloured by category; the markers say where the category changed
 * ("Category changed on Run #60: INFRASTRUCTURE → PRODUCT_BUG") or only
 * reworded its root cause. A chip opens that run's stored versions.
 *
 * Everything comes from the ONE history read the panel already made (`ai` per
 * point, `ai_changes`): the strip adds no request per run; the versions are read
 * only when a popover opens. Names and root causes render as React text.
 */
import { useMemo, useState } from 'react'
import { clsx } from 'clsx'
import type { TestHistoryAIChange, TestHistoryAIPoint } from '@/types/aiTestHistory'
import { categoryColor } from '@/utils/formatters'
import { buildStripModel } from './aiTestHistory.model'
import TestAIVersionsPopover from './TestAIVersionsPopover'

/** Markers shown before "Show all": category changes come first, rewordings after. */
export const MAX_VISIBLE_CHANGES = 4

const NO_CHANGES: readonly TestHistoryAIChange[] = []

export default function TestAIHistoryStrip({
  history,
  changes,
  runId,
}: {
  /** The history points as the API sends them (newest first). */
  history: readonly TestHistoryAIPoint[]
  /** `ai_changes` of the same read; absent on an older server. */
  changes?: readonly TestHistoryAIChange[] | null
  /** The run the page shows (its chip is outlined). */
  runId?: string | null
}) {
  const allChanges = changes ?? NO_CHANGES
  const model = useMemo(
    () => buildStripModel({ history, changes: allChanges, runId }),
    [history, allChanges, runId],
  )
  const [openKey, setOpenKey] = useState<string | null>(null)
  const [showAllChanges, setShowAllChanges] = useState(false)

  if (model.chips.length === 0) return null

  const open = model.chips.find((chip) => chip.key === openKey) ?? null
  const visibleChanges = showAllChanges ? model.changes : model.changes.slice(0, MAX_VISIBLE_CHANGES)
  const hiddenChanges = model.changes.length - visibleChanges.length

  return (
    <section data-test-ai-strip="" aria-label="AI analysis across runs" className="space-y-1.5">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        AI analysis across runs (oldest → newest)
      </p>
      <div className="flex flex-wrap items-center gap-1.5">
        {model.chips.map((chip) => {
          const changedCategory = chip.changeKinds.includes('category')
          const selected = chip.key === openKey
          return (
            <button
              key={chip.key}
              type="button"
              data-test-ai-chip=""
              data-run-id={chip.runId ?? ''}
              data-category={chip.category ?? ''}
              data-changed={changedCategory ? 'category' : chip.changeKinds.length > 0 ? 'root_cause' : undefined}
              data-current-run={chip.isCurrentRun ? '' : undefined}
              aria-pressed={selected}
              title={chip.title}
              onClick={() => setOpenKey((key) => (key === chip.key ? null : chip.key))}
              className={clsx(
                'inline-flex items-center rounded border bg-[var(--color-bg-secondary)] px-1.5 py-0.5 font-mono text-[10px] hover:bg-[var(--color-bg-hover)]',
                categoryColor(chip.category ?? ''),
                chip.isCurrentRun ? 'border-[var(--color-accent)]' : 'border-[var(--color-border)]',
                changedCategory && 'ring-1 ring-[var(--status-broken)]',
                selected && 'bg-[var(--color-bg-hover)]',
              )}
            >
              {chip.text}
            </button>
          )
        })}
      </div>

      {visibleChanges.map((change) => (
        <p
          key={change.key}
          data-test-ai-change={change.tone}
          data-run-id={change.runId ?? ''}
          className={clsx(
            'text-xs',
            change.tone === 'category' ? 'text-[var(--status-broken)]' : 'text-[var(--color-text-muted)]',
          )}
        >
          {change.text}
        </p>
      ))}
      {model.changes.length > MAX_VISIBLE_CHANGES && (
        <button
          type="button"
          data-test-ai-changes-toggle=""
          onClick={() => setShowAllChanges((v) => !v)}
          className="text-xs text-[var(--color-accent)] hover:underline"
        >
          {showAllChanges ? 'Show fewer changes' : `Show ${hiddenChanges} more ${hiddenChanges === 1 ? 'change' : 'changes'}`}
        </button>
      )}

      {open && (
        <TestAIVersionsPopover
          key={open.key}
          runId={open.runId}
          runLabel={open.runLabel}
          ai={open.ai}
          onClose={() => setOpenKey(null)}
        />
      )}
    </section>
  )
}
