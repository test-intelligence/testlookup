/**
 * `GaugeBar` (VIZ-104, K4) — one horizontal meter for every "score on a bar"
 * the product draws: the six verdict meters (confidence, health x3,
 * stability, risk), the Overview verdict's mini pass-rate bar, the
 * IntelligenceHub table meter and the KPI slots that decorate a real scalar.
 * Those were eight hand-copied bars; `gaugeBar.model.ts` holds the rules they
 * each re-derived, this file only draws them.
 *
 *   variant="fill"    a bar filled from the domain's start to the value — a
 *                     single fill, or stacked `segments`;
 *   variant="marker"  the whole track is the scale (usually a gradient) and a
 *                     ring marks the value on it.
 *
 * The meter is a `role="meter"` with `aria-valuenow/min/max` and a
 * `aria-valuetext` in words (the value, its segments, its target). A value
 * that was NOT measured (`null`) is not a meter reading at all: it draws an
 * empty track with no fill and no marker and is announced as "not measured"
 * (`role="img"`: ARIA requires a meter to HAVE a value) — never as 0.
 *
 * The accessible name is `label`. The pages keep their own visible caption
 * (the verdict eyebrow, the KPI cell label, the table's column header), so
 * `label` must repeat that caption's words (WCAG 2.5.3); `showLabel` draws it
 * here instead, for a gauge with no caption of its own.
 *
 * Sizes: `md` is the verdict meter (6 px track, 14 px marker, a scale row
 * under it); `sm` is the 4 px bar of the Overview verdict and of the
 * `DimensionTile`s a follow-up will fold in. `thickness` and `width` override
 * the two for a table cell (IntelligenceHub's is 56 x 5 px, outlined).
 *
 * Tick labels are measured in the reader's font (`textMeasure.ts`): the scale
 * drops its band names, then its interior numbers, before two labels collide
 * — on the Linux CI runner DejaVu Sans is wider than the Windows font the
 * copied meters were tuned on. Every text here is a React text node.
 */
import { useCallback, useId, useLayoutEffect, useMemo, useState, type CSSProperties } from 'react'
import {
  buildGaugeBarModel,
  formatGaugeNumber,
  GAUGE_BAR_SIZES,
  GAUGE_TICK_FONT_PX,
  GAUGE_TONE_COLOR,
  gaugeValueText,
  INLINE_TRACK_WIDTH,
  layoutTickLabels,
  NOT_MEASURED,
  toneForValue,
  type GaugeBands,
  type GaugeBarSize,
  type GaugeBarVariant,
  type GaugeGradient,
  type GaugeSegment,
  type GaugeTarget,
  type GaugeTick,
  type GaugeTone,
  type TickLabel,
} from './gaugeBar.model'
import { useTextMeasure } from './textMeasure'
import { CHART_VARS } from './tokens'

export type {
  GaugeBands,
  GaugeBarSize,
  GaugeBarVariant,
  GaugeGradient,
  GaugeSegment,
  GaugeTarget,
  GaugeTick,
  GaugeTone,
} from './gaugeBar.model'

export interface GaugeBarProps {
  /** The reading. `null` = not measured: an empty track, never a bar at 0. */
  value: number | null
  /** The accessible name. Repeat the visible caption's words. */
  label: string
  /** Default `[0, 100]`. */
  domain?: readonly [number, number]
  /** Default `fill`. */
  variant?: GaugeBarVariant
  /** A theme gradient for the fill (anchored to the TRACK) or the marker variant's track. */
  gradient?: GaugeGradient
  /**
   * The fill / marker colour: a fixed tone, or bands (with their direction)
   * that pick one from the value. Default `neutral`. Colour is never the only
   * cue: the page's pill / number carries the band in words.
   */
  tone?: GaugeTone | GaugeBands
  /** A stacked bar, in domain units. Each takes its tone, or the series colour at its index. */
  segments?: readonly GaugeSegment[]
  /** A mark at a target value (named in the value text). */
  target?: GaugeTarget
  /** Scale marks: a notch in the track and real text under it. */
  ticks?: readonly GaugeTick[]
  /** Default `md`. */
  size?: GaugeBarSize
  /** Number format for the value text, the ticks and `showValue`. */
  format?: (v: number) => string
  /** Replaces the whole `aria-valuetext` (e.g. "72 of 100, Healthy"). */
  valueText?: string
  /** Draw the formatted value to the right of the bar (a table cell). */
  showValue?: boolean
  /** Visible text for an unmeasured value when `showValue` is on. Default "—". */
  notMeasuredText?: string
  /** Draw `label` above the bar and name the meter by it. */
  showLabel?: boolean
  /** Track width. Default: the container's. */
  width?: number | string
  /** Track thickness in px, overriding `size`. */
  thickness?: number
  /** A 1 px border around the track (inside `thickness`). */
  outlined?: boolean
  /** `tint`: the track is the tone at 18 % — the Overview verdict's look. Default `neutral`. */
  track?: 'neutral' | 'tint'
  className?: string
}

const TRACK_BG = 'var(--color-bg-secondary)'
const gradientVar = (g: GaugeGradient) => `var(--gradient-${g})`
const mix = (color: string, pct: number) => `color-mix(in srgb, ${color} ${pct}%, transparent)`

/** Reads the scale row's width and letter spacing once it has a layout (and again when it resizes). */
function useScaleBox(): [(node: HTMLDivElement | null) => void, { width: number; letterSpacing: number } | null] {
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  const [box, setBox] = useState<{ width: number; letterSpacing: number } | null>(null)
  useLayoutEffect(() => {
    if (!node) return
    const read = () => {
      const width = node.getBoundingClientRect().width
      if (width <= 0) return
      const spacing = Number.parseFloat(getComputedStyle(node).letterSpacing)
      setBox((prev) => {
        const next = { width, letterSpacing: Number.isFinite(spacing) ? spacing : 0 }
        return prev && prev.width === next.width && prev.letterSpacing === next.letterSpacing ? prev : next
      })
    }
    read()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(read)
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])
  return [setNode, box]
}

function TickRow({
  ticks,
  format,
}: {
  ticks: ReturnType<typeof buildGaugeBarModel>['ticks']
  format: (v: number) => string
}) {
  const [measureRef, measure] = useTextMeasure<HTMLDivElement>()
  const [boxRef, box] = useScaleBox()
  const ref = useCallback(
    (el: HTMLDivElement | null) => {
      measureRef(el)
      boxRef(el)
    },
    [measureRef, boxRef],
  )
  const layout = useMemo(() => {
    // The row is set in upper case with letter spacing: measure what is drawn.
    const measureText =
      measure && box ? (text: string) => measure(text.toUpperCase(), GAUGE_TICK_FONT_PX) + box.letterSpacing * text.length : null
    return layoutTickLabels(ticks, format, box?.width ?? null, measureText)
  }, [ticks, format, measure, box])

  const place = (l: TickLabel): CSSProperties =>
    l.leftPx !== null
      ? { left: l.leftPx }
      : l.anchor === 'start'
        ? { left: `${l.pct}%` }
        : l.anchor === 'end'
          ? { right: `${100 - l.pct}%` }
          : { left: `${l.pct}%`, transform: 'translateX(-50%)' }

  return (
    <div
      ref={ref}
      data-gauge-ticks={layout.level}
      className="relative mt-1.5 uppercase text-[var(--color-text-faint)] whitespace-nowrap"
      // An explicit line height: the row's height must not depend on which font the reader has.
      style={{ fontSize: GAUGE_TICK_FONT_PX, lineHeight: '14px', height: 14, letterSpacing: 'var(--tracking-wide)' }}
    >
      {layout.labels.map((l) => (
        <span key={l.value} data-gauge-tick={l.value} className="absolute top-0" style={place(l)}>
          {l.text}
        </span>
      ))}
    </div>
  )
}

export default function GaugeBar({
  value,
  label,
  domain,
  variant = 'fill',
  gradient,
  tone,
  segments,
  target,
  ticks,
  size = 'md',
  format = formatGaugeNumber,
  valueText,
  showValue = false,
  notMeasuredText = '—',
  showLabel = false,
  width,
  thickness,
  outlined = false,
  track = 'neutral',
  className,
}: GaugeBarProps) {
  const labelId = useId()
  const model = useMemo(
    () => buildGaugeBarModel({ value, domain, segments, target, ticks }),
    [value, domain, segments, target, ticks],
  )
  const toneName: GaugeTone = !model.measured
    ? 'neutral'
    : typeof tone === 'string'
      ? tone
      : tone
        ? toneForValue(model.value, tone)
        : 'neutral'
  const toneColor = GAUGE_TONE_COLOR[toneName]
  const dims = GAUGE_BAR_SIZES[size]
  const height = thickness ?? dims.thickness

  const trackBg =
    variant === 'marker' && gradient
      ? gradientVar(gradient)
      : track === 'tint' && model.measured
        ? mix(toneColor, 18)
        : TRACK_BG

  // The fill shows the part of the gradient UNDER it: sized to the whole
  // track, not squeezed into the fill. A risk of 20 ends in the "safe" colour;
  // the copied meters compressed the full red-to-green ramp into any width, so
  // a fill of 20 % still ended red.
  const fillStyle: CSSProperties = gradient
    ? {
        width: `${model.pct}%`,
        backgroundImage: gradientVar(gradient),
        backgroundSize: model.pct > 0 ? `${(100 / model.pct) * 100}% 100%` : undefined,
      }
    : { width: `${model.pct}%`, background: toneColor }

  const semantics = model.measured
    ? {
        role: 'meter' as const,
        'aria-valuenow': model.valueNow ?? undefined,
        'aria-valuemin': model.min,
        'aria-valuemax': model.max,
        'aria-valuetext': valueText ?? gaugeValueText(model, format),
      }
    : { role: 'img' as const }
  const name = model.measured ? label : `${label}: ${NOT_MEASURED}`

  const inline = showValue
  return (
    <div
      {...semantics}
      {...(showLabel ? { 'aria-labelledby': labelId } : { 'aria-label': name })}
      data-gauge-bar={variant}
      data-measured={model.measured}
      data-tone={toneName}
      className={[inline ? 'inline-flex items-center gap-2 whitespace-nowrap' : 'block', className]
        .filter(Boolean)
        .join(' ')}
    >
      {showLabel && (
        <div id={labelId} className="text-[11px] text-[var(--color-text-muted)] mb-1">
          {label}
          {/* `aria-labelledby` replaces `aria-label`, so the unmeasured state is said here. */}
          {!model.measured && <span className="sr-only">: {NOT_MEASURED}</span>}
        </div>
      )}
      <div
        data-gauge-track
        className={['relative rounded-full', inline ? 'inline-block shrink-0' : '', outlined ? 'border border-[var(--color-border)]' : '']
          .filter(Boolean)
          .join(' ')}
        style={{ width: width ?? (inline ? INLINE_TRACK_WIDTH : '100%'), height, background: trackBg }}
      >
        {/* The clip keeps the fill and segments inside the rounded track; the
            marker and the target sit outside it, so they may overhang. */}
        <div className="absolute inset-0 rounded-full overflow-hidden">
          {model.measured && variant === 'fill' && !segments && model.pct > 0 && (
            <i
              data-gauge-fill
              className="block h-full rounded-full motion-safe:transition-[width] motion-safe:duration-300"
              style={fillStyle}
            />
          )}
          {model.segments.map((s, i) => (
            <i
              key={i}
              data-gauge-segment={s.label}
              title={`${s.label}: ${format(s.drawn)}`}
              className="absolute top-0 h-full block"
              style={{
                left: `${s.startPct}%`,
                width: `${s.widthPct}%`,
                background: s.tone ? GAUGE_TONE_COLOR[s.tone] : CHART_VARS.series[i % CHART_VARS.series.length],
                // A card-coloured cut between segments: adjacent pieces stay
                // apart without relying on their hues differing.
                boxShadow: i > 0 && s.widthPct > 0 ? `inset 1px 0 0 ${CHART_VARS.card}` : undefined,
              }}
            />
          ))}
          {model.ticks
            .filter((t) => t.notch)
            .map((t) => (
              <i
                key={t.value}
                data-gauge-notch={t.value}
                className="absolute top-0 h-full block w-px"
                style={{ left: `${t.pct}%`, background: CHART_VARS.card }}
              />
            ))}
        </div>
        {model.target && (
          <i
            data-gauge-target={model.target.value}
            title={`${model.target.label} ${format(model.target.value)}`}
            className="absolute block w-0.5 rounded-full"
            style={{
              left: `${model.target.pct}%`,
              top: -3,
              bottom: -3,
              transform: 'translateX(-50%)',
              background: CHART_VARS.text,
            }}
          />
        )}
        {model.measured && variant === 'marker' && (
          <span
            data-gauge-marker
            className="absolute rounded-full"
            style={{
              top: '50%',
              left: `${model.pct}%`,
              transform: 'translate(-50%, -50%)',
              width: dims.marker,
              height: dims.marker,
              background: CHART_VARS.card,
              border: `2px solid ${toneColor}`,
              boxShadow: `0 0 0 2px ${mix(toneColor, 25)}`,
            }}
          />
        )}
      </div>
      {showValue && (
        <span data-gauge-value className="text-[11.5px] tabular-nums text-[var(--color-text-muted)]">
          {model.value === null ? notMeasuredText : format(model.value)}
        </span>
      )}
      {!inline && model.ticks.length > 0 && <TickRow ticks={model.ticks} format={format} />}
    </div>
  )
}
