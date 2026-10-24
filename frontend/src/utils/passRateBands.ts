/**
 * W11 GATE-04: the pass-rate GO line comes from the project's release-gate
 * policy, and every page that draws or names it says which policy that is.
 *
 * One rule everywhere: the GO line is the policy's `yellow_min`; below its
 * `orange_min` is the No-Go zone. With no published policy the defaults
 * (95 / 90 / 99) apply, labelled `default bands` — never left unsaid.
 *
 * Pure: the pages (Trends, Live, Overview) and the tests import these strings,
 * so the copy cannot drift between them.
 */
import type { DashboardSummary } from '@/types/analytics'
import { DEFAULT_PASS_RATE_BANDS, policyBands, type PolicySummary } from '@/services/policyService'

export type BandsLevel = 'project' | 'system' | 'default'

export interface ReadinessBands {
  /** The GO line: at or above it the pass rate is a GO. */
  goMin: number
  /** Below this is the No-Go zone. */
  noGoBelow: number
  greenMin: number
  policyName: string | null
  version: number | null
  level: BandsLevel
}

type SummaryPolicy = NonNullable<DashboardSummary['readiness_policy']>

function isSummaryPolicy(value: SummaryPolicy | PolicySummary): value is SummaryPolicy {
  return typeof (value as SummaryPolicy).bands === 'object' && (value as SummaryPolicy).bands !== null
}

function finite(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

const DEFAULT_BANDS: ReadinessBands = {
  goMin: DEFAULT_PASS_RATE_BANDS.yellow_min,
  noGoBelow: DEFAULT_PASS_RATE_BANDS.orange_min,
  greenMin: DEFAULT_PASS_RATE_BANDS.green_min,
  policyName: null,
  version: null,
  level: 'default',
}

/**
 * The bands a page draws: the summary's `readiness_policy`, the effective
 * policy (`GET /release-gate-policies/effective/{project}`), or the defaults.
 */
export function readinessBands(
  policy?: DashboardSummary['readiness_policy'] | PolicySummary | null,
): ReadinessBands {
  if (!policy) return DEFAULT_BANDS
  if (isSummaryPolicy(policy)) {
    return {
      goMin: finite(policy.bands.yellow_min, DEFAULT_PASS_RATE_BANDS.yellow_min),
      noGoBelow: finite(policy.bands.orange_min, DEFAULT_PASS_RATE_BANDS.orange_min),
      greenMin: finite(policy.bands.green_min, DEFAULT_PASS_RATE_BANDS.green_min),
      policyName: policy.name || null,
      version: typeof policy.version === 'number' ? policy.version : null,
      level: policy.level === 'system' ? 'system' : 'project',
    }
  }
  const bands = policyBands(policy)
  return {
    goMin: bands.yellow_min,
    noGoBelow: bands.orange_min,
    greenMin: bands.green_min,
    policyName: policy.name || null,
    version: typeof policy.version === 'number' ? policy.version : null,
    level: policy.project_id ? 'project' : 'system',
  }
}

/** `95` → `95%`, `97.5` → `97.5%`. */
export function pct(value: number): string {
  return `${Number.isInteger(value) ? value : Number(value.toFixed(1))}%`
}

/** The chart's target legend entry: `GO ≥ 95%`. */
export function goLineLabel(b: ReadinessBands): string {
  return `GO ≥ ${pct(b.goMin)}`
}

/** The shaded zone's legend entry: `No-Go < 90%`. */
export function noGoZoneLabel(b: ReadinessBands): string {
  return `No-Go < ${pct(b.noGoBelow)}`
}

/** Where the lines come from: `default bands` / `Payments gate v4`. */
export function bandsSource(b: ReadinessBands): string {
  if (b.level === 'default' || !b.policyName) return 'default bands'
  return b.version !== null ? `${b.policyName} v${b.version}` : b.policyName
}

/**
 * Trends' takeaway half: `at the GO line (95%, default bands)` /
 * `3 pp below the GO line (95%, Payments gate v4)` /
 * `2 pp above the GO line (95%, default bands)`.
 */
export function versusGoLine(rate: number, b: ReadinessBands): string {
  const where = `the GO line (${pct(b.goMin)}, ${bandsSource(b)})`
  const delta = Math.round(rate - b.goMin)
  if (delta === 0) return `at ${where}`
  return `${Math.abs(delta)} pp ${delta < 0 ? 'below' : 'above'} ${where}`
}

export type LiveTone = 'success' | 'warning' | 'danger'

/** Live's pass-rate tone: at or above the GO line, between the lines, the No-Go zone. */
export function passRateTone(rate: number, b: ReadinessBands): LiveTone {
  if (rate >= b.goMin) return 'success'
  if (rate >= b.noGoBelow) return 'warning'
  return 'danger'
}

/**
 * Live's chip: `null` at or above the GO line, `below the GO line (95%)`
 * between the lines, `in the No-Go zone (< 90%)` under `orange_min`.
 */
export function liveChip(rate: number, b: ReadinessBands): string | null {
  const tone = passRateTone(rate, b)
  if (tone === 'success') return null
  if (tone === 'warning') return `below the GO line (${pct(b.goMin)})`
  return `in the No-Go zone (< ${pct(b.noGoBelow)})`
}

/** The chip's `title`: `Bands: Payments gate v4` / `Bands: default`. */
export function bandsTitle(b: ReadinessBands): string {
  return b.level === 'default' || !b.policyName ? 'Bands: default' : `Bands: ${bandsSource(b)}`
}

const DOWNGRADE_LABELS: Record<string, string> = {
  p0_defects: 'Open critical (P0) defects',
  flaky: 'Flaky tests',
  new_failures_24h: 'New failures in 24 h',
}

/**
 * A hard-cap downgrade token (`classify_with_policy`'s `p0_defects:2>0`) as a
 * sentence: `Open critical (P0) defects: 2, cap 0`. An unknown token is shown
 * verbatim (it is never dropped).
 */
export function describeDowngrade(token: string): string {
  const match = /^([a-z0-9_]+):(-?\d+(?:\.\d+)?)>(-?\d+(?:\.\d+)?)$/.exec(token)
  if (!match) return token
  const label = DOWNGRADE_LABELS[match[1]]
  if (!label) return token
  return `${label}: ${match[2]}, cap ${match[3]}`
}

/**
 * With no policy resolved, the dashboard verdict is NOT the default bands: the
 * summary falls back to `metrics_service._compute_readiness`, a fixed rule over
 * the pass rate, open defects and flaky tests. Its thresholds, restated (keep
 * them in step with that function).
 */
export const BUILT_IN_RULE_SENTENCE =
  'Go at 95% or more with no open defects and at most 5 flaky tests, ' +
  'Conditional from 85% with at most 5 open defects, otherwise No-Go.'

/**
 * Overview's "How this verdict is decided" line:
 * `Decided by Payments gate v4 (project policy): Go at 95% or more,
 * Conditional from 90%, No-Go below 90%.` — or, with no policy, the built-in
 * rule that decides then (never a claim that the default BANDS did).
 */
export function decidedBy(b: ReadinessBands): string {
  if (b.level === 'default' || !b.policyName) {
    return (
      'Decided by the built-in rule (no release-gate policy is published for this project or the deployment): ' +
      BUILT_IN_RULE_SENTENCE
    )
  }
  const scope = b.level === 'system' ? 'system default policy' : 'project policy'
  return (
    `Decided by ${bandsSource(b)} (${scope}): Go at ${pct(b.goMin)} or more, ` +
    `Conditional from ${pct(b.noGoBelow)}, No-Go below ${pct(b.noGoBelow)}.`
  )
}
