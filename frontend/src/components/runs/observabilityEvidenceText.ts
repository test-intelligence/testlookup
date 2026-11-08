/**
 * W39 STRAT-07: the "Observability evidence" card's own words (the lane sentences are the server's, rendered from
 * the answer; the vocabulary the server shares is `types/observabilityTimeline.ts`).
 */
import { CONFIGURED_LABELS, CONFIGURED_SOURCES, type RunObservabilityTimeline } from '@/types/observabilityTimeline'
import { formatCompactDateTime, formatNumber } from '@/utils/formatters'

export const LOAD_ERROR = 'Could not load the observability evidence of this run.'
export const NO_ANALYSIS_SENTENCE = 'No deep analysis of this run is stored.'
export const RUN_WINDOW_CAPTION = 'The shaded band is the run, from its start to its finish.'
export const NO_RUN_WINDOW_CAPTION = "The run's own start and finish are not known, so no band is shaded."

const joined = (names: string[]) =>
  names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`

/** `configured_now` in words: which integrations the project has saved settings for, NOW (not at analysis time). */
export function configuredNowLine(configured: RunObservabilityTimeline['configured_now'] | null | undefined): string {
  const on = CONFIGURED_SOURCES.filter((key) => configured?.[key] === true).map((key) => CONFIGURED_LABELS[key])
  const off = CONFIGURED_SOURCES.filter((key) => configured?.[key] !== true).map((key) => CONFIGURED_LABELS[key])
  if (on.length === 0) return `${joined(off)} are not configured for this project.`
  if (off.length === 0) return `${joined(on)} are configured for this project.`
  return `${joined(on)} ${on.length === 1 ? 'is' : 'are'} configured for this project; ${joined(off)} ${off.length === 1 ? 'is' : 'are'} not.`
}

/** "From the analysis of Nov 8, 10:05." (the snapshot the Prometheus / Splunk trace lanes come from). */
export function analysedLine(analysedAt: string | null): string {
  return analysedAt ? `From the analysis of ${formatCompactDateTime(analysedAt)}.` : 'From the newest stored analysis.'
}

/** "2 earlier analyses not shown." */
export function othersLine(count: number): string {
  return `${formatNumber(count)} earlier ${count === 1 ? 'analysis' : 'analyses'} not shown.`
}
