/**
 * `RingGauge` (VIZ-104, K4b) — one value on a 0-100 ring: `PassRateGauge`,
 * generalised so the release gate's risk score can use it.
 *
 * With no props but `value` it draws exactly what `PassRateGauge` drew — the
 * gallery screenshots that ring, and `RingGauge.defaultRender.test.tsx` pins
 * every drawn byte against a snapshot written before this file existed. What
 * a caller can change is what the risk arc needs: the `caption`, the number
 * `format` and the `tone` bands — a pass rate is good HIGH, a risk score is
 * good LOW, so the direction is the caller's, through `bandsTone`.
 *
 * Two things differ from `PassRateGauge`, neither of them drawn:
 *   - Recharts' `accessibilityLayer` is OFF (RULES.md): on a gauge it was an
 *     unnamed `role="application"` tab stop that did nothing. The ring is a
 *     named `role="meter"` instead, with its value in `aria-value*`.
 *   - `null` is "not measured": an empty ring and a dash, announced as not
 *     measured — never a red ring at 0.
 * `PassRateGauge` keeps both old behaviours (`legacyApplicationLayer`) until
 * its tests and the gallery's LEGACY_APPLICATION_LAYER ratchet move with it.
 *
 * The number in the middle is measured in the reader's font and shrunk to
 * fit inside the ring (DejaVu Sans on the Linux CI runner is wider than the
 * Windows font: "100.0%" at 20 px bold overran a 120 px ring there).
 */
import { useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'
import { PolarAngleAxis, RadialBarChart, RadialBar, ResponsiveContainer } from 'recharts'
import { bandsTone, NOT_MEASURED, PASS_RATE_BANDS, ringTextWidth, type RingTone } from './gaugeBar.model'
import { measureTextWidth } from './textMeasure'
import { CHART_VARS } from './tokens'
import { useChartAnimation } from './motion'

export type { RingTone } from './gaugeBar.model'

const passRateTone = bandsTone(PASS_RATE_BANDS)
const formatPercent = (v: number) => `${v.toFixed(1)}%`

const RING_COLOR: Record<RingTone, string> = {
  good: CHART_VARS.status.passed,
  warn: CHART_VARS.status.broken,
  bad: CHART_VARS.status.failed,
}

/** The value's font size (`text-xl`) and weight; the caption's (`text-[10px]`). */
const VALUE_FONT = { px: 20, weight: 700 }
const CAPTION_FONT = { px: 10, weight: 400 }

/**
 * The font size that fits `text` in `available` px, measured in the span's
 * own font once it is laid out; `undefined` (the class's size) until then, in
 * jsdom, and whenever it already fits.
 */
function useFittedFontSize(
  text: string,
  font: { px: number; weight: number },
  available: number,
  enabled: boolean,
): [RefObject<HTMLSpanElement | null>, number | undefined] {
  const ref = useRef<HTMLSpanElement | null>(null)
  const [fitted, setFitted] = useState<number | undefined>(undefined)
  useLayoutEffect(() => {
    const node = ref.current
    if (!enabled || !node || node.getBoundingClientRect().width === 0) return setFitted(undefined)
    const width = measureTextWidth(text, `${font.weight} ${font.px}px ${getComputedStyle(node).fontFamily}`)
    setFitted(width === null || width <= available ? undefined : Math.floor(((font.px * available) / width) * 10) / 10)
  }, [text, font.px, font.weight, available, enabled])
  return [ref, fitted]
}

export interface RingGaugeProps {
  /** 0-100. `null` (or NaN) = not measured. The arc is clamped; the text keeps the true value. */
  value: number | null
  /** The small line under the number. Default "Pass Rate". */
  caption?: string
  /** The accessible name. Default: the caption. */
  label?: string
  /** Default `v.toFixed(1)%`. */
  format?: (v: number) => string
  /** The arc's colour band. Default: 95 / 80, higher is better (`PASS_RATE_BANDS`). */
  tone?: (v: number) => RingTone
  /** The square's side, px. Default 120. */
  size?: number
  /** Forwarded to Recharts' `isAnimationActive`; `undefined` keeps Recharts' default. */
  animate?: boolean
}

interface ViewProps extends RingGaugeProps {
  /** `PassRateGauge` only: Recharts' accessibility layer on, no meter semantics, NaN drawn as before. */
  legacyApplicationLayer?: boolean
}

/** @internal The one implementation; `RingGauge` and `PassRateGauge` are its two faces. */
export function RingGaugeView({
  value,
  caption = 'Pass Rate',
  label,
  format = formatPercent,
  tone = passRateTone,
  size = 120,
  animate: requestedAnimate,
  legacyApplicationLayer = false,
}: ViewProps) {
  const animate = useChartAnimation(requestedAnimate)
  const measured = value !== null && (legacyApplicationLayer || !Number.isNaN(value))
  const v = measured ? (value as number) : 0
  // `PassRateGauge` never had a not-measured state: its NaN fell through to the red band.
  const color = measured ? RING_COLOR[legacyApplicationLayer && Number.isNaN(v) ? 'bad' : tone(v)] : CHART_VARS.neutral
  // ONE ring: the value arc drawn over its own background track. Two data
  // rows (a 100 "track" row plus the value) made Recharts lay out two
  // concentric rings, so the value sat beside the track instead of on it --
  // invisible while the track was near-black, obvious once it became the grid
  // colour. The fixed 0-100 angle axis makes the arc length the percentage.
  const clamped = Math.min(100, Math.max(0, Number.isFinite(v) ? v : 0))
  const data = [{ value: clamped, fill: color }]
  const text = measured ? format(v) : '—'

  const fit = !legacyApplicationLayer
  const available = ringTextWidth(size)
  const [valueRef, valuePx] = useFittedFontSize(text, VALUE_FONT, available, fit)
  const [captionRef, captionPx] = useFittedFontSize(caption, CAPTION_FONT, available, fit)
  const fontSize = (px: number | undefined): CSSProperties | undefined => (px === undefined ? undefined : { fontSize: px })

  const name = label ?? caption
  const semantics = legacyApplicationLayer
    ? {}
    : measured
      ? {
          role: 'meter' as const,
          'aria-label': name,
          'aria-valuenow': clamped,
          'aria-valuemin': 0,
          'aria-valuemax': 100,
          'aria-valuetext': text,
        }
      : { role: 'img' as const, 'aria-label': `${name}: ${NOT_MEASURED}` }

  return (
    <div {...semantics} className="relative flex items-center justify-center" style={{ width: size, height: size }}>
      <ResponsiveContainer width="100%" height="100%">
        <RadialBarChart
          cx="50%" cy="50%"
          innerRadius="65%" outerRadius="100%"
          startAngle={210} endAngle={-30}
          data={data} barSize={10}
          accessibilityLayer={legacyApplicationLayer}
        >
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} axisLine={false} />
          <RadialBar
            dataKey="value"
            cornerRadius={5}
            background={{ fill: CHART_VARS.grid }}
            isAnimationActive={animate}
          />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span ref={valueRef} className="text-xl font-bold text-[var(--color-text)]" style={fontSize(valuePx)}>
          {text}
        </span>
        <span ref={captionRef} className="text-[10px] text-[var(--color-text-muted)]" style={fontSize(captionPx)}>
          {caption}
        </span>
      </div>
    </div>
  )
}

export default function RingGauge(props: RingGaugeProps) {
  return <RingGaugeView {...props} />
}
