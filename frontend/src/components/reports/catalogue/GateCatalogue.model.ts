/**
 * The pure half of the Release gate's "Context" group (`GateCatalogue.tsx`):
 * the words every caption uses, which releases are compared, how the run's
 * payload is read, and what a failed side request shows. No React, no
 * requests — so each rule is tested on its own (plan 2.5).
 */
import { classifyChartError, type ChartResponse, type ChartState } from '@/components/charts/chartState'
import type { Release } from '@/types/releases'
import { CATALOGUE_MAX_WINDOW_DAYS } from './catalogueScope'
import { displayText } from './catalogueAdapters'

// ── Words ────────────────────────────────────────────────────────────────────

// The group heading, its note and the placeholder height live in a
// dependency-free leaf: the page's Suspense fallback draws them before this
// module's chunk arrives (R1-7), and this module reaches chart code.
export { CONTEXT_HEADING, contextNote, RELEASES_PLACEHOLDER_HEIGHT } from './gateContextWords'
export const RELEASES_TITLE = 'Pass rate by release'
export const CLUSTERS_TITLE = 'Failure cluster share'

/** Releases compared at most: the run's own plus four others (or five others when it has none). */
export const MAX_COMPARED_RELEASES = 5

/** The comparison's window, days: fixed, never the page's (the gate has none). */
export const RELEASE_WINDOW_DAYS = CATALOGUE_MAX_WINDOW_DAYS

/** Rule 3 for the live chart: when, and that the verdict is not this chart. */
export function liveCaption(asOf: string | null, build: string): string {
  const when = asOf ? `Live data as of ${formatAsOf(asOf)}.` : 'Live data.'
  return `${when} The verdict above is the stored decision for build ${build}; it is not computed from this chart.`
}

/** Rule 3 for the stored chart: its data IS the decision's, and nothing is recomputed. */
export function storedCaption(build: string): string {
  return `Stored data: the failure clusters recorded with the decision for build ${build}. Nothing here is recomputed, and the verdict above does not change with it.`
}

/** Rule 5: which population the comparison counts, beside a verdict that counts another. */
export const RELEASES_POPULATION =
  "Pass rate of test executions per release, by day since each release's start. The verdict's pass rate is this run's stored snapshot, not these lines."

export const NOT_ATTRIBUTED_NOTE =
  "This run is not attributed to a release, so the chart compares the project's most recent releases."

/** Rule 6: why there is no comparison, in one sentence. */
export function tooFewNote(count: number, attributed: boolean): string {
  const had = count === 0 ? 'none has' : 'only one has'
  const need = `needs at least two releases with runs in the last ${RELEASE_WINDOW_DAYS} days; ${had} any.`
  return attributed
    ? `A release comparison ${need}`
    : `This run is not attributed to a release, and a comparison of the project's releases ${need}`
}

/** An ISO time as `2026-09-14 08:05 UTC`; an unparseable value as the text it is. */
export function formatAsOf(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return displayText(iso, 'an unknown time')
  const utc = date.toISOString()
  return `${utc.slice(0, 10)} ${utc.slice(11, 16)} UTC`
}

// ── The run, read once ───────────────────────────────────────────────────────

/** What the gate needs from `GET /runs/{id}`: which release, which project. */
export interface GateRun {
  releaseId: string | null
  releaseName: string | null
  projectId: string | null
}

const text = (raw: unknown): string | null => (typeof raw === 'string' && raw.trim() !== '' ? raw : null)

/** The payload read defensively: a missing or non-string field is `null`, never a guess. */
export function readGateRun(payload: unknown): GateRun {
  const body = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>
  return { releaseId: text(body.release_id), releaseName: text(body.release_name), projectId: text(body.project_id) }
}

// ── Which releases to compare ────────────────────────────────────────────────

export interface ComparisonPlan {
  /** Requested release ids: the run's first, then the most recent others. */
  releaseIds: string[]
  /** Day 0 per release id, when the release has a start date. */
  starts: Record<string, string>
  /** Display names per release id (hostile: text only). */
  names: Record<string, string>
  /** Whether the run itself has a release. */
  attributed: boolean
}

/** A release's start: released, else planned; `YYYY-MM-DD`, or `null`. */
function startOf(release: Pick<Release, 'released_at' | 'planned_date'>): string | null {
  const raw = release.released_at ?? release.planned_date
  return typeof raw === 'string' && /^\d{4}-\d{2}-\d{2}/.test(raw) ? raw.slice(0, 10) : null
}

/** How recent a release is: its start, else when it was created. */
function recency(release: Release): string {
  return startOf(release) ?? (typeof release.created_at === 'string' ? release.created_at.slice(0, 10) : '')
}

/**
 * The releases to ask about. Only this run's project's releases; a release
 * the list says has NO runs (`test_run_count === 0`) is left out, since it can
 * only draw nothing; newest first by start date (else creation date), ties by
 * name then id so the pick is stable.
 */
export function planReleaseComparison(run: GateRun, releases: readonly Release[], projectId: string | null): ComparisonPlan {
  const ofProject = releases.filter(
    (release) => (projectId === null || release.project_id === projectId) && release.test_run_count !== 0,
  )
  const others = ofProject
    .filter((release) => release.id !== run.releaseId)
    .sort(
      (a, b) =>
        (recency(a) < recency(b) ? 1 : recency(a) > recency(b) ? -1 : 0) ||
        (a.name < b.name ? -1 : a.name > b.name ? 1 : 0) ||
        (a.id < b.id ? -1 : 1),
    )
  const attributed = run.releaseId !== null
  const picked = others.slice(0, attributed ? MAX_COMPARED_RELEASES - 1 : MAX_COMPARED_RELEASES)

  // Keyed by release id, which is server text: without a prototype, an id of
  // `__proto__` or `constructor` is an entry like any other (on `{}` the first
  // is swallowed by the prototype setter and the second reads `Object`).
  const starts: Record<string, string> = Object.create(null)
  const names: Record<string, string> = Object.create(null)
  const releaseIds: string[] = []
  if (attributed) {
    const own = releases.find((release) => release.id === run.releaseId)
    releaseIds.push(run.releaseId as string)
    names[run.releaseId as string] = displayText(own?.name ?? run.releaseName, '(unnamed release)')
    const start = own ? startOf(own) : null
    if (start) starts[run.releaseId as string] = start
  }
  for (const release of picked) {
    releaseIds.push(release.id)
    names[release.id] = displayText(release.name, '(unnamed release)')
    const start = startOf(release)
    if (start) starts[release.id] = start
  }
  return { releaseIds, starts, names, attributed }
}

/** Series that measured at least one day: the releases that had runs in the window. */
export function measuredSeriesCount(response: ChartResponse): number {
  if (response.series.kind !== 'series') return 0
  return response.series.series.filter((series) => series.points.some((point) => point.y !== null)).length
}

// ── Side requests ────────────────────────────────────────────────────────────

/** A non-data state from a failed side request (the run, the release list). */
export function sideRequestState(error: unknown, retry: () => void): ChartState<ChartResponse> {
  const classified = classifyChartError(error)
  if (classified.type === 'error') return { status: 'error', error: classified.error, retry }
  if (classified.type === 'forbidden') return { status: 'forbidden', requestId: classified.requestId }
  return { status: 'loading' }
}
