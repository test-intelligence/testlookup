/**
 * Draft phases from a PRD (W11 REL-07) — the form's pure part.
 *
 * The server drafts (the heading parser always; the model when the
 * deployment allows it, falling back to the rules draft with a reason) and
 * stores NOTHING: these rows are what the reader edits, and only Create sends
 * them (one bulk request). Every string here is shown as React text — a PRD
 * heading or a model's answer is never HTML.
 */
import type { PhaseDraft, PhaseDraftResult, ReleasePhasesBulkBody } from '@/types/releases'

/** = the server's `PHASE_TYPES` and the New-phase form's options (`ReleasesPage.tsx`), pinned by the tests. */
export const PHASE_TYPE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'planning', label: 'Planning' },
  { value: 'development', label: 'Development' },
  { value: 'code_freeze', label: 'Code Freeze' },
  { value: 'qa_testing', label: 'QA Testing' },
  { value: 'uat', label: 'UAT' },
  { value: 'staging', label: 'Staging' },
  { value: 'production', label: 'Production' },
]

const PHASE_TYPE_VALUES = new Set(PHASE_TYPE_OPTIONS.map(o => o.value))

/** The badge's second half for a rules draft (= the server's `FALLBACK_REASON_TEXT`). */
export const FALLBACK_REASON_TEXT: Readonly<Record<string, string>> = {
  offline_mode: 'the deployment is offline (AI_OFFLINE_MODE)',
  rules_mode: 'AI analysis mode is set to rules',
  llm_unavailable: 'the AI model is not available',
  llm_timeout: 'the AI model did not answer in time',
  llm_error: 'the AI model call failed',
  llm_unparsable: "the AI model's answer could not be read",
  llm_empty: 'the AI model drafted no phases',
}

/** At most this many phases are created at once (the server's `MAX_BULK_PHASES`). */
export const MAX_DRAFT_ROWS = 30
/** A row without Days counts this many when "First phase starts" lays the windows out. */
export const DEFAULT_ROW_DAYS = 5
export const MIN_DAYS = 1
export const MAX_DAYS = 90
const DESCRIPTION_MAX = 4000
const DAY_MS = 86_400_000

export const NO_PHASES_DRAFTED = 'No phases were drafted.'

/** One editable row of the preview. `days` is the input's text ('' = none). */
export interface DraftRow {
  key: string
  name: string
  phaseType: string
  days: string
  description: string
}

let rowSeq = 0
function rowKey(): string {
  rowSeq += 1
  return `draft-row-${rowSeq}`
}

/** `Drafted by rules`, `Drafted by rules — the deployment is offline (AI_OFFLINE_MODE)`, `Drafted by qwen2.5:7b`. */
export function draftBadge(result: Pick<PhaseDraftResult, 'source' | 'model_name' | 'fallback_reason'>): string {
  if (result.source === 'llm') return `Drafted by ${result.model_name?.trim() || 'the AI model'}`
  const reason = result.fallback_reason ? FALLBACK_REASON_TEXT[result.fallback_reason] : undefined
  return reason ? `Drafted by rules — ${reason}` : 'Drafted by rules'
}

/** The drafted phases as editable rows: the description carries the exit criteria as `Exit criteria: …`. */
export function rowsFromDraft(phases: readonly PhaseDraft[]): DraftRow[] {
  return phases.slice(0, MAX_DRAFT_ROWS).map(p => ({
    key: rowKey(),
    name: p.name ?? '',
    phaseType: PHASE_TYPE_VALUES.has(p.phase_type) ? p.phase_type : 'qa_testing',
    days: p.planned_days != null ? String(p.planned_days) : '',
    description: [p.description?.trim(), p.exit_criteria_text?.trim() ? `Exit criteria: ${p.exit_criteria_text.trim()}` : null]
      .filter(Boolean)
      .join('\n'),
  }))
}

/** A blank row ("Add a row"). */
export function emptyRow(): DraftRow {
  return { key: rowKey(), name: '', phaseType: 'qa_testing', days: '', description: '' }
}

/** A row's Days as a number, `null` when blank; `NaN` when not a whole number from 1 to 90. */
export function rowDays(row: Pick<DraftRow, 'days'>): number | null {
  const text = row.days.trim()
  if (text === '') return null
  const n = Number(text)
  return Number.isInteger(n) && n >= MIN_DAYS && n <= MAX_DAYS ? n : Number.NaN
}

/**
 * Consecutive planned windows from `firstStart` (a UTC day): each row lasts
 * its Days (a row without them, `DEFAULT_ROW_DAYS`), the next starts the day
 * after. No `firstStart` → no dates.
 */
export function scheduleRows(
  rows: readonly Pick<DraftRow, 'days'>[],
  firstStart: string | null,
): Array<{ planned_start: string | null; planned_end: string | null }> {
  const start = firstStart && /^\d{4}-\d{2}-\d{2}$/.test(firstStart) ? Date.parse(`${firstStart}T00:00:00Z`) : Number.NaN
  if (!Number.isFinite(start)) return rows.map(() => ({ planned_start: null, planned_end: null }))
  let cursor = start
  return rows.map(row => {
    const days = rowDays(row)
    const length = days != null && Number.isFinite(days) ? days : DEFAULT_ROW_DAYS
    const end = cursor + (length - 1) * DAY_MS
    const window = {
      planned_start: new Date(cursor).toISOString().replace('.000Z', 'Z'),
      planned_end: new Date(end).toISOString().replace('.000Z', 'Z'),
    }
    cursor = end + DAY_MS
    return window
  })
}

/** Why Create is blocked, as a sentence; `null` = the rows can be created. */
export function validateRows(rows: readonly DraftRow[]): string | null {
  if (rows.length === 0) return 'Add at least one phase.'
  if (rows.length > MAX_DRAFT_ROWS) return `At most ${MAX_DRAFT_ROWS} phases can be created at once.`
  if (rows.some(r => r.name.trim() === '')) return 'Every phase needs a name.'
  const seen = new Map<string, string>()
  for (const row of rows) {
    const key = row.name.trim().toLowerCase()
    if (seen.has(key)) return `Two phases are named "${row.name.trim()}": each name must be different.`
    seen.set(key, row.name)
  }
  if (rows.some(r => Number.isNaN(rowDays(r)))) return `Days must be a whole number from ${MIN_DAYS} to ${MAX_DAYS}.`
  return null
}

/** Create's ONE request: the edited rows, their windows, and who drafted them. */
export function bulkBody(
  rows: readonly DraftRow[],
  result: Pick<PhaseDraftResult, 'source' | 'model_name'>,
  firstStart: string | null = null,
): ReleasePhasesBulkBody {
  const windows = scheduleRows(rows, firstStart)
  const fromModel = result.source === 'llm'
  return {
    phases: rows.map((row, i) => ({
      name: row.name.trim(),
      phase_type: row.phaseType,
      description: row.description.trim() ? row.description.trim().slice(0, DESCRIPTION_MAX) : null,
      planned_start: windows[i].planned_start,
      planned_end: windows[i].planned_end,
    })),
    source: fromModel ? 'llm' : 'rules',
    model_name: fromModel ? (result.model_name ?? null) : null,
  }
}

/** The success toast: `Created 3 phases from the draft.` */
export function createdToast(created: number): string {
  return `Created ${created} phase${created === 1 ? '' : 's'} from the draft.`
}

/** The Create button: `Create 3 phases`. */
export function createLabel(count: number): string {
  return `Create ${count} phase${count === 1 ? '' : 's'}`
}
