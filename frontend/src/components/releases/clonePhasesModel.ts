/**
 * Copy phases from another release (W11 REL-05) — the form's pure part.
 *
 * The sources come from the release list the page already holds (every item
 * carries its `phases`): the target's project only, the target excluded,
 * releases without phases excluded. The shift is the server's rule
 * (`release_service.clone_phases`): the earliest planned START among the
 * source's phases (else its earliest planned end) moves to "First phase
 * starts", and every planned date moves by the same number of days; an
 * undated phase stays undated. Dates are UTC days: the date input's day is
 * sent as 00:00 UTC.
 */
import type { Release, ReleasePhase } from '@/types/releases'

const DAY_MS = 86_400_000
const IN_PROGRESS = new Set(['in_progress', 'in-progress', 'active', 'running'])

/** The list item as the server serialises it: the cutoff start rides along though `Release` does not name it. */
export type ReleaseWithCutoff = Release & { cutoff_start_at?: string | null }

export interface CloneSource {
  release: Release
  /** `2026.10 · 4 phases` */
  label: string
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

function time(iso: string | null | undefined): number | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isFinite(t) ? t : null
}

/** A UTC day `YYYY-MM-DD` as its 00:00 UTC instant (ms), or `null`. */
export function dayStart(day: string | null | undefined): number | null {
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null
  const t = Date.parse(`${day}T00:00:00Z`)
  return Number.isFinite(t) ? t : null
}

/** An instant's UTC day, `YYYY-MM-DD`. */
export function utcDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

/** The 00:00 UTC instant of an instant's day. */
function midnight(ms: number): number {
  return Math.floor(ms / DAY_MS) * DAY_MS
}

/** The release's phases in their order (`order_index`, then creation). */
export function sortedPhases(release: Pick<Release, 'phases'>): ReleasePhase[] {
  return [...(release.phases ?? [])].sort(
    (a, b) => (a.order_index ?? 0) - (b.order_index ?? 0) || String(a.created_at).localeCompare(String(b.created_at)),
  )
}

/**
 * The releases a target can copy from: the same project's OTHER releases that
 * have phases; the active and in-progress ones first, then by planned date,
 * newest first.
 */
export function cloneSources(target: Pick<Release, 'id' | 'project_id'>, releases: readonly Release[]): CloneSource[] {
  const rank = (r: Release) => (r.is_active ? 0 : IN_PROGRESS.has((r.status ?? '').toLowerCase()) ? 1 : 2)
  return releases
    .filter(r => r.project_id === target.project_id && r.id !== target.id && (r.phases?.length ?? 0) > 0)
    .sort((a, b) =>
      rank(a) - rank(b)
      || (time(b.planned_date) ?? -Infinity) - (time(a.planned_date) ?? -Infinity)
      || a.name.localeCompare(b.name))
    .map(release => ({ release, label: `${release.name} · ${plural(release.phases.length, 'phase')}` }))
}

/** The instant the shift moves: the earliest planned start, else the earliest planned end (the server's anchor). */
export function sourceAnchor(phases: readonly ReleasePhase[]): number | null {
  const starts = phases.map(p => time(p.planned_start)).filter((t): t is number => t != null)
  if (starts.length) return Math.min(...starts)
  const ends = phases.map(p => time(p.planned_end)).filter((t): t is number => t != null)
  return ends.length ? Math.min(...ends) : null
}

/** Whether any source phase has a planned date (the form asks for "First phase starts" only then). */
export function hasDatedPhases(source: Pick<Release, 'phases'>): boolean {
  return sourceAnchor(source.phases ?? []) != null
}

/** The source's span in whole days, from its anchor to its latest planned date. */
export function sourceSpanDays(phases: readonly ReleasePhase[]): number {
  const anchor = sourceAnchor(phases)
  if (anchor == null) return 0
  const all = phases.flatMap(p => [time(p.planned_start), time(p.planned_end)]).filter((t): t is number => t != null)
  return Math.round((Math.max(...all) - anchor) / DAY_MS)
}

/**
 * "First phase starts", prefilled: the target's cutoff start, else its
 * planned date minus the source's span (so the copied schedule ends on it),
 * else today. A UTC day.
 */
export function defaultFirstStart(target: ReleaseWithCutoff, source: Pick<Release, 'phases'>, today: Date): string {
  const cutoff = time(target.cutoff_start_at)
  if (cutoff != null) return utcDay(cutoff)
  const planned = time(target.planned_date)
  if (planned != null) return utcDay(planned - sourceSpanDays(source.phases ?? []) * DAY_MS)
  return utcDay(today.getTime())
}

export interface ClonePreviewRow {
  id: string
  name: string
  phaseType: string
  /** Calendar days the planned window covers (both ends included); `null` when undated. */
  days: number | null
  /** The moved planned dates, UTC days. */
  start: string | null
  end: string | null
}

/** The source's phases as they will be copied: dates moved so the first phase starts on `firstStart`. */
export function shiftedPreview(source: Pick<Release, 'phases'>, firstStart: string | null): ClonePreviewRow[] {
  const phases = sortedPhases(source)
  const anchor = sourceAnchor(phases)
  const target = dayStart(firstStart)
  const delta = anchor != null && target != null ? target - anchor : 0
  return phases.map(p => {
    const start = time(p.planned_start)
    const end = time(p.planned_end)
    const first = start ?? end
    const last = end ?? start
    return {
      id: p.id,
      name: p.name,
      phaseType: p.phase_type,
      days: first != null && last != null
        ? Math.max(1, Math.round((midnight(last) - midnight(first)) / DAY_MS) + 1)
        : null,
      start: start != null ? utcDay(start + delta) : null,
      end: end != null ? utcDay(end + delta) : null,
    }
  })
}

/** The request's `shift_to`: the chosen day at 00:00 UTC, or `null` (no dated phase: nothing to move). */
export function shiftTo(source: Pick<Release, 'phases'>, firstStart: string | null): string | null {
  if (!hasDatedPhases(source)) return null
  return dayStart(firstStart) != null ? `${firstStart}T00:00:00Z` : null
}

/** The success toast: `Copied 4 phases from 2026.10.` */
export function copiedToast(created: number, sourceName: string): string {
  return `Copied ${plural(created, 'phase')} from ${sourceName}.`
}
