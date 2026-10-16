/**
 * VIZ-505 — the Explorer's configuration in the URL and in a saved view.
 * Pure: no React.
 *
 * Seven keys always, the same in both places: `metric`, `x`, `series`
 * (`none` for no series), `facet` (`none` for one panel), `y` (`shared` |
 * `independent`), `days` and, since TRND-10's Customise, `top` (lines per
 * panel: `3` | `5` | `7`, `none` for no `top_n`); and `title` when the reader
 * gave the grid one. A link and a saved view are both untrusted text, so
 * reading never throws: a key that is absent takes the default quietly (a link
 * or a view from before `top` / `title` existed reads exactly as it did), a
 * key that is present but invalid takes the default WITH a notice, and the
 * result goes through `coerceConfig`, so a combination the rules refuse
 * (`?series=suite&facet=suite`) is reset field by field, each reset said.
 */
import {
  coerceConfig,
  EXPLORE_DAYS,
  EXPLORE_TITLE_MAX,
  EXPLORE_TOP_N,
  quoted,
  type ExploreAxes,
  type ExploreConfig,
  type ExploreContext,
  type ExploreDays,
  type ExploreTopN,
  type ExploreYScale,
} from './exploreModel'

export const EXPLORE_URL_KEYS = {
  metric: 'metric',
  x: 'x',
  series: 'series',
  facet: 'facet',
  yScale: 'y',
  days: 'days',
  topN: 'top',
  title: 'title',
} as const

/** "No series" / "no facet" / "no lines-per-panel cap" in the URL. */
export const NONE = 'none'

/** Failure rate by week, one line per environment, one panel per suite, on one scale, over 30 days. */
export const DEFAULT_EXPLORE: ExploreConfig = {
  metric: 'failure_rate',
  x: 'week',
  series: 'environment',
  facet: 'suite',
  yScale: 'shared',
  days: 30,
  topN: null,
}

const Y_SCALES: readonly ExploreYScale[] = ['shared', 'independent']

export type ExploreSource = URLSearchParams | Record<string, unknown> | null | undefined

/** One key's raw value: a string, or a number from a saved view; anything else is absent. */
function rawOf(source: ExploreSource, key: string): string | null {
  if (!source) return null
  if (source instanceof URLSearchParams) return source.get(key)
  const value = Object.prototype.hasOwnProperty.call(source, key) ? source[key] : undefined
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

export interface ReadExploreOptions {
  /** The window when the source names none (the page snaps the global window to `EXPLORE_DAYS`). */
  defaultDays?: ExploreDays
}

/** `top`: absent or `none` = no cap; `3` / `5` / `7`; anything else = no cap, with a notice. */
function readTopN(source: ExploreSource, notices: string[]): ExploreTopN | null {
  const raw = rawOf(source, EXPLORE_URL_KEYS.topN)
  if (raw === null || raw === NONE) return null
  const parsed = Number(raw)
  if (/^\d+$/.test(raw) && (EXPLORE_TOP_N as readonly number[]).includes(parsed)) return parsed as ExploreTopN
  notices.push(`Lines per panel "${quoted(raw)}" is not ${EXPLORE_TOP_N.join(', ')}; showing the default number of lines instead.`)
  return null
}

/**
 * `title`: as typed (the reader is typing it into the URL, so a trailing
 * space between two words must survive the round trip); blank = none; longer
 * than `EXPLORE_TITLE_MAX` = none, with a notice.
 */
function readTitle(source: ExploreSource, notices: string[]): string | undefined {
  const raw = rawOf(source, EXPLORE_URL_KEYS.title)
  if (raw === null || raw.trim() === '') return undefined
  if (raw.length > EXPLORE_TITLE_MAX) {
    notices.push(`The title is longer than ${EXPLORE_TITLE_MAX} characters; showing no title instead.`)
    return undefined
  }
  return raw
}

/** The configuration a URL or a saved view describes, and what had to be changed to make it valid. */
export function readExploreConfig(
  source: ExploreSource,
  ctx: ExploreContext,
  { defaultDays = DEFAULT_EXPLORE.days }: ReadExploreOptions = {},
): { config: ExploreConfig; notices: string[] } {
  const notices: string[] = []
  const field = (key: string, fallback: string | null): string | null => {
    const raw = rawOf(source, key)
    if (raw === null) return fallback
    return raw === NONE ? null : raw
  }
  const axes: ExploreAxes = {
    metric: field(EXPLORE_URL_KEYS.metric, DEFAULT_EXPLORE.metric),
    x: field(EXPLORE_URL_KEYS.x, DEFAULT_EXPLORE.x),
    series: field(EXPLORE_URL_KEYS.series, DEFAULT_EXPLORE.series),
    facet: field(EXPLORE_URL_KEYS.facet, DEFAULT_EXPLORE.facet),
  }
  const coerced = coerceConfig(axes, ctx, DEFAULT_EXPLORE)
  notices.push(...coerced.notices)

  let yScale = DEFAULT_EXPLORE.yScale
  const rawY = rawOf(source, EXPLORE_URL_KEYS.yScale)
  if (rawY !== null) {
    if ((Y_SCALES as readonly string[]).includes(rawY)) yScale = rawY as ExploreYScale
    else notices.push(`Y-scale "${quoted(rawY)}" is not shared or independent; showing a shared scale instead.`)
  }

  let days = defaultDays
  const rawDays = rawOf(source, EXPLORE_URL_KEYS.days)
  if (rawDays !== null) {
    const parsed = Number(rawDays)
    if ((EXPLORE_DAYS as readonly number[]).includes(parsed) && /^\d+$/.test(rawDays)) days = parsed as ExploreDays
    else notices.push(`Window "${quoted(rawDays)}" is not one of ${EXPLORE_DAYS.join(', ')} days; showing ${defaultDays} days instead.`)
  }

  const topN = readTopN(source, notices)
  const title = readTitle(source, notices)
  const config: ExploreConfig = { ...coerced.axes, yScale, days, topN }
  if (title !== undefined) config.title = title
  return { config, notices }
}

/** The seven keys as strings (`none` for a missing series, facet or cap), and `title` when there is one. */
export function exploreViewFilters(config: ExploreConfig): Record<string, string | number> {
  const filters: Record<string, string | number> = {
    [EXPLORE_URL_KEYS.metric]: config.metric,
    [EXPLORE_URL_KEYS.x]: config.x,
    [EXPLORE_URL_KEYS.series]: config.series ?? NONE,
    [EXPLORE_URL_KEYS.facet]: config.facet ?? NONE,
    [EXPLORE_URL_KEYS.yScale]: config.yScale,
    [EXPLORE_URL_KEYS.days]: config.days,
    [EXPLORE_URL_KEYS.topN]: config.topN ?? NONE,
  }
  if (config.title?.trim()) filters[EXPLORE_URL_KEYS.title] = config.title
  return filters
}

/**
 * `params` with the Explorer's keys set to `config` (a `title` the config no
 * longer has is removed) and every other key kept (the scope keys other
 * features own). The page applies it with `setSearchParams(next, { replace:
 * true })`, so picking does not stack history.
 */
export function writeExploreConfig(params: URLSearchParams, config: ExploreConfig): URLSearchParams {
  const next = new URLSearchParams(params)
  next.delete(EXPLORE_URL_KEYS.title)
  for (const [key, value] of Object.entries(exploreViewFilters(config))) next.set(key, String(value))
  return next
}
