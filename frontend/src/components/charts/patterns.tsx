/**
 * Status patterns for the SVG (Recharts) charts — the drawn half of "status is
 * never colour-only" (VIZ-102 / VIZ-105). The five status hues are only
 * 1.07–1.38:1 against each other, so each status fill carries its fixed decal
 * (`STATUS_ENCODING[s].decal`: solid / diagonal / crosshatch / dashes / dots),
 * cut out of the fill in the card colour — the same shapes the ECharts decals
 * draw on canvas (`echartsDecal`). Lines use `STATUS_ENCODING[s].dash`.
 *
 * Every chart instance prefixes its pattern ids (`useChartPatternIds`), so two
 * charts on one page never share — and so can never clobber — a pattern id.
 * `ChartLegend` draws each swatch with the SAME `url(#…)` fill (or dash) as
 * the marks it names.
 */
import { useId, type ReactNode } from 'react'
import type { VizStatus } from '@/lib/viz/contracts'
import { CHART_VARS, DECAL_TILE, STATUS_ENCODING, type DecalKind } from './tokens'

const TILE = DECAL_TILE

export interface PatternSpec {
  /** The full element id (already prefixed). */
  id: string
  /** Fill colour: a `var(--…)` reference from `CHART_VARS`. */
  color: string
  decal: DecalKind
}

function decalMarks(kind: DecalKind, cut: string): ReactNode {
  switch (kind) {
    case 'solid':
      return null
    case 'diagonal':
      return <rect x={0} y={0} width={2.5} height={TILE} fill={cut} />
    case 'crosshatch':
      return (
        <>
          <rect x={0} y={0} width={1.5} height={TILE} fill={cut} />
          <rect x={0} y={0} width={TILE} height={1.5} fill={cut} />
        </>
      )
    case 'dashes':
      return <rect x={1} y={3} width={4} height={2} fill={cut} />
    case 'dots':
      return <circle cx={4} cy={4} r={1.5} fill={cut} />
  }
}

/** `<pattern>` elements for `specs`; put them inside the chart's `<defs>`. */
export function renderPatterns(specs: readonly PatternSpec[]): ReactNode {
  return specs.map(({ id, color, decal }) => (
    <pattern
      key={id}
      id={id}
      data-chart-pattern={decal}
      width={TILE}
      height={TILE}
      patternUnits="userSpaceOnUse"
      patternTransform={decal === 'diagonal' || decal === 'crosshatch' ? 'rotate(45)' : undefined}
    >
      <rect width={TILE} height={TILE} fill={color} />
      {decalMarks(decal, CHART_VARS.card)}
    </pattern>
  ))
}

export const patternFill = (id: string) => `url(#${id})`

/** A per-instance id prefix, safe inside `url(#…)`. */
export function useChartPatternPrefix(): string {
  return `cp${useId().replace(/[^A-Za-z0-9_-]/g, '')}`
}

/** The pattern id of `status` under `prefix` (ends with `STATUS_ENCODING[status].patternId`). */
export const statusPatternId = (prefix: string, status: VizStatus) => `${prefix}-${STATUS_ENCODING[status].patternId}`

/** One pattern per status, under `prefix`. */
export function statusPatternSpecs(prefix: string, statuses: readonly VizStatus[]): PatternSpec[] {
  return statuses.map((status) => ({
    id: statusPatternId(prefix, status),
    color: CHART_VARS.status[status],
    decal: STATUS_ENCODING[status].decal,
  }))
}

export interface LegendEntry {
  key: string
  label: string
  /** For a status entry: `data-legend-status`, so tests and styles can find it. */
  status?: VizStatus
  /** A fill swatch (`url(#…)` or a colour)… */
  fill?: string
  /** …or a line swatch: its stroke and dash. */
  stroke?: string
  dash?: string
}

/**
 * The legend for a Recharts chart (pass as `<Legend content={() => <ChartLegend … />} />`).
 * Each swatch is drawn with the same fill / dash as the marks, so a reader who
 * cannot tell the hues apart matches the pattern instead.
 */
export function ChartLegend({ entries }: { entries: readonly LegendEntry[] }) {
  return (
    <ul
      data-chart-legend=""
      className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-3 text-xs text-[var(--color-text-secondary)]"
    >
      {entries.map((entry) => (
        <li key={entry.key} data-legend-status={entry.status} className="flex items-center gap-1.5">
          {entry.stroke ? (
            <svg width={24} height={10} aria-hidden="true" focusable="false">
              <line
                data-legend-swatch=""
                x1={1}
                y1={5}
                x2={23}
                y2={5}
                stroke={entry.stroke}
                strokeWidth={2}
                strokeDasharray={entry.dash}
              />
            </svg>
          ) : (
            <svg width={14} height={14} aria-hidden="true" focusable="false">
              <rect
                data-legend-swatch=""
                x={0.5}
                y={0.5}
                width={13}
                height={13}
                rx={2}
                fill={entry.fill}
                stroke={CHART_VARS.border}
              />
            </svg>
          )}
          <span>{entry.label}</span>
        </li>
      ))}
    </ul>
  )
}
