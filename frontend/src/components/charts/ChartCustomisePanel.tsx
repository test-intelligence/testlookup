/**
 * VIZ-604 — "Customise": a SIDE PANEL (not a modal), so the chart it changes
 * stays in view and redraws as each setting changes. Title, metric, series
 * dimension, time bucket and how many lines; chart type and scale are shown
 * with the options this chart does not offer and why (`unavailableOptions`),
 * so a misleading chart is never one click away and never silently missing.
 * "Reset to default" restores the chart as shipped.
 */
import { useId } from 'react'
import { X } from 'lucide-react'
import {
  isDefault,
  SERIES_DIMENSIONS,
  SERIES_METRICS,
  TIME_BUCKETS,
  TITLE_MAX,
  TOP_N_OPTIONS,
  impliedTitle,
  unavailableOptions,
  type SeriesChartConfig,
  type SeriesDimension,
  type SeriesMetric,
  type SeriesTopN,
  type TimeBucket,
} from './chartCustomiseModel'

export interface ChartCustomisePanelProps {
  /** The chart's name, for the panel's heading. */
  chartTitle: string
  config: SeriesChartConfig
  onChange: (next: SeriesChartConfig) => void
  onReset: () => void
  onClose: () => void
}

const SELECT =
  'min-h-8 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-input)] px-2 text-sm text-[var(--color-text)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-ring)]'
const LABEL = 'text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]'

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className={LABEL}>
        {label}
      </label>
      {children}
    </div>
  )
}

export default function ChartCustomisePanel({ chartTitle, config, onChange, onReset, onClose }: ChartCustomisePanelProps) {
  const base = useId()
  const ids = {
    title: `${base}-title`,
    metric: `${base}-metric`,
    series: `${base}-series`,
    bucket: `${base}-bucket`,
    topN: `${base}-topn`,
    type: `${base}-type`,
    scale: `${base}-scale`,
  }
  const unavailable = unavailableOptions(config)
  const reasonsFor = (control: 'chartType' | 'scale' | 'topN') => unavailable.filter((option) => option.control === control)
  const set = (patch: Partial<SeriesChartConfig>) => onChange({ ...config, ...patch })

  return (
    <aside
      aria-label={`Customise ${chartTitle}`}
      data-chart-customise-panel=""
      className="fixed right-0 top-16 z-40 flex h-[calc(100vh-4rem)] w-80 flex-col gap-4 overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 shadow-xl"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-[var(--color-text)]">Customise</h2>
          <p className="text-xs text-[var(--color-text-secondary)]">{chartTitle}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close customise panel"
          className="inline-flex h-8 w-8 items-center justify-center rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </div>

      <Field id={ids.title} label="Title">
        <input
          id={ids.title}
          data-customise="title"
          value={config.title ?? ''}
          maxLength={TITLE_MAX}
          placeholder={impliedTitle(config)}
          onChange={(event) => set({ title: event.target.value || undefined })}
          className={SELECT}
        />
      </Field>

      <Field id={ids.metric} label="Metric">
        <select id={ids.metric} data-customise="metric" value={config.metric} onChange={(event) => set({ metric: event.target.value as SeriesMetric })} className={SELECT}>
          {(Object.keys(SERIES_METRICS) as SeriesMetric[]).map((key) => (
            <option key={key} value={key}>
              {SERIES_METRICS[key].label}
            </option>
          ))}
        </select>
      </Field>

      <Field id={ids.series} label="One line per">
        <select id={ids.series} data-customise="series" value={config.seriesBy} onChange={(event) => set({ seriesBy: event.target.value as SeriesDimension })} className={SELECT}>
          {(Object.keys(SERIES_DIMENSIONS) as SeriesDimension[]).map((key) => (
            <option key={key} value={key}>
              {SERIES_DIMENSIONS[key].label}
            </option>
          ))}
        </select>
      </Field>

      <Field id={ids.bucket} label="Time bucket">
        <select id={ids.bucket} data-customise="bucket" value={config.bucket} onChange={(event) => set({ bucket: event.target.value as TimeBucket })} className={SELECT}>
          {(Object.keys(TIME_BUCKETS) as TimeBucket[]).map((key) => (
            <option key={key} value={key}>
              {TIME_BUCKETS[key]}
            </option>
          ))}
        </select>
      </Field>

      <Field id={ids.topN} label="Lines (busiest first, the rest as “Other”)">
        <select id={ids.topN} data-customise="topN" value={config.topN} onChange={(event) => set({ topN: Number(event.target.value) as SeriesTopN })} className={SELECT}>
          {TOP_N_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
          {reasonsFor('topN').map((option) => (
            <option key={option.label} disabled>
              {option.label} (unavailable)
            </option>
          ))}
        </select>
      </Field>

      <Field id={ids.type} label="Chart type">
        <select id={ids.type} data-customise="chartType" value="line" onChange={() => undefined} className={SELECT}>
          <option value="line">Line</option>
          {reasonsFor('chartType').map((option) => (
            <option key={option.label} disabled>
              {option.label} (unavailable)
            </option>
          ))}
        </select>
      </Field>

      <Field id={ids.scale} label="Scale">
        <select id={ids.scale} data-customise="scale" value="linear" onChange={() => undefined} className={SELECT}>
          <option value="linear">Linear</option>
          {reasonsFor('scale').map((option) => (
            <option key={option.label} disabled>
              {option.label} (unavailable)
            </option>
          ))}
        </select>
      </Field>

      <ul data-customise-reasons="" className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)]">
        {unavailable.map((option) => (
          <li key={`${option.control}-${option.label}`}>
            <span className="font-semibold">{option.label}</span>: {option.reason}
          </li>
        ))}
      </ul>

      <button
        type="button"
        data-customise="reset"
        disabled={isDefault(config)}
        onClick={onReset}
        className="self-start rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm font-medium text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] disabled:opacity-50"
      >
        Reset to default
      </button>
    </aside>
  )
}
