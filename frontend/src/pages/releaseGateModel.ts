/**
 * The release gate card's plain lines, read from the decision's own payload (batch 8).
 *
 * D-104: the card states the numbers the verdict used. A stored decision keeps them in
 * `input_snapshot.policy_snapshot.document.thresholds` (the policy the evaluator applied, frozen with the
 * decision); the quick look publishes `go_threshold`, `no_go_threshold`, `threshold` and `no_go_floor_pct` at
 * the top of its snapshot. A number the snapshot lacks is left out, never filled in from today's defaults.
 *
 * D-142: a quick look names the policy rules it could not check (`input_snapshot.rules_not_checked`).
 */
import { DECISION_LABEL } from '@/components/ui/statusVocabulary'

type Snapshot = Record<string, unknown> | null | undefined

export interface VerdictThresholds {
  go?: number
  noGo?: number
  passMin?: number
  floorPct?: number
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
}

/** The thresholds the verdict was decided with: the frozen policy document first, else the quick look's own. */
export function verdictThresholds(snapshot: Snapshot): VerdictThresholds {
  const snap = record(snapshot)
  if (!snap) return {}
  const doc = record(record(record(snap.policy_snapshot)?.document)?.thresholds)
  if (doc) {
    const passMin = num(doc.pass_rate_minimum)
    const factor = num(doc.pass_rate_hard_floor_factor)
    return {
      go: num(doc.go_threshold),
      noGo: num(doc.no_go_threshold),
      passMin,
      floorPct: passMin != null && factor != null ? passMin * factor : undefined,
    }
  }
  return {
    go: num(snap.go_threshold),
    noGo: num(snap.no_go_threshold),
    passMin: num(snap.threshold),
    floorPct: num(snap.no_go_floor_pct),
  }
}

/** 20 → "20", 62.99999 → "63", 66.5 → "66.5". */
function fmt(n: number): string {
  return String(Number(n.toFixed(1)))
}

/** The pass-rate band edges the gate applied at read time (`pass_rate_bands` on the response; review F1). */
export interface PassRateBands {
  orange_min?: number | null
  yellow_min?: number | null
  green_min?: number | null
}

/** "Pass-rate bands: No-Go under 90%, Conditional under 95%": under orange is red (No-Go), orange is Conditional. */
export function describePassRateBands(bands: PassRateBands | null | undefined): string | null {
  const red = num(bands?.orange_min)
  const orange = num(bands?.yellow_min)
  const parts = [
    red != null ? `${DECISION_LABEL.no_go} under ${fmt(red)}%` : null,
    orange != null ? `${DECISION_LABEL.conditional} under ${fmt(orange)}%` : null,
  ].filter(Boolean)
  return parts.length ? `Pass-rate bands: ${parts.join(', ')}` : null
}

/**
 * Review F1: the sentence for a verdict the pass-rate band itself made stricter, e.g. "Held back by the
 * pass-rate band: 92.0% is under 95% (Conditional)." `null` when the band, the edge or the pass rate is unknown.
 */
export function describeBandHold(
  passRate: number | null | undefined,
  band: string | null | undefined,
  bands: PassRateBands | null | undefined,
): string | null {
  const rate = num(passRate)
  if (rate == null) return null
  const edge = band === 'red' ? num(bands?.orange_min) : band === 'orange' ? num(bands?.yellow_min) : undefined
  if (edge == null) return null
  const label = band === 'red' ? DECISION_LABEL.no_go : DECISION_LABEL.conditional
  return `Held back by the pass-rate band: ${rate.toFixed(1)}% is under ${fmt(edge)}% (${label}).`
}

/**
 * "Go under risk 20 and at 90% or more · No-Go at risk 55 or under 63%", with each missing part left out, then
 * the pass-rate bands when a policy applied them (they can floor the verdict too, review F1).
 * `null` when there is nothing to say.
 */
export function describeVerdictThresholds(snapshot: Snapshot, bands?: PassRateBands | null): string | null {
  const { go, noGo, passMin, floorPct } = verdictThresholds(snapshot)
  const goParts = [
    go != null ? `under risk ${fmt(go)}` : null,
    passMin != null ? `at ${fmt(passMin)}% or more` : null,
  ].filter(Boolean)
  const noGoParts = [
    noGo != null ? `at risk ${fmt(noGo)}` : null,
    floorPct != null ? `under ${fmt(floorPct)}%` : null,
  ].filter(Boolean)
  const segments = [
    goParts.length ? `${DECISION_LABEL.go} ${goParts.join(' and ')}` : null,
    noGoParts.length ? `${DECISION_LABEL.no_go} ${noGoParts.join(' or ')}` : null,
    describePassRateBands(bands),
  ].filter(Boolean)
  return segments.length ? segments.join(' · ') : null
}

/** The names of the policy rules a quick look could not check, in the policy's order. */
export function rulesNotChecked(snapshot: Snapshot): string[] {
  const list = record(snapshot)?.rules_not_checked
  if (!Array.isArray(list)) return []
  return list
    .map((rule) => {
      const r = record(rule)
      const name = r?.name ?? r?.type
      return typeof name === 'string' && name.trim() ? name.trim() : null
    })
    .filter((name): name is string => name !== null)
}

/** D-97: the read-time note a stored decision carries when a category was corrected after it. */
export interface AnalysisChanged {
  analysis_changed_since?: boolean | null
  analysis_changed_at?: string | null
}

/** Review F1: the read-time band edges and whether the band itself made the verdict stricter. */
export interface BandFloor {
  release_readiness_band?: string | null
  pass_rate_bands?: PassRateBands | null
  band_held_back?: boolean | null
}
