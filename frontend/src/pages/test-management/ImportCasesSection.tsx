/**
 * W13 TCL-04 — Import test cases from a CSV or an Excel workbook, IN THE PAGE
 * (the Cases tab's top slot, `'import'`: one form at a time with New test case
 * and AI Generate; never a dialog).
 *
 * Choosing a file runs the DRY RUN at once (nothing is written): the summary,
 * the header report, outcome chips over a dense preview. **Import N cases**
 * sends the SAME file again with `dry_run=false` (the server re-validates it)
 * and the table then shows what was created, each created row opening its
 * case. A 400 / 413 / 422 renders the server's sentence here (the service
 * passes `suppressToast`).
 *
 * Every cell is React TEXT: a title `=HYPERLINK("…")` is shown as those
 * characters; no `href` or `style` is built from a cell.
 */
import { useId, useRef, useState, type ChangeEvent } from 'react'
import { Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { Table, Td, Th, Tr } from '@/components/ui/Table'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { testLibraryService } from '@/services/testLibraryService'
import type { ImportRowResult, TestCaseImportResponse } from '@/types/test-library'
import { downloadBlob } from '@/utils/download'
import { formatNumber } from '@/utils/formatters'
import {
  IMPORT_ACCEPT,
  IMPORT_APPLY_FAILED,
  IMPORT_APPLYING_TEXT,
  IMPORT_CHECK_FAILED,
  IMPORT_CHECKING_TEXT,
  IMPORT_LIMITS_TEXT,
  IMPORT_TITLE,
  IMPORT_TITLE_ID,
  OUTCOME_TONE,
  TEMPLATE_FILE_NAME,
  TEMPLATE_LINK_TEXT,
  applyLabel,
  duplicateTarget,
  filterRows,
  headerReportLines,
  importToast,
  outcomeChips,
  outcomeLabel,
  resultLine,
  serverSentence,
  summaryLine,
  templateCsv,
  type ImportOutcomeFilter,
} from './importCasesModel'

export interface ImportCasesSectionProps {
  projectId: string
  slot: InlineSlot<'create' | 'ai' | 'import'>
  onClose: () => void
  /** After an apply that created at least one case (the host refreshes the cases roll AND health). */
  onImported: (created: number) => void
  onOpenCase: (caseId: string) => void
}

type Phase = 'idle' | 'checking' | 'preview' | 'applying' | 'applied'

const LINK_BUTTON = 'text-left text-[var(--color-accent-ink)] hover:underline [overflow-wrap:anywhere]'
const CHIP =
  'inline-flex items-center gap-1 rounded-full border border-[var(--color-border)] px-2.5 py-0.5 text-xs text-[var(--color-text-secondary)] ' +
  'hover:bg-[var(--color-bg-hover)] aria-pressed:border-[var(--color-accent)] aria-pressed:bg-[var(--color-accent-bg-soft)] aria-pressed:text-[var(--color-accent-ink)]'
const PILL = 'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium'

function WhatHappens({ row }: { row: ImportRowResult }) {
  const target = duplicateTarget(row.duplicate_of)
  const reasons = row.reasons ?? []
  const warnings = row.warnings ?? []
  if (!reasons.length && !warnings.length && !target) return <span className="text-[var(--color-text-faint)]">—</span>
  return (
    <ul className="m-0 list-none space-y-0.5 p-0 text-xs [overflow-wrap:anywhere]">
      {reasons.map((reason, i) => (
        <li key={`r${i}`} data-import-reason="" className="text-[var(--ui-danger-ink)]">{reason}</li>
      ))}
      {target && (
        <li data-import-duplicate-of="" className="text-[var(--color-text-secondary)]">Duplicate of {target}</li>
      )}
      {warnings.map((warning, i) => (
        <li key={`w${i}`} data-import-warning="" className="text-[var(--color-text-muted)]">{warning}</li>
      ))}
    </ul>
  )
}

export default function ImportCasesSection({ projectId, slot, onClose, onImported, onOpenCase }: ImportCasesSectionProps) {
  const inputId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [result, setResult] = useState<TestCaseImportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<ImportOutcomeFilter>('all')
  // A second file chosen while the first is still being checked: only the latest answer counts.
  const ticket = useRef(0)

  async function check(next: File) {
    const mine = ++ticket.current
    setFile(next)
    setResult(null)
    setError(null)
    setFilter('all')
    setPhase('checking')
    try {
      const answer = await testLibraryService.importCases(projectId, next, true)
      if (mine !== ticket.current) return
      setResult(answer)
      setPhase('preview')
    } catch (err) {
      if (mine !== ticket.current) return
      setError(serverSentence(err, IMPORT_CHECK_FAILED))
      setPhase('idle')
    }
  }

  async function apply() {
    if (!file || !result || phase !== 'preview') return
    const mine = ++ticket.current
    setError(null)
    setPhase('applying')
    try {
      const answer = await testLibraryService.importCases(projectId, file, false)
      if (mine !== ticket.current) return
      setResult(answer)
      setFilter('all')
      setPhase('applied')
      // Nothing typed is left to lose: closing now must not ask.
      slot.setDirty(false)
      const created = answer.summary.created ?? 0
      if (created > 0) {
        toast.success(importToast(created))
        onImported(created)
      }
    } catch (err) {
      if (mine !== ticket.current) return
      setError(serverSentence(err, IMPORT_APPLY_FAILED))
      setPhase('preview')
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    const next = event.target.files?.[0]
    if (next) void check(next)
  }

  const applied = phase === 'applied'
  const rows = result?.rows ?? []
  const shown = filterRows(rows, filter)
  const headerLines = headerReportLines(result?.headers)
  const createCount = result?.dry_run ? result.summary.create : 0

  const footer = applied ? (
    <button type="button" className="btn-secondary" onClick={onClose}>Done</button>
  ) : (
    <>
      <button type="button" className="btn-secondary" onClick={onClose} disabled={phase === 'applying'}>Cancel</button>
      {result && (
        <button
          type="button"
          data-import-apply=""
          className="btn-primary inline-flex items-center gap-2"
          disabled={phase !== 'preview' || createCount === 0}
          onClick={() => void apply()}
        >
          {phase === 'applying' && <LoadingSpinner size="sm" />}
          {phase === 'applying' ? IMPORT_APPLYING_TEXT : applyLabel(createCount)}
        </button>
      )}
    </>
  )

  return (
    <InlineFormSection
      title={IMPORT_TITLE}
      titleId={IMPORT_TITLE_ID}
      onClose={onClose}
      canClose={phase !== 'applying'}
      slot={slot}
      busy={phase === 'checking' || phase === 'applying'}
      dataAttributes={{ 'data-tm-import': '' }}
      footer={footer}
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor={inputId} className="text-sm font-medium text-[var(--color-text)]">File</label>
          <input
            id={inputId}
            type="file"
            accept={IMPORT_ACCEPT}
            data-import-file=""
            onChange={onFileChange}
            disabled={phase === 'checking' || phase === 'applying'}
            className="min-w-0 text-sm text-[var(--color-text-secondary)]"
          />
          <button
            type="button"
            data-import-template=""
            className="btn-ghost btn-sm inline-flex items-center gap-1.5"
            onClick={() => downloadBlob(new Blob([templateCsv()], { type: 'text/csv;charset=utf-8' }), TEMPLATE_FILE_NAME)}
          >
            <Download aria-hidden="true" className="h-3.5 w-3.5" />
            {TEMPLATE_LINK_TEXT}
          </button>
        </div>
        <p className="m-0 text-xs text-[var(--color-text-muted)]">{IMPORT_LIMITS_TEXT}</p>
        {phase === 'checking' && (
          <div role="status" className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <LoadingSpinner size="sm" />
            {IMPORT_CHECKING_TEXT}
          </div>
        )}
        {error && (
          <p role="alert" data-import-error="" className="m-0 text-sm text-[var(--ui-danger-ink)]">{error}</p>
        )}
        {result && (
          <div className="space-y-2" data-import-preview={applied ? 'applied' : 'dry-run'}>
            <p data-import-summary="" className="m-0 text-sm font-medium text-[var(--color-text)]">
              {applied ? resultLine(result.summary) : summaryLine(result.summary)}
            </p>
            {(headerLines.ignored || headerLines.unknown) && (
              <div className="space-y-0.5 text-xs text-[var(--color-text-muted)]" data-import-headers="">
                {headerLines.ignored && <p className="m-0 [overflow-wrap:anywhere]">{headerLines.ignored}</p>}
                {headerLines.unknown && <p className="m-0 [overflow-wrap:anywhere]">{headerLines.unknown}</p>}
              </div>
            )}
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show rows">
              {outcomeChips(rows, applied).map((chip) => (
                <button
                  key={chip.id}
                  type="button"
                  data-import-chip={chip.id}
                  aria-pressed={filter === chip.id}
                  className={CHIP}
                  onClick={() => setFilter(chip.id)}
                >
                  {chip.label}{' '}
                  <span className="tabular-nums">{formatNumber(chip.count)}</span>
                </button>
              ))}
            </div>
            <div className="max-h-[28rem] overflow-y-auto rounded-md border border-[var(--color-border)]">
              <Table density="dense" className="table-fixed">
                <thead>
                  <tr>
                    <Th numeric className="w-14">Row</Th>
                    <Th>Title</Th>
                    <Th className="w-24">Outcome</Th>
                    <Th>What happens</Th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((row) => (
                    <Tr key={row.row} interactive={false} data-import-row={String(row.row)} data-outcome={row.outcome}>
                      <Td numeric>{row.row}</Td>
                      <Td className="min-w-0 [overflow-wrap:anywhere]">
                        {row.outcome === 'created' && row.case_id ? (
                          <button type="button" className={LINK_BUTTON} onClick={() => onOpenCase(row.case_id as string)}>
                            {row.title || 'Untitled'}
                          </button>
                        ) : (
                          row.title || <span className="text-[var(--color-text-faint)]">—</span>
                        )}
                      </Td>
                      <Td>
                        <span className={clsx(PILL, OUTCOME_TONE[row.outcome] ?? 'border border-[var(--color-border)] text-[var(--color-text-muted)]')}>
                          {outcomeLabel(row.outcome)}
                        </span>
                      </Td>
                      <Td className="min-w-0"><WhatHappens row={row} /></Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </div>
          </div>
        )}
      </div>
    </InlineFormSection>
  )
}
