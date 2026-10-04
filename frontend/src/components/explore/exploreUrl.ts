/**
 * VIZ-505 — the Explorer's configuration in the URL and in a saved view.
 * Pure: no React.
 *
 * Six keys, the same in both places: `metric`, `x`, `series` (`none` for no
 * series), `facet` (`none` for one panel), `y` (`shared` | `independent`) and
 * `days`. A link and a saved view are both untrusted text, so reading never
 * throws: a key that is absent takes the default quietly, a key that is
 * present but invalid takes the default WITH a notice, and the result goes
 * through `coerceConfig`, so a combination the rules refuse
 * (`?series=suite&facet=suite`) is reset field by field, each reset said.
 */
import {
  coerceConfig,
  EXPLORE_DAYS,
  quoted,
  type ExploreAxes,
  type ExploreConfig,
  type ExploreContext,
  type ExploreDays,
  type ExploreYScale,
} from './exploreModel'

export const EXPLORE_URL_KEYS = {
  metric: 'metric',
  x: 'x',
  series: 'series',
  facet: 'facet',
  yScale: 'y',
  days: 'days',
} as const

/** "No series" / "no facet" in the URL. */
export const NONE = 'none'

/** Failure rate by week, one line per environment, one panel per suite, on one scale, over 30 days. */
export const DEFAULT_EXPLORE: ExploreConfig = {
  metric: 'failure_rate',
  x: 'week',
  series: 'environment',
  facet: 'suite',
  yScale: 'shared',
  days: 30,
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

  return { config: { ...coerced.axes, yScale, days }, notices }
}

/** The six keys as strings, `none` for a missing series or facet. */
export function exploreViewFilters(config: ExploreConfig): Record<string, string | number> {
  return {
    [EXPLORE_URL_KEYS.metric]: config.metric,
    [EXPLORE_URL_KEYS.x]: config.x,
    [EXPLORE_URL_KEYS.series]: config.series ?? NONE,
    [EXPLORE_URL_KEYS.facet]: config.facet ?? NONE,
    [EXPLORE_URL_KEYS.yScale]: config.yScale,
    [EXPLORE_URL_KEYS.days]: config.days,
  }
}

/**
 * `params` with the six keys set to `config` and every other key kept (the
 * scope keys other features own). The page applies it with
 * `setSearchParams(next, { replace: true })`, so picking does not stack history.
 */
export function writeExploreConfig(params: URLSearchParams, config: ExploreConfig): URLSearchParams {
  const next = new URLSearchParams(params)
  for (const [key, value] of Object.entries(exploreViewFilters(config))) next.set(key, String(value))
  return next
}
