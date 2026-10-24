/**
 * The release calendar's month grid and week layout (W11 REL-06). Pure.
 *
 * Dates are UTC DAYS, as the server stores them and the schedule export
 * writes them: a release's day is its `released_at` once released, else its
 * `planned_date`; a phase covers its planned window (one end alone is a
 * one-day window). Cancelled releases are not on the calendar. Weeks start on
 * Monday; a month is always six week rows.
 *
 * Placement is by STATIC class names from the lookup tables below (never an
 * inline style built from data: the production CSP), so Tailwind sees every
 * class literally.
 */
import type { Release } from '@/types/releases'

const DAY_MS = 86_400_000
export const MAX_LANES = 3

/** Grid column start, 1-7 (Monday = 1). Written out so Tailwind generates each. */
export const COL_START: readonly string[] = [
  'col-start-1', 'col-start-2', 'col-start-3', 'col-start-4', 'col-start-5', 'col-start-6', 'col-start-7',
]
/** Grid column span, 1-7. */
export const COL_SPAN: readonly string[] = [
  'col-span-1', 'col-span-2', 'col-span-3', 'col-span-4', 'col-span-5', 'col-span-6', 'col-span-7',
]
/** Lane 0-2 → the grid row under the day numbers. */
export const LANE_ROW: readonly string[] = ['row-start-2', 'row-start-3', 'row-start-4']

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]
export const WEEKDAYS: readonly string[] = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const RELEASED = new Set(['released', 'shipped', 'complete', 'completed'])
const CANCELLED = new Set(['cancelled', 'canceled', 'archived'])

export interface CalendarDay {
  /** `YYYY-MM-DD`, a UTC day. */
  date: string
  /** The day of the month, for the cell. */
  day: number
  inMonth: boolean
  isToday: boolean
}

export interface CalendarItem {
  kind: 'release' | 'phase'
  /** Unique within the week. */
  key: string
  releaseId: string
  label: string
  /** The full name and dates (the item's native hover text). */
  title: string
  /** Phase status (bars are coloured by it); `null` for a release chip. */
  status: string | null
  /** 1-7 within the week row, and its length there (clipped at the row's edges). */
  colStart: number
  span: number
  lane: number
  /** The item goes on from the previous / into the next week row. */
  continuesBefore: boolean
  continuesAfter: boolean
}

export interface WeekLayout {
  items: CalendarItem[]
  /** Per day (0-6): the items that did not fit in the three lanes. */
  hidden: { count: number; names: string[] }[]
}

function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

function dayOf(iso: string | null | undefined): string | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  return Number.isFinite(t) ? isoDay(t) : null
}

function dayMs(day: string): number {
  return Date.parse(`${day}T00:00:00Z`)
}

/** `2026-11` → itself when valid; anything else (or nothing) → `today`'s month. */
export function parseMonthParam(value: string | null | undefined, today: Date): string {
  if (value && /^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return value
  return monthParam(today)
}

/** The URL value for a month: `YYYY-MM` of a date's UTC month. */
export function monthParam(date: Date): string {
  return date.toISOString().slice(0, 7)
}

/** `2026-11` moved by `delta` months. */
export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number)
  return monthParam(new Date(Date.UTC(y, m - 1 + delta, 1)))
}

/** `November 2026`. */
export function monthLabel(month: string): string {
  const [y, m] = month.split('-').map(Number)
  return `${MONTHS[m - 1] ?? month} ${y}`
}

/** Six week rows of seven UTC days, Monday first, covering `month` (`YYYY-MM`). */
export function monthGrid(month: string, today: Date = new Date()): CalendarDay[][] {
  const [y, m] = month.split('-').map(Number)
  const first = Date.UTC(y, m - 1, 1)
  const weekday = (new Date(first).getUTCDay() + 6) % 7   // Monday = 0
  const start = first - weekday * DAY_MS
  const todayIso = isoDay(today.getTime())
  return Array.from({ length: 6 }, (_, w) =>
    Array.from({ length: 7 }, (_, d) => {
      const ms = start + (w * 7 + d) * DAY_MS
      const date = new Date(ms)
      return {
        date: isoDay(ms),
        day: date.getUTCDate(),
        inMonth: date.getUTCMonth() === m - 1,
        isToday: isoDay(ms) === todayIso,
      }
    }),
  )
}

/** A release's day on the calendar, or `null` (no date, or cancelled). */
export function releaseDay(release: Pick<Release, 'status' | 'released_at' | 'planned_date'>): string | null {
  const status = (release.status ?? '').toLowerCase()
  if (CANCELLED.has(status)) return null
  if (RELEASED.has(status)) return dayOf(release.released_at) ?? dayOf(release.planned_date)
  return dayOf(release.planned_date)
}

interface Span {
  kind: 'release' | 'phase'
  key: string
  releaseId: string
  label: string
  title: string
  status: string | null
  first: string
  last: string
}

/** Every dated thing of the releases: one chip per release day, one bar per planned phase window. */
export function calendarSpans(releases: readonly Release[]): Span[] {
  const spans: Span[] = []
  for (const release of releases) {
    if (CANCELLED.has((release.status ?? '').toLowerCase())) continue
    const day = releaseDay(release)
    if (day) {
      const word = RELEASED.has((release.status ?? '').toLowerCase()) ? 'released' : 'planned for'
      spans.push({
        kind: 'release', key: `release-${release.id}`, releaseId: release.id,
        label: release.name, title: `${release.name}: ${word} ${day}`, status: null, first: day, last: day,
      })
    }
    for (const phase of release.phases ?? []) {
      const start = dayOf(phase.planned_start)
      const end = dayOf(phase.planned_end)
      if (!start && !end) continue
      const first = (start ?? end) as string
      const lastRaw = (end ?? start) as string
      const last = lastRaw < first ? first : lastRaw
      spans.push({
        kind: 'phase', key: `phase-${phase.id}`, releaseId: release.id,
        label: `${release.name} · ${phase.name}`,
        title: `${release.name} · ${phase.name}: ${first} to ${last} (${(phase.status ?? 'pending').replace('_', ' ')})`,
        status: phase.status ?? 'pending', first, last,
      })
    }
  }
  return spans
}

/**
 * One week row: each item clipped to the row, placed in the first free lane
 * of three (longer items first, then by name); what does not fit is counted
 * on each day it covers ("+N more", its title naming them).
 */
export function layoutWeek(weekDays: readonly CalendarDay[], releases: readonly Release[]): WeekLayout {
  const weekStart = weekDays[0].date
  const weekEnd = weekDays[weekDays.length - 1].date
  const lo = dayMs(weekStart)
  const placed = calendarSpans(releases)
    .filter(s => s.last >= weekStart && s.first <= weekEnd)
    .map(s => {
      const first = s.first < weekStart ? weekStart : s.first
      const last = s.last > weekEnd ? weekEnd : s.last
      const colStart = Math.round((dayMs(first) - lo) / DAY_MS) + 1
      const span = Math.round((dayMs(last) - dayMs(first)) / DAY_MS) + 1
      return { s, colStart, span, continuesBefore: s.first < weekStart, continuesAfter: s.last > weekEnd }
    })
    .sort((a, b) => a.colStart - b.colStart || b.span - a.span || a.s.label.localeCompare(b.s.label))

  const lanes: boolean[][] = Array.from({ length: MAX_LANES }, () => Array(7).fill(false))
  const items: CalendarItem[] = []
  const hidden = Array.from({ length: 7 }, () => ({ count: 0, names: [] as string[] }))
  for (const p of placed) {
    const cols = Array.from({ length: p.span }, (_, i) => p.colStart - 1 + i)
    const lane = lanes.findIndex(row => cols.every(c => !row[c]))
    if (lane === -1) {
      for (const c of cols) {
        hidden[c].count += 1
        hidden[c].names.push(p.s.label)
      }
      continue
    }
    for (const c of cols) lanes[lane][c] = true
    items.push({
      kind: p.s.kind, key: p.s.key, releaseId: p.s.releaseId, label: p.s.label, title: p.s.title,
      status: p.s.status, colStart: p.colStart, span: p.span, lane,
      continuesBefore: p.continuesBefore, continuesAfter: p.continuesAfter,
    })
  }
  return { items, hidden }
}

/** The bar's colour classes by phase status (the `--ui-*` tokens; a release chip is the accent). */
export function itemTone(item: Pick<CalendarItem, 'kind' | 'status'>): string {
  if (item.kind === 'release') return 'bg-[var(--color-accent-muted)] text-[var(--color-accent)] font-semibold'
  switch ((item.status ?? '').toLowerCase()) {
    case 'completed':
    case 'complete':
    case 'done':
      return 'bg-[var(--ui-success-bg)] text-[var(--ui-success-ink)]'
    case 'in_progress':
    case 'active':
    case 'running':
      return 'bg-[var(--ui-warning-bg)] text-[var(--ui-warning-ink)]'
    case 'failed':
    case 'fail':
      return 'bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)]'
    default:
      return 'bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)]'
  }
}

/** `+N more`, and its title listing the hidden names. */
export function moreLabel(hidden: { count: number; names: string[] }): { text: string; title: string } | null {
  if (hidden.count === 0) return null
  return { text: `+${hidden.count} more`, title: hidden.names.join('\n') }
}
