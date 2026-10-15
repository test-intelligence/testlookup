/**
 * EXEC-04: the words the Run page's Timing tab prints for its figures. A
 * figure the report did not carry (`null` in the EXEC-03 contract) is "not
 * reported", never "0 ms" and never a bare dash; a genuine zero stays "0ms".
 */
import type { RunTimingFigures, SuiteTimingSource } from '@/types/executionMetrics'
import { formatDuration, formatNumber, formatPercent } from '@/utils/formatters'

/** What a figure the report did not carry reads as. Never "0 ms". */
export const NOT_REPORTED = 'not reported'

/** A millisecond figure, or "not reported". */
export function msText(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) || value < 0 ? NOT_REPORTED : formatDuration(Math.round(value))
}

/** `1.9×`, or "not reported". */
export function ratioText(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? NOT_REPORTED : `${formatNumber(value, { maximumFractionDigits: 1 })}×`
}

/** A 0-1 share as `8.8%`, or "not reported". */
export function shareText(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? NOT_REPORTED : formatPercent(value, { from: 'ratio' })
}

/** A server reason as a sentence: capitalised, one full stop. */
export function reasonSentence(reason: string | null | undefined, fallback = 'Not measured'): string {
  const text = (reason ?? '').trim() || fallback
  const capital = text.charAt(0).toUpperCase() + text.slice(1)
  return /[.!?]$/.test(capital) ? capital : `${capital}.`
}

const RUN_SOURCE_TEXT: Record<SuiteTimingSource, string> = {
  report: "Timing source: the report's own run clock.",
  derived: "Timing source: derived from the tests' start and finish times.",
  derived_sum: 'Timing source: the report carried no run clock, so only the summed test time is known.',
}

/** Where the run's figures come from, in one sentence. */
export function runSourceText(source: RunTimingFigures['timing_source']): string {
  return (source && RUN_SOURCE_TEXT[source]) || "Not timed yet: this run's timing has not been computed."
}

/** A suite row's source badge. */
export const SUITE_SOURCE_LABEL: Record<SuiteTimingSource, string> = {
  report: 'report',
  derived: 'derived',
  derived_sum: 'summed only',
}
