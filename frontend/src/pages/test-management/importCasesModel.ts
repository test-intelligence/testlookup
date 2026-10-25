/**
 * W13 TCL-04 — the Import section's copy and its pure helpers (no React, no
 * fetching). The section (`ImportCasesSection`) renders these strings; its
 * test imports them, so one sentence lives in one place.
 *
 * `IMPORT_TEMPLATE_HEADERS` mirrors the server's list
 * (`backend/app/core/test_library_vocab.py`); `tests/test_import_template_parity.py`
 * reads THIS file as text and pins the two equal — keep it one literal array.
 */
import type { ImportDuplicateOf, ImportHeaderReport, ImportRowResult, ImportSummary } from '@/types/test-library'
import { formatNumber } from '@/utils/formatters'

export const IMPORT_TEMPLATE_HEADERS = ['Title', 'Type', 'Priority', 'Severity', 'Feature Area', 'Objective', 'Preconditions', 'Steps', 'Expected Result', 'Automated', 'Automation Status', 'Tags', 'Suite', 'Description', 'Test Data'] as const

export const IMPORT_TITLE = 'Import test cases'
export const IMPORT_TITLE_ID = 'tm-import-title'
export const IMPORT_ACCEPT = '.csv,.xlsx'
export const IMPORT_LIMITS_TEXT = 'CSV (UTF-8) or Excel, up to 5 MB and 2,000 rows. Every case is imported as a Draft.'
export const IMPORT_CHECKING_TEXT = 'Checking the file…'
export const IMPORT_APPLYING_TEXT = 'Importing…'
export const TEMPLATE_LINK_TEXT = 'Download the CSV template'
export const TEMPLATE_FILE_NAME = 'test-cases-template.csv'
export const IMPORT_CHECK_FAILED = 'The file could not be checked. Try again.'
export const IMPORT_APPLY_FAILED = 'The cases could not be imported. Try again.'

/** The chips over the preview: every row, then one outcome. */
export type ImportOutcomeFilter = 'all' | 'create' | 'duplicate' | 'invalid' | 'created'

const OUTCOME_LABELS: Record<string, string> = {
  create: 'Create',
  duplicate: 'Duplicate',
  invalid: 'Invalid',
  created: 'Created',
}

/** "Create" / "Duplicate" / "Invalid" / "Created"; an unknown outcome as written. */
export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome
}

/** The outcome pill's look: a `--ui-*` tint with its legible `-ink` (never a status hue as text on its tint). */
export const OUTCOME_TONE: Record<string, string> = {
  create: 'bg-[var(--ui-info-bg)] text-[var(--ui-info-ink)] border border-[var(--ui-info-bd)]',
  created: 'bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)] border border-[var(--ui-success-bd)]',
  duplicate: 'bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)] border border-[var(--ui-warning-bd)]',
  invalid: 'bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)] border border-[var(--ui-danger-bd)]',
}

const plural = (n: number, one: string, many: string) => `${formatNumber(n)} ${n === 1 ? one : many}`

/** The dry run's summary: "20 rows: 17 to create · 0 duplicates · 3 invalid". */
export function summaryLine(summary: ImportSummary): string {
  return `${plural(summary.rows, 'row', 'rows')}: ${formatNumber(summary.create)} to create · ${plural(summary.duplicate, 'duplicate', 'duplicates')} · ${formatNumber(summary.invalid)} invalid`
}

/** After the apply: "17 cases imported as Draft. 3 rows were not imported." (the second sentence only when some were not). */
export function resultLine(summary: ImportSummary): string {
  const created = summary.created ?? 0
  const notImported = Math.max(0, summary.rows - created)
  const head = `${plural(created, 'case', 'cases')} imported as Draft.`
  if (notImported === 0) return head
  return `${head} ${plural(notImported, 'row was', 'rows were')} not imported.`
}

/** The apply's toast: "17 test cases imported". */
export function importToast(created: number): string {
  return `${plural(created, 'test case', 'test cases')} imported`
}

/** The apply button: "Import 17 cases" (disabled at 0). */
export function applyLabel(create: number): string {
  return `Import ${plural(create, 'case', 'cases')}`
}

/** The header report: "Ignored columns: Status, Version" / "Unknown columns: Owner" (null when there are none). */
export function headerReportLines(headers: ImportHeaderReport | undefined): { ignored: string | null; unknown: string | null } {
  const ignored = headers?.ignored ?? []
  const unknown = headers?.unknown ?? []
  return {
    ignored: ignored.length ? `Ignored columns: ${ignored.join(', ')}` : null,
    unknown: unknown.length ? `Unknown columns: ${unknown.join(', ')}` : null,
  }
}

/** A duplicate's target: "TC-1a2b3c Login works" (an existing case) or "row 7" (an earlier row of the file). */
export function duplicateTarget(dup: ImportDuplicateOf | null | undefined): string | null {
  if (!dup) return null
  if (dup.case_id) return `TC-${dup.case_id.slice(0, 6)}${dup.title ? ` ${dup.title}` : ''}`
  if (dup.row != null) return `row ${dup.row}`
  return null
}

/** The chips in order, each with its count: All, then the outcomes this answer can hold. */
export function outcomeChips(rows: readonly ImportRowResult[], applied: boolean): { id: ImportOutcomeFilter; label: string; count: number }[] {
  const outcomes: ImportOutcomeFilter[] = applied ? ['created', 'duplicate', 'invalid'] : ['create', 'duplicate', 'invalid']
  return [
    { id: 'all', label: 'All', count: rows.length },
    ...outcomes.map((id) => ({ id, label: outcomeLabel(id), count: rows.filter((row) => row.outcome === id).length })),
  ]
}

export function filterRows(rows: readonly ImportRowResult[], filter: ImportOutcomeFilter): ImportRowResult[] {
  return filter === 'all' ? [...rows] : rows.filter((row) => row.outcome === filter)
}

/** U+FEFF, written by code point (a literal one is invisible in review). */
export const UTF8_BOM = String.fromCharCode(0xfeff)

/** The template a person fills in: the BOM (so Excel reads it as UTF-8) and the header row. */
export function templateCsv(): string {
  return `${UTF8_BOM}${IMPORT_TEMPLATE_HEADERS.join(',')}\r\n`
}

/**
 * The server's sentence of a failed write, for W13's in-page forms (this
 * section, the saved-view form, the requirement form, the target editor):
 * an `HTTPException` detail (a string, or `{ message }`), else the first
 * Pydantic item's `msg` (a 422 array), else `fallback`. Rendered as React text.
 */
export function serverSentence(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } } | null | undefined)?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail)) {
    const first = detail.find((item) => item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string')
    const msg = first ? String((first as { msg: string }).msg).replace(/^Value error, /, '').trim() : ''
    if (msg) return msg
  }
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message.trim()
  }
  return fallback
}
