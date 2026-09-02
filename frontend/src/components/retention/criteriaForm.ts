/**
 * Pure form → criteria translation for the S5 deletion builder.
 *
 * Separate from the component so it can be tested without a DOM, and because
 * a component file that exports non-components breaks Fast Refresh.
 */
import { NARROWING_FIELDS, type RetentionCriteria, type RunStatus } from '@/types/retention'

export interface CriteriaFormState {
  olderThanDays: string
  dateFrom: string
  dateTo: string
  statuses: RunStatus[]
  branches: string
  environments: string
  suiteNames: string
  suiteMatch: 'only' | 'any'
}

export const EMPTY_CRITERIA_FORM: CriteriaFormState = {
  olderThanDays: '',
  dateFrom: '',
  dateTo: '',
  statuses: [],
  branches: '',
  environments: '',
  suiteNames: '',
  suiteMatch: 'only',
}

/** Split a comma/newline separated field, dropping blanks. */
export function toList(raw: string): string[] {
  return raw
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

/**
 * Build the request body.
 *
 * Empty fields are OMITTED rather than sent as null: the backend treats a
 * present-but-empty list as a criterion that matches nothing, which would
 * select zero runs and report that as a successful narrowing.
 */
export function buildCriteria(form: CriteriaFormState): RetentionCriteria {
  const criteria: RetentionCriteria = {}

  const days = Number(form.olderThanDays)
  if (form.olderThanDays.trim() && Number.isFinite(days) && days > 0) {
    criteria.older_than_days = days
  }
  if (form.dateFrom) criteria.date_from = new Date(form.dateFrom).toISOString()
  if (form.dateTo) criteria.date_to = new Date(form.dateTo).toISOString()
  if (form.statuses.length) criteria.statuses = form.statuses

  const branches = toList(form.branches)
  if (branches.length) criteria.branches = branches

  const environments = toList(form.environments)
  if (environments.length) criteria.environments = environments

  const suites = toList(form.suiteNames)
  if (suites.length) {
    criteria.suite_names = suites
    // Only meaningful alongside suite_names; sending it alone would imply a
    // suite filter that is not there.
    criteria.suite_match = form.suiteMatch
  }

  return criteria
}

/**
 * True when the form narrows nothing.
 *
 * The backend refuses this with a 422 — a project id alone is a full project
 * purge, not a filtered deletion. Checking here too keeps an operator from
 * clicking Preview to be told they built nothing, and keeps the destructive
 * button disabled rather than merely erroring.
 */
export function narrowsNothing(criteria: RetentionCriteria): boolean {
  return !NARROWING_FIELDS.some((field) => {
    const value = criteria[field]
    return Array.isArray(value) ? value.length > 0 : value != null && value !== ''
  })
}
