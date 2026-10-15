/**
 * W03 AIH-04: the URL contract of the run's AI history (contract §9.2). Pure: every helper reads or returns a NEW
 * `URLSearchParams` and keeps every other key (`tab`, the Tests filters, a deep link's ids).
 *
 *   ai_version=<int>        the stored digest version being VIEWED; absent = "Current" (the live Analysis view).
 *                           Equal to the list's `latest_version` it is treated as Current (no snapshot).
 *   ai_compare=<int|live>   open the diff of that version against the viewed one; `live` = the current data.
 *   ai_baseline=<run uuid>  an explicit baseline for the previous-run card (else the server's default rule).
 *   report_version=<int>    unchanged and still honoured on the Analysis and Evidence tabs.
 *
 * "One picker drives both": choosing an AI version moves `report_version` to the decision report that version
 * cited (`applyAiVersion`), and choosing a report version on the Evidence tab moves `ai_version` to the digest that
 * cited it (`applyReportVersion`). An old `?report_version=` link maps to the version that published it
 * (`selectedDigestVersion`).
 */
import type { AIVersionSummary } from '@/types/aiHistory'

/** What the helpers need of a listed version. */
export type VersionLike = Pick<AIVersionSummary, 'version' | 'decision_report_ref'>

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function positiveInt(raw: string | null): number | null {
  if (raw == null || raw.trim() === '') return null
  const value = Number(raw)
  return Number.isInteger(value) && value > 0 ? value : null
}

/** `?report_version=`: the decision report version a link or a picker chose; null = the latest report. */
export function readReportVersion(params: URLSearchParams): number | null {
  return positiveInt(params.get('report_version'))
}

/** `?ai_version=`: a positive integer, else null (Current). */
export function readAiVersion(params: URLSearchParams): number | null {
  return positiveInt(params.get('ai_version'))
}

/** `?ai_compare=`: a positive integer, `live`, else null (no diff open). */
export function readAiCompare(params: URLSearchParams): number | 'live' | null {
  const raw = params.get('ai_compare')
  if (raw === 'live') return 'live'
  return positiveInt(raw)
}

/** `?ai_baseline=`: a uuid-shaped run id, else null (the server's default baseline). */
export function readAiBaseline(params: URLSearchParams): string | null {
  const raw = params.get('ai_baseline')?.trim()
  return raw && UUID.test(raw) ? raw : null
}

/** The digest version that cited decision report `reportVersion` (the newest when several did), else null. */
export function digestVersionForReport(versions: readonly VersionLike[], reportVersion: number): number | null {
  let found: number | null = null
  for (const v of versions) {
    if (v.decision_report_ref?.report_version === reportVersion && (found == null || v.version > found)) found = v.version
  }
  return found
}

/**
 * The digest version the page shows: an explicit `ai_version`, else the version whose decision report an old
 * `?report_version=` link names, else null (Current).
 */
export function selectedDigestVersion(
  versions: readonly VersionLike[],
  aiVersion: number | null,
  reportVersion: number | null,
): number | null {
  if (aiVersion != null) return aiVersion
  if (reportVersion != null) return digestVersionForReport(versions, reportVersion)
  return null
}

/** A stored snapshot is shown only for a version that is not the latest (the latest IS the current analysis). */
export function isSnapshotVersion(viewed: number | null, latestVersion: number | null | undefined): boolean {
  return viewed != null && viewed !== latestVersion
}

/**
 * The Analysis picker chose `version` (null = Current): set or clear `ai_version`, move `report_version` to the
 * decision report that version cited (cleared for Current or a version without one), close any open diff.
 */
export function applyAiVersion(
  params: URLSearchParams,
  version: number | null,
  versions: readonly VersionLike[],
): URLSearchParams {
  const next = new URLSearchParams(params)
  next.delete('ai_compare')
  if (version == null) {
    next.delete('ai_version')
    next.delete('report_version')
    return next
  }
  next.set('ai_version', String(version))
  const reportVersion = versions.find((v) => v.version === version)?.decision_report_ref?.report_version
  if (reportVersion != null && reportVersion > 0) next.set('report_version', String(reportVersion))
  else next.delete('report_version')
  return next
}

/**
 * The Evidence tab's report select chose `reportVersion` (null = the latest report): set or clear
 * `report_version`, and point `ai_version` at the digest that cited it (cleared when none did). A diff that was
 * open against another viewed version is closed.
 */
export function applyReportVersion(
  params: URLSearchParams,
  reportVersion: number | null,
  versions: readonly VersionLike[],
): URLSearchParams {
  const next = new URLSearchParams(params)
  const before = next.get('ai_version')
  if (reportVersion == null) {
    next.delete('report_version')
    next.delete('ai_version')
  } else {
    next.set('report_version', String(reportVersion))
    const digest = digestVersionForReport(versions, reportVersion)
    if (digest == null) next.delete('ai_version')
    else next.set('ai_version', String(digest))
  }
  if (next.get('ai_version') !== before) next.delete('ai_compare')
  return next
}

/** Open (or close, with null) the diff of `compare` against the viewed version. */
export function applyAiCompare(params: URLSearchParams, compare: number | 'live' | null): URLSearchParams {
  const next = new URLSearchParams(params)
  if (compare == null) next.delete('ai_compare')
  else next.set('ai_compare', String(compare))
  return next
}

/** Choose an explicit baseline run for the previous-run card (null = the server's default). */
export function applyAiBaseline(params: URLSearchParams, baselineRunId: string | null): URLSearchParams {
  const next = new URLSearchParams(params)
  if (baselineRunId && UUID.test(baselineRunId)) next.set('ai_baseline', baselineRunId)
  else next.delete('ai_baseline')
  return next
}

/** The Evidence tab showing decision report `reportVersion`, with the AI version that cited it. */
export function evidenceHref(runId: string, reportVersion: number, aiVersion: number): string {
  const params = new URLSearchParams()
  params.set('tab', 'evidence')
  params.set('report_version', String(reportVersion))
  params.set('ai_version', String(aiVersion))
  return `/runs/${encodeURIComponent(runId)}?${params.toString()}`
}

/** A test of a run (the test page). */
export function runTestHref(runId: string, testId: string): string {
  return `/runs/${encodeURIComponent(runId)}/tests/${encodeURIComponent(testId)}`
}
