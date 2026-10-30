/**
 * `MetricSpark` (W23d AGT-15) — one application-metric series from the log
 * stage's Prometheus evidence: the kit's `Sparkline` (no new chart library,
 * colours from the chart tokens through its tone) beside a one-line caption
 * "620 ms vs 200 ms before".
 *
 * Fixed 120 × 24 px; the caption is text, so the line itself still draws no
 * text a wider CI font could push out. Fewer than 2 measured points draws no
 * line (the Sparkline rule) and keeps the caption only.
 */
import Sparkline, { type SparklineTone } from './Sparkline'
import { formatMetric, metricCaption } from '@/components/ai/specialistEvidenceModel'

export const METRIC_SPARK_WIDTH = 120
export const METRIC_SPARK_HEIGHT = 24

export interface MetricSparkProps {
  /** The template's label, e.g. "p95 latency". */
  label: string
  /** The regex-checked service name, e.g. "checkout". */
  service: string
  /** A `TEMPLATE_UNITS` value: formats the caption and the accessible name. */
  unit: string
  /** OLDEST first; `null` = not measured (the line breaks). */
  values: readonly (number | null)[]
  windowMean: number | null
  baselineMean: number | null
  /** `warn` when the series rose; else `neutral`. */
  tone?: SparklineTone
}

export default function MetricSpark({
  label, service, unit, values, windowMean, baselineMean, tone = 'neutral',
}: MetricSparkProps) {
  const caption = metricCaption(windowMean, baselineMean, unit)
  const name = service ? `${label} for ${service}` : label
  return (
    <div className="flex shrink-0 flex-col gap-0.5" data-metric-spark="">
      <Sparkline
        series={values}
        label={name}
        tone={tone}
        height={METRIC_SPARK_HEIGHT}
        format={(value) => formatMetric(value, unit)}
        className="w-[120px]"
      />
      {caption && (
        <span className="whitespace-nowrap text-2xs text-[var(--color-text-muted)]" data-metric-spark-caption="">
          {caption}
        </span>
      )}
    </div>
  )
}
