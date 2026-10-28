/**
 * scheduleModel — the pure copy and helpers of the purge schedule (window,
 * pause) and of the per-field inheritance markers (W22b ACT-06).
 *
 * Every sentence the Retention page's schedule field, its inherited markers
 * and the Instance defaults card show lives here, so a test imports one
 * string instead of re-typing it. Pure functions only: no React, no request,
 * no `Intl` (dates are built from the formatters or by hand in UTC).
 *
 * Inputs are structural (`MarkerSource`, `ScheduleSource`) so the policy GET
 * (`RetentionPolicy`), a test fixture and the instance card's own object are
 * all accepted without a cast. Absence is not a value: a policy without
 * `field_sources` (an older server, an older fixture) gets NO marker at all.
 *
 * The wire vocabulary (the four sentence templates, the inheritable fields,
 * the pause length, the default window) is NOT re-declared here: it is
 * re-exported from `@/types/retentionExport`, the one mirror of
 * `retention_vocab.py`.
 */
import {
  DEFAULT_LABEL,
  INHERITABLE_FIELDS,
  INHERITED_LABEL,
  NEXT_PURGE_SENTENCE,
  PAUSED_SENTENCE,
  PAUSE_DAYS_DEFAULT,
  PURGE_WINDOW_DEFAULT_HOUR,
  type FieldSource,
  type InheritableField,
} from '@/types/retentionExport'
import { formatDay } from '@/utils/formatters'

export { INHERITABLE_FIELDS, PAUSE_DAYS_DEFAULT, PURGE_WINDOW_DEFAULT_HOUR }
/** The seven fields a project may inherit; where a value comes from. */
export type { FieldSource, InheritableField }

/** How a field's value reads in a marker. */
export type MarkerUnit = 'switch' | 'days' | 'runs' | 'hour'

export const FIELD_UNITS: Readonly<Record<InheritableField, MarkerUnit>> = Object.freeze({
  enabled: 'switch',
  raw_events_days: 'days',
  runs_days: 'days',
  artifacts_days: 'days',
  audit_days: 'days',
  keep_last_runs: 'runs',
  purge_window_start_utc: 'hour',
})

export const WINDOW_FIELD_LABEL = 'Purge window (UTC)'
export const WINDOW_FIELD_HELP =
  'The hourly purge runs for this project only in this hour. 02:00 is the default.'
export const PROJECT_VALUE_LABEL = 'Project value'
export const OVERRIDE_LABEL = 'Override'
export const RESET_TO_INHERITED_LABEL = 'Reset to inherited'
/** An inherited field overridden, not yet saved. */
export const OVERRIDDEN_LINE = 'Project value on save'
/** A project value reset to inherited, not yet saved. */
export const RESET_LINE = 'Inherited value on save'
export const SCHEDULE_OFF_SENTENCE = 'Scheduled purging is off for this project.'
export const PAUSE_LABEL = `Pause ${PAUSE_DAYS_DEFAULT} days`
export const RESUME_LABEL = 'Resume'
export const PAUSED_CHIP_LABEL = 'Paused'
export const DIRTY_TITLE = 'Save or discard your changes first'
export const SWITCH_ON_LABEL = 'On'
export const SWITCH_OFF_LABEL = 'Off'

/** `'03:00'` for 3 — the label of one window hour. */
export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`
}

/** The 24 window hours, `00:00` … `23:00` (the page's and the instance card's select). */
export const HOURS: ReadonlyArray<{ value: number; label: string }> = Object.freeze(
  Array.from({ length: 24 }, (_, hour) => ({ value: hour, label: hourLabel(hour) })),
)

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** A field value as a marker reads it: `180 days`, `10 runs`, `02:00 UTC`, `On`. */
export function formatFieldValue(value: boolean | number, unit: MarkerUnit): string {
  if (unit === 'switch') return value ? SWITCH_ON_LABEL : SWITCH_OFF_LABEL
  const n = Number(value)
  if (unit === 'hour') return `${hourLabel(n)} UTC`
  if (unit === 'runs') return plural(n, 'run', 'runs')
  return plural(n, 'day', 'days')
}

/** What a marker reads from a policy: the effective values plus the two W22b maps. */
export type MarkerSource = {
  field_sources?: Partial<Record<string, string>> | null
  inherit_values?: Partial<Record<string, boolean | number>> | null
} & Partial<Record<InheritableField, boolean | number | null>>

/** The field's source, or null when the server sent none for it (no marker). */
export function fieldSource(field: InheritableField, policy: MarkerSource): FieldSource | null {
  const source = policy.field_sources?.[field]
  return source === 'project' || source === 'instance' || source === 'default' ? source : null
}

/**
 * The marker text of one field: `180 days (instance default)`,
 * `90 days (default)`, `Project value` — null when the policy carries no
 * source for the field (an older server shows nothing). The inherited value
 * is the effective one (what the disabled input shows), else `inherit_values`.
 */
export function markerLine(
  field: InheritableField,
  policy: MarkerSource,
  unit: MarkerUnit = FIELD_UNITS[field],
): string | null {
  const source = fieldSource(field, policy)
  if (source === null) return null
  if (source === 'project') return PROJECT_VALUE_LABEL
  const value = policy[field] ?? policy.inherit_values?.[field]
  if (value == null) return null
  const template = source === 'instance' ? INHERITED_LABEL : DEFAULT_LABEL
  return template.replace('{value}', formatFieldValue(value, unit))
}

/** What `Reset to inherited` puts in the input; null when the server did not say. */
export function inheritedValue(field: InheritableField, policy: MarkerSource): boolean | number | null {
  return policy.inherit_values?.[field] ?? null
}

// ── Dates ────────────────────────────────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const NO_DATE = '—'

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

const two = (n: number): string => String(n).padStart(2, '0')

/** `'Oct 29, 2026, 03:00 UTC'` — the window is a UTC hour, so its next run reads in UTC. */
export function formatUtcDateTime(iso: string | null | undefined): string {
  const d = parseDate(iso)
  if (!d) return NO_DATE
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}, ${two(d.getUTCHours())}:${two(d.getUTCMinutes())} UTC`
}

/** `'Nov 4, 2026, 14:00'` — the viewer's local date-time (the pause end). */
export function formatLocalDateTime(iso: string | null | undefined): string {
  const d = parseDate(iso)
  if (!d) return NO_DATE
  return `${formatDay(d)}, ${two(d.getHours())}:${two(d.getMinutes())}`
}

export type DateZone = 'local' | 'utc'
export type ScheduleDateFormat = (iso: string, zone: DateZone) => string

const defaultScheduleFormat: ScheduleDateFormat = (iso, zone) =>
  zone === 'utc' ? formatUtcDateTime(iso) : formatLocalDateTime(iso)

// ── The status line ──────────────────────────────────────────────────────────

/** What the schedule status line reads from the policy GET. */
export type ScheduleSource = {
  enabled?: boolean | null
  purge_paused_until?: string | null
  next_scheduled_purge_at?: string | null
}

export type ScheduleState = 'paused' | 'next' | 'off' | 'unknown'

export interface ScheduleLine {
  state: ScheduleState
  /** null for `unknown` (enabled, but the server sent no next purge). */
  text: string | null
}

/**
 * The schedule field's status line: `Paused until <local date-time>` while
 * paused (the server sends `purge_paused_until` only while it is in the
 * future), else `Next scheduled purge <UTC date-time>`, else — purging off —
 * `Scheduled purging is off for this project.`.
 */
export function scheduleLine(
  policy: ScheduleSource,
  format: ScheduleDateFormat = defaultScheduleFormat,
): ScheduleLine {
  if (policy.purge_paused_until) {
    return {
      state: 'paused',
      text: PAUSED_SENTENCE.replace('{date}', format(policy.purge_paused_until, 'local')),
    }
  }
  if (policy.next_scheduled_purge_at) {
    return {
      state: 'next',
      text: NEXT_PURGE_SENTENCE.replace('{date}', format(policy.next_scheduled_purge_at, 'utc')),
    }
  }
  if (!policy.enabled) return { state: 'off', text: SCHEDULE_OFF_SENTENCE }
  return { state: 'unknown', text: null }
}

// ── Inherit / override in the policy form ────────────────────────────────────

/** `own` — the input is the project's value; `inherit` — disabled, reads the inherited value. */
export type FieldMode = 'own' | 'inherit'
export type FieldModes = Partial<Record<InheritableField, FieldMode>>

const FIELD_ORDER: readonly InheritableField[] = INHERITABLE_FIELDS

/** The saved modes: `project` → own, `instance` / `default` → inherit; `{}` without sources. */
export function modesFromPolicy(policy: MarkerSource): FieldModes {
  const modes: FieldModes = {}
  for (const field of FIELD_ORDER) {
    const source = fieldSource(field, policy)
    if (source !== null) modes[field] = source === 'project' ? 'own' : 'inherit'
  }
  return modes
}

export interface InheritPlan {
  /** Project values reset to inherited: sent in the PUT's `inherit` list, never as a value. */
  inherit: InheritableField[]
  /** Inherited fields the admin overrode: sent with their value even when it is unchanged. */
  override: InheritableField[]
  /** Fields still inherited: never sent. */
  inherited: InheritableField[]
}

/**
 * The `inherit` list and the per-field send rules `diffPayload` applies, from
 * the form's modes against the saved policy's. A field the server gave no
 * source for is in none of the lists (today's diff applies to it).
 */
export function inheritPayload(form: { modes: FieldModes }, saved: MarkerSource): InheritPlan {
  const savedModes = modesFromPolicy(saved)
  const plan: InheritPlan = { inherit: [], override: [], inherited: [] }
  for (const field of FIELD_ORDER) {
    const was = savedModes[field]
    if (was === undefined) continue
    const now = form.modes[field] ?? was
    if (was === 'inherit' && now === 'inherit') plan.inherited.push(field)
    else if (was === 'inherit') plan.override.push(field)
    else if (now === 'inherit') plan.inherit.push(field)
  }
  return plan
}
