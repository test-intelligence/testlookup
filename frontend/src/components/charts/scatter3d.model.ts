/**
 * The 3D test scatter's model (VIZ-508): where each test sits in the unit
 * cube, the axes' ticks in words, the quadrant groups, and the view's words.
 * Pure: no React, no DOM, no three — the engine (`engines/three/scatter3d.ts`)
 * draws what this returns, and this is unit-tested without WebGL.
 *
 * The three axes are the 2D scatter's three measures, each on the scale the
 * 2D chart reads it on:
 *   - x: the p95 duration, log10 over whole decades (`logExtent`), floored at
 *     `X_FLOOR_MS` exactly as the 2D plot draws a sub-millisecond p95;
 *   - y (up): the failure rate on its unit's fixed range (0..100 for percent);
 *   - z: the executions, log10 over whole decades — the 2D chart's symbol
 *     size, made a position.
 * Each is normalised to 0..1, so the engine draws one unit cube whatever the
 * data. A degenerate extent (one test, every value equal) still gets a
 * decade (log) or the unit's range (linear), never a division by zero.
 *
 * Quadrants are the 2D chart's (`quadrantOf`, strictly above the medians),
 * drawn with the same colours and shapes (`quadrantColor`, `QUADRANT_SYMBOLS`),
 * so the key under the plot is the same key.
 */
import type { PointsAxis, PointsChart, VizAxisUnit } from '@/lib/viz/contracts'
import { formatNumber } from '@/utils/formatters'
import { axisValueFormatter } from './chartText'
import { QUADRANTS, X_FLOOR_MS, quadrantOf, type Quadrant } from './testScatter.model'
import { SCATTER_DENSE_POINTS, logExtent, quadrantColor } from './engines/echarts/scatterOption'
import type { ChartTokens } from './tokens'

// ── Words ────────────────────────────────────────────────────────────────────

export const VIEW_3D_LABEL = 'View in 3D'
export const VIEW_2D_LABEL = 'Back to 2D'
export const VIEW_LABELS = [VIEW_3D_LABEL, VIEW_2D_LABEL] as const
export const RESET_VIEW_LABEL = 'Reset view'
export const ROTATE_HINT = 'Drag to rotate.'
export const EXPORT_NOTE = 'Export and print use the 2D chart.'
export const NO_WEBGL_NOTICE = 'The 3D view needs WebGL 2, which this browser does not offer here. Showing the 2D chart.'
export const CONTEXT_LOST_NOTICE = 'The 3D view stopped: the browser took its graphics context back. Showing the 2D chart.'

/** Why the 3D view gave up: the host goes back to 2D and says so. */
export type Scatter3DUnavailable = 'no-webgl' | 'context-lost'

export function unavailableNotice(reason: Scatter3DUnavailable): string {
  return reason === 'context-lost' ? CONTEXT_LOST_NOTICE : NO_WEBGL_NOTICE
}

/** The plot's name for assistive tech: what the three axes are. */
export function scatter3DDescription(chart: PointsChart): string {
  const n = chart.points.length
  return `3D scatter of ${formatNumber(n)} ${n === 1 ? 'test' : 'tests'}: ${chart.x.label} across, ${chart.y.label} up, ${chart.size.label} in depth.`
}

// ── Layout ───────────────────────────────────────────────────────────────────

/** One tick: its place along the axis (0..1) and its text. */
export interface Scatter3DTick {
  at: number
  text: string
}

export interface Scatter3DAxis {
  title: string
  ticks: Scatter3DTick[]
}

export interface Scatter3DLayout {
  /** x, y, z per point, in data order, each in 0..1. */
  positions: number[]
  /** Every point's diameter, CSS px (one size: executions are the depth now, not the area). */
  pointSize: number
  /** Data indices per quadrant, all four keys; with no medians every test is `fast-stable` (the 2D rule). */
  groups: Record<Quadrant, number[]>
  x: Scatter3DAxis
  y: Scatter3DAxis
  z: Scatter3DAxis
}

/** An axis' value -> 0..1, and its ticks. */
interface AxisScale {
  at: (value: number) => number
  ticks: Scatter3DTick[]
}

/** At most this many decades are labelled; past it, every other one (a 5,000-point suite spans 1 ms .. 10 min). */
const MAX_DECADE_TICKS = 6
/** Linear ticks: the ends and three between (0, 25, 50, 75, 100 %). */
const LINEAR_TICKS = 4

const clamp01 = (t: number) => (Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0)

/** A log axis over whole decades around `values` (each floored at `floor`). */
export function logScale(values: readonly number[], floor: number, unit: VizAxisUnit): AxisScale {
  const [min, max] = logExtent(values.map((v) => Math.max(v, floor)))
  // Whole decades (`logExtent` snaps to them): rounded, so a tick sits exactly on 0 and 1.
  const low = Math.round(Math.log10(min))
  const span = Math.round(Math.log10(max)) - low
  const step = span > MAX_DECADE_TICKS ? Math.ceil(span / MAX_DECADE_TICKS) : 1
  const text = axisValueFormatter(unit)
  const ticks: Scatter3DTick[] = []
  for (let d = 0; d <= span; d += step) ticks.push({ at: d / span, text: text(10 ** (low + d)) })
  return { at: (value) => clamp01((Math.log10(Math.max(value, floor)) - low) / span), ticks }
}

/** A linear axis on its unit's fixed range (0..100 for percent, 0..1 for a ratio), else 0 to the largest value. */
export function linearScale(axis: Pick<PointsAxis, 'unit'>, values: readonly number[]): AxisScale {
  const top = axis.unit === 'percent' ? 100 : axis.unit === 'ratio' ? 1 : Math.max(0, ...values.filter(Number.isFinite))
  const max = top > 0 ? top : 1
  const text = axisValueFormatter(axis.unit)
  const ticks: Scatter3DTick[] = []
  for (let i = 0; i <= LINEAR_TICKS; i++) ticks.push({ at: i / LINEAR_TICKS, text: text((max * i) / LINEAR_TICKS) })
  return { at: (value) => clamp01(value / max), ticks }
}

function axisScale(axis: PointsAxis, values: readonly number[], floor: number): AxisScale {
  return axis.scale === 'log' ? logScale(values, floor, axis.unit) : linearScale(axis, values)
}

/** A point's diameter, CSS px: the 2D chart's mid-size symbol, smaller past its dense threshold. */
export const SCATTER_3D_POINT = { normal: 10, dense: 6 } as const

/** Executions are a count of at least one: drawn on whole decades from 1. */
const SIZE_FLOOR = 1

/** Where every test sits in the unit cube, the quadrant groups, and the three axes' ticks. */
export function scatter3DLayout(chart: PointsChart): Scatter3DLayout {
  const xFloor = chart.x.scale === 'log' && chart.x.unit === 'ms' ? X_FLOOR_MS : Number.MIN_VALUE
  const x = axisScale(chart.x, chart.points.map((p) => p.x), xFloor)
  const y = axisScale(chart.y, chart.points.map((p) => p.y), Number.MIN_VALUE)
  const z = logScale(chart.points.map((p) => p.size), SIZE_FLOOR, 'count')
  const positions: number[] = []
  const groups: Record<Quadrant, number[]> = { 'slow-flaky': [], 'fast-flaky': [], 'slow-stable': [], 'fast-stable': [] }
  chart.points.forEach((point, index) => {
    positions.push(x.at(point.x), y.at(point.y), z.at(point.size))
    groups[chart.medians ? quadrantOf(point, chart.medians) : 'fast-stable'].push(index)
  })
  return {
    positions,
    pointSize: chart.points.length > SCATTER_DENSE_POINTS ? SCATTER_3D_POINT.dense : SCATTER_3D_POINT.normal,
    groups,
    x: { title: chart.x.label, ticks: x.ticks },
    y: { title: chart.y.label, ticks: y.ticks },
    z: { title: chart.size.label, ticks: z.ticks },
  }
}

// ── Colours ──────────────────────────────────────────────────────────────────

/** The resolved token strings the engine paints with (it turns each into a three `Color`). */
export interface Scatter3DColors {
  quadrants: Record<Quadrant, string>
  /** The axes' edges and tick marks. */
  frame: string
  /** The rest of the cube and the gridlines on its back walls. */
  grid: string
}

/** The 2D chart's quadrant colours and the chart grid, from resolved tokens only. */
export function scatter3DColors(tokens: ChartTokens): Scatter3DColors {
  const quadrants = {} as Record<Quadrant, string>
  for (const q of QUADRANTS) quadrants[q] = quadrantColor(q, tokens)
  return { quadrants, frame: tokens.axis, grid: tokens.grid }
}

// ── The engine's contract ────────────────────────────────────────────────────

export interface Scatter3DCallbacks {
  /** The browser took the WebGL context away (`webglcontextlost`). */
  onContextLost: () => void
  /** The camera moved: its azimuth around the cube, in degrees (rounded). */
  onCameraChange?: (azimuthDegrees: number) => void
}

/** What `mountScatter3D` hands back. Every method is a no-op after `dispose`. */
export interface Scatter3DHandle {
  update: (layout: Scatter3DLayout) => void
  setColors: (colors: Scatter3DColors) => void
  resize: () => void
  /** The test under a point of the canvas (CSS px from the host's top left), or `null`. */
  pick: (x: number, y: number) => number | null
  resetView: () => void
  dispose: () => void
}

export interface Scatter3DEngine {
  mountScatter3D: (host: HTMLElement, layout: Scatter3DLayout, colors: Scatter3DColors, callbacks: Scatter3DCallbacks) => Scatter3DHandle
}
