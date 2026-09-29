/**
 * The gauge bar's model (VIZ-104, K4) — every rule as pure data, so the bar,
 * its marker, its scale and its accessible value cannot disagree, and every
 * edge case is testable without a DOM.
 *
 * Eight hand-drawn bars on six pages (the verdict meters, the Overview mini
 * meter, the IntelligenceHub table meter) each re-derived these rules, and
 * each got one of them wrong somewhere:
 *   - NOT MEASURED IS NOT ZERO. `null` is "no value": an empty track, no
 *     marker, no fill and an accessible "not measured" — never a bar at 0,
 *     which reads as "measured, and it is zero" (`RiskMeter` drew exactly
 *     that for a missing risk score).
 *   - Positions are CLAMPED to the domain at both ends: a value past either
 *     end pins the fill / marker to that end and never draws outside the
 *     track (and never as NaN). The true value is still what the text says.
 *   - Stacked `segments` are laid end to end in domain units; their widths
 *     sum to exactly the clamped position of their total, so the stack never
 *     runs past the track.
 *   - A `target` and each tick sit at their TRUE position on the domain. The
 *     copied meters put their two notches at 33 % and 67 % whatever the
 *     thresholds were ("Healthy · 70" was drawn at 67).
 *   - Which end is good is a PROP (`direction`), not a colour choice buried
 *     in a page: risk is lower-is-better, health is higher-is-better.
 */
import { CHART_VARS } from './tokens'

/**
 * Semantic tone of a gauge. Always paired with text: never colour-only.
 * `accent` passes no judgement (a share of a budget, the P2 slice of a
 * severity split); `toneForValue` never returns it.
 */
export type GaugeTone = 'good' | 'watch' | 'warn' | 'bad' | 'neutral' | 'accent'

/** Which end of the domain is the good one. */
export type GaugeDirection = 'higher-is-better' | 'lower-is-better'

/**
 * Two ascending thresholds that split the domain into three bands, plus the
 * direction that says which band is good.
 *   higher-is-better: v >= hi good, v >= lo warn, else bad
 *   lower-is-better:  v >= hi bad,  v >= lo warn, else good
 */
export interface GaugeBands {
  direction: GaugeDirection
  thresholds: readonly [lo: number, hi: number]
}

/** One piece of a stacked bar, in domain units. */
export interface GaugeSegment {
  value: number
  label: string
  tone?: GaugeTone
}

/** One scale mark under (and notched into) the bar. */
export interface GaugeTick {
  value: number
  /** A band name ("At risk"); the text reads `LABEL · value`. Omitted: just the value. */
  label?: string
}

export interface GaugeTarget {
  value: number
  label: string
}

export type GaugeBarVariant = 'fill' | 'marker'
export type GaugeBarSize = 'sm' | 'md'
/** `health` runs bad → good left to right; `risk` runs good → bad (lower is better). */
export type GaugeGradient = 'health' | 'risk'

/** Track thickness and marker diameter, px. */
export const GAUGE_BAR_SIZES: Record<GaugeBarSize, { thickness: number; marker: number }> = {
  sm: { thickness: 4, marker: 10 },
  md: { thickness: 6, marker: 14 },
}

/** The track width beside an inline value, px (the IntelligenceHub table meter's). */
export const INLINE_TRACK_WIDTH = 56

/** The tick labels' font size, px (the copied meters' `text-[10px]`). */
export const GAUGE_TICK_FONT_PX = 10

/** The fallback domain when none (or a degenerate one) is given. */
export const DEFAULT_GAUGE_DOMAIN: readonly [number, number] = [0, 100]

/** The CSS colour of each tone: the status tokens, so it follows the theme. */
export const GAUGE_TONE_COLOR: Record<GaugeTone, string> = {
  good: CHART_VARS.status.passed,
  watch: CHART_VARS.status.skipped,
  warn: CHART_VARS.status.broken,
  bad: CHART_VARS.status.failed,
  neutral: CHART_VARS.neutral,
  accent: CHART_VARS.accent,
}

/**
 * The tone of `value` under `bands`. `null` (not measured) and NaN are
 * `neutral`: an unmeasured value is neither good nor bad.
 */
export function toneForValue(value: number | null, bands: GaugeBands): GaugeTone {
  if (value === null || Number.isNaN(value)) return 'neutral'
  const [lo, hi] = bands.thresholds
  if (bands.direction === 'higher-is-better') return value >= hi ? 'good' : value >= lo ? 'warn' : 'bad'
  return value >= hi ? 'bad' : value >= lo ? 'warn' : 'good'
}

/** `min(max(v, lo), hi)`; NaN is not a number to clamp, so it is `lo`. */
export function clamp(v: number, lo: number, hi: number): number {
  if (Number.isNaN(v)) return lo
  return Math.min(hi, Math.max(lo, v))
}

/** A domain the bar can divide by: finite and increasing, or the default. */
export function resolveDomain(domain: readonly [number, number] | undefined): readonly [number, number] {
  if (!domain) return DEFAULT_GAUGE_DOMAIN
  const [min, max] = domain
  if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return DEFAULT_GAUGE_DOMAIN
  return domain
}

/**
 * Where `v` sits on the track, 0-100, clamped at both ends. A zero-width
 * domain ([0, 0]: "0 of 0 failures") puts everything at 0, never NaN.
 */
export function positionPct(v: number, domain: readonly [number, number]): number {
  const [min, max] = domain
  if (max === min) return 0
  return clamp(((v - min) / (max - min)) * 100, 0, 100)
}

export interface GaugeBarModelInput {
  value: number | null
  domain?: readonly [number, number]
  segments?: readonly GaugeSegment[]
  target?: GaugeTarget
  ticks?: readonly GaugeTick[]
}

export interface GaugeSegmentGeometry extends GaugeSegment {
  /** The value it is drawn from: negative or non-finite counts as 0. */
  drawn: number
  startPct: number
  widthPct: number
}

export interface GaugeTickGeometry extends GaugeTick {
  pct: number
  /** Interior ticks cut a notch into the track; the two ends do not. */
  notch: boolean
}

export interface GaugeBarModel {
  /** False for `null` / NaN: draw an empty track and say "not measured". */
  measured: boolean
  /** The caller's value, unclamped (what the text says). `null` when not measured. */
  value: number | null
  min: number
  max: number
  /** `aria-valuenow`: the value clamped INTO the domain (ARIA requires min <= now <= max). */
  valueNow: number | null
  /** Fill width / marker position, 0-100. 0 when not measured (and then nothing is drawn). */
  pct: number
  segments: GaugeSegmentGeometry[]
  /** The target at its position (0-100); `null` without a target or when not measured. */
  target: (GaugeTarget & { pct: number }) | null
  ticks: GaugeTickGeometry[]
}

export function buildGaugeBarModel(input: GaugeBarModelInput): GaugeBarModel {
  const domain = resolveDomain(input.domain)
  const [min, max] = domain
  const measured = input.value !== null && !Number.isNaN(input.value)
  const value = measured ? (input.value as number) : null

  const segments: GaugeSegmentGeometry[] = []
  if (measured && input.segments) {
    // Stack in domain units from `min`. Each edge is clamped on its own, so
    // the widths telescope: they sum to exactly the clamped end of the stack,
    // and a stack larger than the domain is cut at the track's end.
    let cumulative = 0
    for (const seg of input.segments) {
      const drawn = Number.isFinite(seg.value) && seg.value > 0 ? seg.value : 0
      const startPct = positionPct(min + cumulative, domain)
      cumulative += drawn
      const endPct = positionPct(min + cumulative, domain)
      segments.push({ ...seg, drawn, startPct, widthPct: endPct - startPct })
    }
  }

  const ticks: GaugeTickGeometry[] = (input.ticks ?? [])
    .filter((t) => Number.isFinite(t.value))
    .map((t) => {
      const pct = positionPct(t.value, domain)
      return { ...t, pct, notch: pct > 0 && pct < 100 }
    })
    .sort((a, b) => a.pct - b.pct)

  return {
    measured,
    value,
    min,
    max,
    valueNow: value === null ? null : clamp(value, min, max),
    pct: value === null ? 0 : positionPct(value, domain),
    segments,
    target:
      measured && input.target && Number.isFinite(input.target.value)
        ? { ...input.target, pct: positionPct(input.target.value, domain) }
        : null,
    ticks,
  }
}

/** The default number format: integers as they are, anything else to one decimal. */
export function formatGaugeNumber(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(1)
}

export const NOT_MEASURED = 'not measured'

/**
 * `aria-valuetext`: the true value (not the clamped one) against the domain,
 * then the segments, then the target — everything the bar shows, in words.
 */
export function gaugeValueText(
  model: GaugeBarModel,
  format: (v: number) => string = formatGaugeNumber,
): string {
  if (model.value === null) return NOT_MEASURED
  const parts = [`${format(model.value)} of ${format(model.max)}`]
  if (model.segments.length) {
    parts.push(model.segments.map((s) => `${s.label} ${format(s.drawn)}`).join(', '))
  }
  if (model.target) parts.push(`${model.target.label} ${format(model.target.value)}`)
  return parts.join('; ')
}

// ── The scale under the bar ────────────────────────────────────────────────

/**
 * How much of each tick's text the scale can show at the width it has.
 *   full:   "AT RISK · 33"
 *   values: "33" — the band names do not fit between their neighbours
 *   ends:   only the first and last value — not even the numbers fit
 */
export type TickLabelLevel = 'full' | 'values' | 'ends'

export type TickAnchor = 'start' | 'middle' | 'end'

export interface TickLabel {
  value: number
  pct: number
  text: string
  anchor: TickAnchor
  /**
   * The measured left edge in px, pushed back inside the track where a
   * centred label would hang off an end. `null` when nothing was measured:
   * the label is then placed by its `anchor` alone.
   */
  leftPx: number | null
}

export interface TickLabelLayout {
  level: TickLabelLevel
  labels: TickLabel[]
}

/** Minimum clear space between two tick labels, in px. */
export const TICK_LABEL_GAP_PX = 6

export function tickText(tick: GaugeTick, level: TickLabelLevel, format: (v: number) => string): string {
  return level === 'full' && tick.label ? `${tick.label} · ${format(tick.value)}` : format(tick.value)
}

/**
 * The first tick reads from the track's left edge, the last ends at its right
 * edge, and every interior one is centred on its notch — each label keeps
 * space on both sides of its own tick, where a left-aligned "HEALTHY · 70"
 * would run into the "100" at the end.
 */
export function tickAnchor(pct: number): TickAnchor {
  return pct <= 0 ? 'start' : pct >= 100 ? 'end' : 'middle'
}

/**
 * The richest level whose labels all fit, measured.
 *
 * `measure` is the text's rendered width in px (the page's real font — on
 * the Linux CI runner that is DejaVu Sans, wider than Windows' Segoe UI, so
 * a scale that fits one can collide on the other). `trackPx` is the scale's
 * width. With either unknown (jsdom, or before layout) the answer is `full`,
 * which is what the copied meters always drew.
 */
export function layoutTickLabels(
  ticks: readonly GaugeTickGeometry[],
  format: (v: number) => string,
  trackPx: number | null,
  measure: ((text: string) => number) | null,
): TickLabelLayout {
  const build = (level: TickLabelLevel): TickLabel[] => {
    const kept = level === 'ends' ? ticks.filter((_, i) => i === 0 || i === ticks.length - 1) : ticks
    return kept.map((t) => ({
      value: t.value,
      pct: t.pct,
      text: tickText(t, level, format),
      anchor: tickAnchor(t.pct),
      leftPx: null,
    }))
  }
  if (trackPx === null || trackPx <= 0 || measure === null) return { level: 'full', labels: build('full') }

  // The labels placed in px, or `null` when two of them collide.
  const place = (labels: TickLabel[]): TickLabel[] | null => {
    let previousRight = -Infinity
    const placed: TickLabel[] = []
    for (const l of labels) {
      const w = measure(l.text)
      const at = (l.pct / 100) * trackPx
      // A centred label that would hang off either end is pushed back in.
      const wanted = l.anchor === 'start' ? at : l.anchor === 'end' ? at - w : at - w / 2
      const left = clamp(wanted, 0, Math.max(0, trackPx - w))
      if (w > trackPx || left < previousRight + TICK_LABEL_GAP_PX) return null
      previousRight = left + w
      placed.push({ ...l, leftPx: left })
    }
    return placed
  }
  for (const level of ['full', 'values'] as const) {
    const labels = place(build(level))
    if (labels) return { level, labels }
  }
  // Last resort: the two ends, which any track wider than two numbers holds.
  return { level: 'ends', labels: place(build('ends')) ?? build('ends') }
}

// ── The ring (`RingGauge`) ─────────────────────────────────────────────────

export type RingTone = 'good' | 'warn' | 'bad'

/** `PassRateGauge`'s bands: 95 and up is good, 80 and up is a warning, below is bad. */
export const PASS_RATE_BANDS: GaugeBands = { direction: 'higher-is-better', thresholds: [80, 95] }

/**
 * A ring `tone` function from bands (with their direction): for the release
 * gate's risk score, `bandsTone({ direction: 'lower-is-better', thresholds:
 * [40, 70] })`. An unmeasured value never reaches it.
 */
export function bandsTone(bands: GaugeBands): (v: number) => RingTone {
  return (v) => {
    const t = toneForValue(v, bands)
    return t === 'good' || t === 'warn' ? t : 'bad'
  }
}

/**
 * The widest text the ring's hole holds at `size`. Recharts lays the ring out
 * in a box inset by its 5 px margin, from 65 % to 100 % of the radius, and
 * centres the 10 px bar in that band: the hole's edge is at 0.4125 x (size -
 * 10) - 5 (40.4 px at 120, against the 40.75 Recharts draws). The text sits
 * just off the centre, so it gets 85 % of that diameter.
 */
export function ringTextWidth(size: number): number {
  return Math.max(0, 2 * (0.4125 * (size - 10) - 5) * 0.85)
}
