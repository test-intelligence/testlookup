/**
 * A replay run's items (W16 AGT-09 b, ADMIN only — a feedback-built dataset carries text from every project, so the
 * route is ADMIN and QA leads see aggregates only, §7.2 D-4): status chips with the run's counts, a compact table of
 * 25 a page, and a row that opens its diff inline — `Expected` | `Candidate output` as two `<pre>` columns plus the
 * input excerpt.
 *
 * Every value is model or dataset output: React TEXT only (JSON through `JSON.stringify` inside a `<pre>`), never
 * markup, never a link, never a style — a `<script>` string in an output is shown as those characters.
 */
import { Fragment, useState } from 'react'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { useEvalRunItems } from '@/hooks/useAiEval'
import type { EvalItem } from '@/types/aiEval'
import { truncateMiddle } from '@/utils/formatters'
import { CHIP_CLASS, COPY, ITEM_FILTERS, TONE_CLASS, itemCells, itemStatusChip, prettyJson } from './aiEvalModel'

/** `AUTOMATION_DEFECT` → `AUTOMATION_` `<wbr>` `DEFECT`: a category breaks only after an underscore (W14's rule for stage
 *  names), so the Expected / Candidate columns wrap instead of cutting the value they exist to compare. */
function breakableCategory(value: string) {
  const parts = value.split('_')
  return parts.map((part, i) => (
    <Fragment key={i}>
      {part}
      {i < parts.length - 1 && <>_<wbr /></>}
    </Fragment>
  ))
}

export const ITEMS_PAGE_SIZE = 25
/** The Item column (150 px) holds ~18 characters of text-xs on CI's DejaVu. */
const ITEM_LABEL_CHARS = 18

const PRE = 'm-0 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-2 font-mono text-2xs text-[var(--color-text)]'

function Diff({ item }: { item: EvalItem }) {
  return (
    <div data-item-diff={item.item_index} className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <div className="min-w-0">
          <p className="eyebrow m-0 mb-1 text-[var(--color-text-secondary)]">Expected</p>
          <pre className={PRE}>{prettyJson(item.expected)}</pre>
        </div>
        <div className="min-w-0">
          <p className="eyebrow m-0 mb-1 text-[var(--color-text-secondary)]">Candidate output</p>
          <pre className={PRE}>{prettyJson(item.output)}</pre>
        </div>
      </div>
      {item.error_text && <p className="m-0 text-xs text-[var(--ui-warning-ink)]">{item.error_text}</p>}
      <div className="min-w-0">
        <p className="eyebrow m-0 mb-1 text-[var(--color-text-secondary)]">Input</p>
        <pre className={clsx(PRE, 'max-h-32')}>{prettyJson(item.input)}</pre>
      </div>
    </div>
  )
}

export default function EvalRunItems({ runId, taskType }: { runId: string; taskType: string }) {
  const [filter, setFilter] = useState<string>('all')
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<number | null>(null)
  const status = ITEM_FILTERS.find((f) => f.id === filter)?.status
  const { data, error, isLoading, mutate } = useEvalRunItems(
    runId,
    { ...(status ? { status } : {}), limit: ITEMS_PAGE_SIZE, offset },
    true,
  )
  const items = Array.isArray(data?.items) ? data.items : []
  const total = typeof data?.total === 'number' ? data.total : items.length
  const counts: Record<string, number> = data?.counts && typeof data.counts === 'object' ? data.counts : {}
  const allCount = Object.values(counts).reduce((sum, n) => sum + (typeof n === 'number' ? n : 0), 0)

  const choose = (id: string) => {
    setFilter(id)
    setOffset(0)
    setOpen(null)
  }

  return (
    <div data-run-items={runId} className="space-y-2">
      <div role="group" aria-label="Item status" className="flex flex-wrap gap-1.5">
        {ITEM_FILTERS.map((f) => {
          const n = f.status ? counts[f.status] ?? 0 : allCount
          const active = filter === f.id
          return (
            <button
              key={f.id}
              type="button"
              aria-pressed={active}
              data-item-filter={f.id}
              onClick={() => choose(f.id)}
              className={clsx(
                'rounded-full border px-2.5 py-0.5 text-xs',
                active
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent-bg-soft)] text-[var(--color-text)]'
                  : 'border-[var(--color-border)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]',
              )}
            >
              {f.label} ({n})
            </button>
          )
        })}
      </div>

      {isLoading ? (
        <LoadingSpinner size="sm" />
      ) : error ? (
        <p className="m-0 text-xs text-[var(--color-text-muted)]">
          {COPY.loadError.items}{' '}
          <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
        </p>
      ) : items.length === 0 ? (
        <p className="m-0 text-xs text-[var(--color-text-muted)]">{allCount === 0 && !status ? COPY.legacyItems : COPY.itemsEmpty}</p>
      ) : (
        <div className="overflow-x-auto" data-table-scroller="run-items">
          <Table density="compact" className="table-fixed text-xs" aria-label="Evaluation items">
            <colgroup>
              <col style={{ width: 32 }} />
              <col style={{ width: 150 }} />
              <col style={{ width: 84 }} />
              <col style={{ width: 140 }} />
              <col style={{ width: 140 }} />
              <col style={{ width: 64 }} />
              <col style={{ width: 44 }} />
            </colgroup>
            <thead>
              <tr>
                <Th numeric>#</Th>
                <Th>Item</Th>
                <Th>Status</Th>
                <Th>Expected</Th>
                <Th>Candidate</Th>
                <Th>Engine</Th>
                <Th numeric>ms</Th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const cells = itemCells(taskType, item)
                const chip = itemStatusChip(item.status)
                const isOpen = open === item.item_index
                return (
                  <Fragment key={item.item_index}>
                    <Tr selected={isOpen} data-item-row={item.item_index} data-open={isOpen ? 'true' : undefined} onClick={() => setOpen(isOpen ? null : item.item_index)}>
                      <Td numeric>{item.item_index + 1}</Td>
                      {/* The key's END tells items apart (`golden-classification-002`): cut in the middle. */}
                      <Td className="truncate" title={cells.item}>{truncateMiddle(cells.item, ITEM_LABEL_CHARS)}</Td>
                      <Td><span data-item-status={item.status} className={clsx(CHIP_CLASS, TONE_CLASS[chip.tone])}>{chip.label}</span></Td>
                      <Td className="whitespace-normal" title={cells.expected}>{breakableCategory(cells.expected)}</Td>
                      <Td className="whitespace-normal" title={cells.candidate}>{breakableCategory(cells.candidate)}</Td>
                      <Td className="truncate" title={item.mode_used ?? undefined}>{cells.engine}</Td>
                      <Td numeric>{cells.ms}</Td>
                    </Tr>
                    {isOpen && (
                      <tr data-in-page-row="">
                        <td colSpan={7} className="border-b border-[var(--color-border)] px-2 py-2">
                          <Diff item={item} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </Table>
        </div>
      )}

      {total > ITEMS_PAGE_SIZE && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <button type="button" className="btn-secondary btn-sm" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - ITEMS_PAGE_SIZE)); setOpen(null) }}>
            Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + ITEMS_PAGE_SIZE, total)} of {total}
          </span>
          <button type="button" className="btn-secondary btn-sm" disabled={offset + ITEMS_PAGE_SIZE >= total} onClick={() => { setOffset(offset + ITEMS_PAGE_SIZE); setOpen(null) }}>
            Next
          </button>
        </div>
      )}
    </div>
  )
}
