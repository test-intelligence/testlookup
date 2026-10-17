/**
 * NTF-03 / NTF-04: what a digest subscription covers, as the digests page
 * says it. Pure: no React, no request.
 *
 *  - Per Release names a release of the top bar project (`releaseOptions`
 *    orders them: the active release, then in-progress ones, then the rest in
 *    the list's order);
 *  - a row says what it covers (`subscriptionSummary`) and, when the server's
 *    `scope_problem` says it cannot deliver as configured, why
 *    (`scopeProblemText`);
 *  - the Saved Views tab lists the project's REPORT views and the legacy empty
 *    rows (`isListedView`), never the widget-layout rows;
 *  - the in-page view editor saves the report-view shape
 *    (`reportViewFilters`, `page: 'trends'`: Trends' scope is the digest's).
 *
 * Every server field read here goes through a guard: the enrichment
 * (`scope_label`, `scope_problem`, `saved_view_name`) may be missing or
 * malformed, and the row then renders without it.
 */
import type { DigestScheduleType, DigestScopeInfo, DigestSubscription } from '@/services/digestService'
import type { SavedView } from '@/services/savedViewsService'
import type { Release } from '@/types/releases'
import {
  VIEW_NAME_MAX,
  VIEW_NAME_MIN,
  isReportView,
  reportViewFilters,
  type SavedViewPage,
} from '@/components/reports/savedViewsModel'

// ── Schedules ─────────────────────────────────────────────────

export const SCHEDULE_LABELS: Record<DigestScheduleType, string> = {
  WEEKLY: 'Weekly',
  WEEKLY_RETRO: 'Weekly Retro',
  DAILY: 'Daily',
  PER_RUN: 'Per Run',
  PER_RELEASE: 'Per Release',
  PER_SUITE: 'Per Suite',
}

export function scheduleLabel(schedule: unknown): string {
  if (typeof schedule !== 'string') return ''
  return SCHEDULE_LABELS[schedule as DigestScheduleType] ?? schedule.replace(/_/g, ' ')
}

/** The schedules whose CONTENT a saved view filters (the server's `VIEW_SCHEDULES`). */
export const VIEW_SCHEDULES: readonly DigestScheduleType[] = ['DAILY', 'WEEKLY']

export const viewAppliesTo = (schedule: unknown): boolean =>
  typeof schedule === 'string' && (VIEW_SCHEDULES as readonly string[]).includes(schedule)

// ── Releases ──────────────────────────────────────────────────

/** A release the select can offer: an id and a name, whatever else the list carried. */
function isOfferable(r: unknown): r is Release {
  return (
    typeof r === 'object' && r !== null &&
    typeof (r as Release).id === 'string' && (r as Release).id !== '' &&
    typeof (r as Release).name === 'string'
  )
}

/** The active release first, then in-progress ones, then the rest, each in the list's order. */
export function releaseOptions(releases: readonly unknown[] | null | undefined): Release[] {
  if (!Array.isArray(releases)) return []
  const rank = (r: Release) => (r.is_active === true ? 0 : r.status === 'in_progress' ? 1 : 2)
  return releases
    .filter(isOfferable)
    .map((r, i) => ({ r, i }))
    .sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i)
    .map(({ r }) => r)
}

// ── A subscription's scope ────────────────────────────────────

export type ProblemKind = 'delivery' | 'view'

export interface SubscriptionScope {
  /** The release's name (Per Release) or the suite (Per Suite), from the server. */
  label: string | null
  /** The server's `scope_problem`, a plain string, or null. */
  problem: string | null
  /** The bound view's name snapshot, or null. */
  viewName: string | null
}

const nonEmpty = (v: unknown): string | null => (typeof v === 'string' && v.trim() !== '' ? v : null)

/** The defensive guard over a row's enrichment fields: anything malformed reads as absent. */
export function parseSubscriptionScope(sub: unknown): SubscriptionScope {
  if (typeof sub !== 'object' || sub === null) return { label: null, problem: null, viewName: null }
  const s = sub as Record<string, unknown>
  return { label: nonEmpty(s.scope_label), problem: nonEmpty(s.scope_problem), viewName: nonEmpty(s.saved_view_name) }
}

/** What the row says when it cannot deliver as configured; null for no (or an unknown) problem. */
export function scopeProblemText(problem: string | null | undefined, schedule?: unknown): string | null {
  switch (problem) {
    case 'unscoped':
      if (schedule === 'PER_RELEASE') return 'Not delivering — choose a release'
      if (schedule === 'PER_SUITE') return 'Not delivering — choose a suite'
      return null
    case 'release_missing':
      return 'Not delivering — its release no longer exists'
    case 'saved_view_deleted':
      return 'View deleted — this digest covers the whole project'
    case 'saved_view_unavailable':
      return 'View no longer available to you — the whole project'
    default:
      return null
  }
}

/** Which fix a problem needs: the scope (release / suite) or the view. */
export function problemKind(problem: string | null | undefined, schedule?: unknown): ProblemKind | null {
  if (problem === 'unscoped') return schedule === 'PER_RELEASE' || schedule === 'PER_SUITE' ? 'delivery' : null
  if (problem === 'release_missing') return schedule === 'PER_RELEASE' ? 'delivery' : null
  if (problem === 'saved_view_deleted' || problem === 'saved_view_unavailable') return 'view'
  return null
}

/**
 * The row's summary line: `Per Release · 2026.10 via email · View: Checkout
 * regressions · Delivered: 3 times · Next: …`. A Per Release row never shows
 * a raw release id; a legacy row's other scope value still reads
 * `Scope: …`. Plain text (React renders it).
 */
export function subscriptionSummary(sub: DigestSubscription): string {
  const scope = parseSubscriptionScope(sub)
  const parts: string[] = [scheduleLabel(sub.schedule)]
  if (scope.label) parts.push(scope.label)
  else if (sub.schedule === 'PER_SUITE' && nonEmpty(sub.scope_value)) parts.push(String(sub.scope_value))
  else if (sub.schedule !== 'PER_RELEASE' && nonEmpty(sub.scope_value)) parts.push(`Scope: ${sub.scope_value}`)
  let line = `${parts.join(' · ')} via ${sub.channel}`
  const viewProblem = problemKind(scope.problem, sub.schedule) === 'view'
  if (sub.saved_view_id && scope.viewName && !viewProblem) line += ` · View: ${scope.viewName}`
  if (sub.trigger_filter && sub.trigger_filter !== 'all') line += ` · ${sub.trigger_filter.replace('_', ' ')}`
  if (sub.report_attachment) line += ' · Report attached'
  line += ` · Delivered: ${sub.delivery_count} times`
  if (sub.next_delivery_at) line += ` · Next: ${new Date(sub.next_delivery_at).toLocaleDateString()}`
  return line
}

// ── Saved views ───────────────────────────────────────────────

/** A report view, or a legacy row the old "Save View" made (`filters` `{}`); never a widget layout. */
export function isListedView(view: Pick<SavedView, 'filters'>): boolean {
  if (isReportView(view)) return true
  const f = view.filters
  return typeof f === 'object' && f !== null && !Array.isArray(f) && Object.keys(f).length === 0
}

/**
 * The views a digest of `projectId` may name: report views OF that project.
 * The list for a project also carries the reader's own project-less rows,
 * which the server refuses as a digest's view (`BAD_VIEW`).
 */
export function pickableViews<T extends Pick<SavedView, 'filters' | 'project_id'>>(
  views: readonly T[],
  projectId: string | null | undefined,
): T[] {
  if (!projectId) return []
  return views.filter(v => isReportView(v) && v.project_id === projectId)
}

const PAGE_LABELS: Record<string, string> = {
  dashboard: 'Overview',
  trends: 'Trends',
  coverage: 'Coverage',
  failures: 'Failures',
  defects: 'Defects',
  summary_report: 'Summary',
  explore: 'Explore',
}

/** `Saved on Trends`, from the view's page; null when it has none. */
export function viewPageText(view: Pick<SavedView, 'page'>): string | null {
  const page: unknown = view.page
  if (typeof page !== 'string' || page === '') return null
  return `Saved on ${PAGE_LABELS[page] ?? page}`
}

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string' && s.trim() !== '') : []

const windowOf = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= 365 ? v : null)

export const daysText = (n: number) => `${n} day${n === 1 ? '' : 's'}`

/**
 * A listed view's scope: `Releases: 2026.10 · Suites: checkout · Last 7 days`
 * (`Any release`, `Any suite` when absent). Release names come from the
 * project's list; an id it does not hold is counted, never shown raw.
 */
export function viewScopeText(view: Pick<SavedView, 'filters'>, releases: readonly Release[]): string {
  if (!isReportView(view)) return 'No filters — edit to add some'
  const f = view.filters
  const ids = strings(f.release_ids)
  const byId = new Map(releases.map(r => [r.id, r.name]))
  const names = ids.map(id => byId.get(id)).filter((n): n is string => typeof n === 'string')
  const unknown = ids.length - names.length
  let rel = names.length ? `Releases: ${names.join(', ')}` : ids.length ? 'Releases:' : 'Any release'
  // "2026.10, plus 2 not in this project": the known names first, then how many
  // ids no longer resolve here (a bare "+2" read as if all were missing).
  if (unknown > 0) rel += `${names.length ? ', plus ' : ' '}${unknown} not in this project`
  const suites = strings(f.suites)
  const parts = [rel, suites.length ? `Suites: ${suites.join(', ')}` : 'Any suite']
  const win = windowOf(f.window)
  if (win !== null) parts.push(`Last ${daysText(win)}`)
  return parts.join(' · ')
}

// ── A digest's scope, as the preview (and a delivered digest) says it ──

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

/** `release 2026.10 · suite checkout · last 7 days`; empty when the view filters nothing. */
export function scopeDetail(scope: DigestScopeInfo): string {
  const parts: string[] = []
  if (scope.release_names.length) parts.push(`${plural(scope.release_names.length, 'release', 'releases')} ${scope.release_names.join(', ')}`)
  if (scope.suites.length) parts.push(`${plural(scope.suites.length, 'suite', 'suites')} ${scope.suites.join(', ')}`)
  if (scope.window_days) parts.push(`last ${daysText(scope.window_days)}`)
  return parts.join(' · ')
}

/** The block's first line; null for a project-wide (or unknown) source. */
export function scopeHeadline(scope: DigestScopeInfo): string | null {
  switch (scope.source) {
    case 'saved_view': {
      const detail = scopeDetail(scope)
      const name = scope.view_name ?? 'a saved view'
      return detail ? `Filtered by view: ${name} — ${detail}` : `Filtered by view: ${name}`
    }
    case 'saved_view_deleted':
      return 'View deleted — this digest covers the whole project'
    case 'saved_view_unavailable':
      return 'View no longer available to you — the whole project'
    default:
      return null
  }
}

/** The server's `DigestScope.is_filtered`, for a view that applied. */
export const isFilteredScope = (scope: DigestScopeInfo): boolean =>
  scope.source === 'saved_view' && (scope.release_ids.length > 0 || scope.suites.length > 0 || !!scope.window_days)

export function droppedNote(n: number): string | null {
  if (!Number.isInteger(n) || n <= 0) return null
  return n === 1
    ? '1 release in this view no longer exists in this project and was left out.'
    : `${n} releases in this view no longer exist in this project and were left out.`
}

export const FILTERED_NOTE = 'Project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest.'

// ── The in-page view editor ───────────────────────────────────

export const VIEW_WINDOWS: readonly number[] = [1, 7, 14, 30, 90]
export const DEFAULT_VIEW_WINDOW = 7
export const MAX_VIEW_SUITES = 50

export interface ViewDraft {
  name: string
  releaseIds: string[]
  /** As typed: comma-separated. */
  suitesText: string
  windowDays: number
  isShared: boolean
}

export const EMPTY_DRAFT: ViewDraft = { name: '', releaseIds: [], suitesText: '', windowDays: DEFAULT_VIEW_WINDOW, isShared: false }

/** Comma-separated suites, each trimmed; empty entries dropped; the first of a repeat kept. */
export function parseSuites(text: string): string[] {
  const out: string[] = []
  for (const raw of String(text ?? '').split(',')) {
    const s = raw.trim()
    if (s && !out.includes(s)) out.push(s)
  }
  return out
}

/** A listed view as the editor's draft. A legacy row opens empty (with its name). */
export function draftFromView(view: Pick<SavedView, 'name' | 'filters' | 'is_shared'>, releases: readonly Release[]): ViewDraft {
  const base = { ...EMPTY_DRAFT, name: typeof view.name === 'string' ? view.name : '', isShared: view.is_shared === true }
  if (!isReportView(view)) return base
  const known = new Set(releases.map(r => r.id))
  const win = windowOf(view.filters.window)
  return {
    ...base,
    // What the editor shows is what it saves: an id the project no longer lists is not kept.
    releaseIds: strings(view.filters.release_ids).filter(id => known.has(id)),
    suitesText: strings(view.filters.suites).join(', '),
    windowDays: win !== null && VIEW_WINDOWS.includes(win) ? win : DEFAULT_VIEW_WINDOW,
  }
}

export interface DraftErrors {
  name?: string
  suites?: string
  window?: string
}

export function validateDraft(draft: ViewDraft): DraftErrors {
  const errors: DraftErrors = {}
  const name = draft.name.trim()
  if (name.length < VIEW_NAME_MIN || name.length > VIEW_NAME_MAX) {
    errors.name = `A name needs ${VIEW_NAME_MIN} to ${VIEW_NAME_MAX} characters.`
  }
  if (parseSuites(draft.suitesText).length > MAX_VIEW_SUITES) errors.suites = `At most ${MAX_VIEW_SUITES} suites.`
  if (!VIEW_WINDOWS.includes(draft.windowDays)) errors.window = 'Choose 1, 7, 14, 30 or 90 days.'
  return errors
}

export const hasErrors = (e: DraftErrors) => Boolean(e.name || e.suites || e.window)

/** The saved-views body of a draft: the report-view shape (`kind: report_view`). */
export function viewPayload(draft: ViewDraft, page: SavedViewPage = 'trends') {
  return {
    name: draft.name.trim(),
    page,
    is_shared: draft.isShared,
    filters: reportViewFilters(page, {
      releaseIds: [...draft.releaseIds],
      suiteNames: parseSuites(draft.suitesText),
      windowDays: draft.windowDays,
    }),
  }
}
