/**
 * VIZ-505 — the Explorer's pickers: metric, x axis, lines (the series
 * dimension), panels (the facet), the y-scale and the window.
 *
 * A picker offers only what `allowedOptions` allows given the pickers before
 * it, so every combination on screen is one the API accepts. The panels
 * picker lists all seven facets (TRND-13: suite, release, branch,
 * environment, status, failure category, ingestion source); one that is
 * refused here (the series already uses it; a release facet in All Projects;
 * Runs split by status or failure category) stays in the list, disabled
 * (the reason as its title), and the reason is said in words
 * beside the picker (`facetRefusalLines`: not the series one, which the Lines
 * picker says itself), so the reader is told why rather than left wondering
 * where it went — and the select stays as narrow as its options.
 */
import { useId, type ReactElement } from 'react'
import { clsx } from 'clsx'
import {
  allowedOptions,
  API_METRICS,
  DIMENSION_LABELS,
  EXPLORE_DAYS,
  EXPLORE_FACETS,
  EXPLORE_METRICS,
  facetRefusalLines,
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
  const refused = EXPLORE_FACETS.map((facet) => ({
    facet,
    reason: facets.includes(facet) ? null : refusalReason('facet', facet, config, ctx),
  }))
  const said = facetRefusalLines(refused)

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
          {refused.map(({ facet, reason }) => (
            // Disabled, the reason as its title and, unless it is the obvious one, said beside the picker:
            // a native select is as wide as its longest option, so a sentence (or even a suffix) here
            // widened the whole row of pickers.
            <option key={facet} value={facet} disabled={reason !== null} title={reason ?? undefined} data-refused={reason ? '' : undefined}>
              {`One per ${DIMENSION_LABELS[facet].label.toLowerCase()}`}
            </option>
          ))}
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
      {/* The panels picker's refusals in words, on a line of their own under the pickers. */}
      {said.length > 0 ? (
        <ul data-explore-refusals="" className="m-0 basis-full list-none space-y-0.5 p-0 text-xs text-[var(--color-text-secondary)]">
          {said.map((line) => (
            <li key={line} data-explore-refusal="">
              {line}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
