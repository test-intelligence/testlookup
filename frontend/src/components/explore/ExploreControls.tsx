/**
 * VIZ-505 — the Explorer's pickers: metric, x axis, lines (the series
 * dimension), panels (the facet), the y-scale and the window.
 *
 * A picker offers only what `allowedOptions` allows given the pickers before
 * it, so every combination on screen is one the API accepts. The two facets
 * that exist but are refused here (the series already uses it; a release
 * facet in All Projects) stay in the list, disabled, with the reason, so the
 * reader is told why rather than left wondering where "Release" went.
 */
import { useId, type ReactElement } from 'react'
import { clsx } from 'clsx'
import {
  allowedOptions,
  API_METRICS,
  DIMENSION_LABELS,
  EXPLORE_DAYS,
  EXPLORE_METRICS,
  refusalReason,
  type ApiDimension,
  type ApiMetric,
  type ExploreConfig,
  type ExploreContext,
  type ExploreDays,
  type ExploreFacet,
  type ExploreX,
  type ExploreYScale,
} from './exploreModel'
import { NONE } from './exploreUrl'

// The token classes of `CompareSection`'s pickers: no colour literals.
const SELECT =
  'rounded border border-[var(--color-border-light)] bg-[var(--color-bg-card)] px-2 py-1 text-sm text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'
const LABEL = 'inline-flex items-center gap-1.5 text-sm text-[var(--color-text-secondary)]'
const TOGGLE =
  'px-2.5 py-1 text-sm font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50 disabled:hover:bg-transparent'
const TOGGLE_ON = 'bg-[var(--color-accent-bg-soft)] text-[var(--color-accent-ink)]'

const FACETS: readonly ExploreFacet[] = ['suite', 'release']

export interface ExploreControlsProps {
  config: ExploreConfig
  ctx: ExploreContext
  /** The whole next configuration; the page coerces it and writes the URL. */
  onChange: (next: ExploreConfig) => void
}

export default function ExploreControls({ config, ctx, onChange }: ExploreControlsProps): ReactElement {
  const metricId = useId()
  const xId = useId()
  const seriesId = useId()
  const facetId = useId()
  const daysId = useId()
  const metrics = allowedOptions('metric', config, ctx) as ApiMetric[]
  const xs = allowedOptions('x', config, ctx) as ExploreX[]
  const series = allowedOptions('series', config, ctx) as (ApiDimension | null)[]
  const facets = allowedOptions('facet', config, ctx)

  const set = <K extends keyof ExploreConfig>(key: K, value: ExploreConfig[K]) => onChange({ ...config, [key]: value })

  return (
    <div data-explore-controls="" className="flex flex-wrap items-center gap-x-5 gap-y-2">
      <label htmlFor={metricId} className={LABEL}>
        Metric
        <select
          id={metricId}
          data-explore-control="metric"
          value={config.metric}
          onChange={(event) => set('metric', event.target.value as ApiMetric)}
          className={SELECT}
        >
          {API_METRICS.filter((metric) => metrics.includes(metric)).map((metric) => (
            <option key={metric} value={metric}>
              {EXPLORE_METRICS[metric].label}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={xId} className={LABEL}>
        X axis
        <select id={xId} data-explore-control="x" value={config.x} onChange={(event) => set('x', event.target.value as ExploreX)} className={SELECT}>
          {xs.map((x) => (
            <option key={x} value={x}>
              {DIMENSION_LABELS[x].label}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={seriesId} className={LABEL}>
        Lines
        <select
          id={seriesId}
          data-explore-control="series"
          value={config.series ?? NONE}
          onChange={(event) => set('series', event.target.value === NONE ? null : (event.target.value as ApiDimension))}
          className={SELECT}
        >
          {series.map((dim) => (
            <option key={dim ?? NONE} value={dim ?? NONE}>
              {dim ? `One per ${DIMENSION_LABELS[dim].label.toLowerCase()}` : 'One line'}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor={facetId} className={LABEL}>
        Panels
        <select
          id={facetId}
          data-explore-control="facet"
          value={config.facet ?? NONE}
          onChange={(event) => set('facet', event.target.value === NONE ? null : (event.target.value as ExploreFacet))}
          className={SELECT}
        >
          <option value={NONE}>One panel</option>
          {FACETS.map((facet) => {
            const reason = facets.includes(facet) ? null : refusalReason('facet', facet, config, ctx)
            return (
              <option key={facet} value={facet} disabled={reason !== null} title={reason ?? undefined}>
                {`One per ${DIMENSION_LABELS[facet].label.toLowerCase()}`}
                {reason ? ` (${reason})` : ''}
              </option>
            )
          })}
        </select>
      </label>
      <div role="group" aria-label="Y-scale" className={LABEL}>
        Y-scale
        <span className="inline-flex overflow-hidden rounded border border-[var(--color-border-light)]">
          {(['shared', 'independent'] as const satisfies readonly ExploreYScale[]).map((mode) => (
            <button
              key={mode}
              type="button"
              data-explore-yscale-toggle={mode}
              aria-pressed={config.yScale === mode}
              disabled={config.facet === null}
              title={config.facet === null ? 'One panel has one scale' : undefined}
              onClick={() => set('yScale', mode)}
              className={clsx(TOGGLE, config.yScale === mode && TOGGLE_ON)}
            >
              {mode === 'shared' ? 'Shared' : 'Own'}
            </button>
          ))}
        </span>
      </div>
      <label htmlFor={daysId} className={LABEL}>
        Window
        <select
          id={daysId}
          data-explore-control="days"
          value={config.days}
          onChange={(event) => set('days', Number(event.target.value) as ExploreDays)}
          className={SELECT}
        >
          {EXPLORE_DAYS.map((days) => (
            <option key={days} value={days}>
              Last {days} days
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}
