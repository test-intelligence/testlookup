/**
 * The heatmap's canvas decals must DRAW what the legend's SVG patterns draw.
 *
 * Why this test rasterises instead of comparing options: the five status hues
 * are only 1.07-1.38:1 against each other, so the decal is the one thing that
 * tells a status apart for many readers. The old `crosshatch` decal was a
 * distinct option object (`tokens.test.ts` checked the JSON was unique) and a
 * distinct `STATUS_ENCODING` name, yet ECharts drew it as horizontal dashes —
 * the same shape as `skipped` — because an ECharts decal is a grid of dash
 * ROWS, and `[[1, 5], [5, 1]]` rows are dashes, not crossing lines. Equal names
 * proved nothing; only the drawn shape counts.
 *
 * So both sides are turned into geometry and classified by shape:
 *   - the canvas side is tiled by ECharts' OWN decal code
 *     (`createOrUpdatePatternFromDecal`, SVG painter, so no canvas is needed),
 *     recording every mark it would paint;
 *   - the legend side is read from the `<pattern>` the rendered legend swatch
 *     actually points at.
 * Each is sampled over a 64 px square (rotation applied) and named by its
 * continuous line directions, or dash / dot shape.
 */
import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { VIZ_STATUSES, type VizStatus } from '@/lib/viz/contracts'
import HeatmapChart from '../../HeatmapChart'
import { CHART_VARS, STATUS_ENCODING, type ChartDecal, type DecalKind } from '../../tokens'
import { heatmapTooltipContent, type StatusMatrix } from './heatmapOption'

const engine = vi.hoisted(() => {
  const instance = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), dispatchAction: vi.fn() }
  return { instance, init: vi.fn(() => instance), load: vi.fn() }
})
vi.mock('../registry', () => ({ loadChartEngine: engine.load }))

// ── geometry ──────────────────────────────────────────────────────────────────

type Mark =
  | { kind: 'rect'; x: number; y: number; width: number; height: number }
  | { kind: 'circle'; cx: number; cy: number; r: number }

/** One repeating tile: marks in tile space, rotated (radians, clockwise on screen, as SVG and zrender do). */
interface Tile {
  width: number
  height: number
  rotation: number
  marks: Mark[]
}

interface DecalModule {
  createOrUpdatePatternFromDecal: (
    decal: Record<string, unknown>,
    api: unknown,
  ) => { rotation?: number; svgWidth: number; svgHeight: number } | null
}

/** The marks ECharts itself paints for `decal`, from its own tiling code. */
async function echartsTile(decal: ChartDecal): Promise<Tile> {
  // A non-literal specifier: echarts ships no typings for its lib/ paths.
  const { createOrUpdatePatternFromDecal } = (await import('echarts/lib/util/decal.js' as string)) as DecalModule
  const marks: Mark[] = []
  const zr = {
    painter: {
      type: 'svg',
      renderOneToVNode(symbol: { shape: { symbolType: string; x: number; y: number; width: number; height: number } }) {
        const { symbolType, x, y, width, height } = symbol.shape
        if (symbolType === 'circle') marks.push({ kind: 'circle', cx: x + width / 2, cy: y + height / 2, r: Math.min(width, height) / 2 })
        else if (symbolType === 'rect') marks.push({ kind: 'rect', x, y, width, height })
        else throw new Error(`unmodelled decal symbol ${symbolType}`)
        return null
      },
    },
  }
  const api = { getDevicePixelRatio: () => 1, getZr: () => zr }
  // A copy: ECharts writes its defaults into the object and caches by identity.
  const pattern = createOrUpdatePatternFromDecal({ ...decal }, api)
  if (!pattern) throw new Error('no pattern')
  return { width: pattern.svgWidth, height: pattern.svgHeight, rotation: pattern.rotation ?? 0, marks }
}

/** The marks an SVG `<pattern>` cuts out of its background. */
function svgTile(pattern: Element): Tile {
  const rotate = /rotate\(\s*(-?[\d.]+)/.exec(pattern.getAttribute('patternTransform') ?? '')
  const [background, ...rest] = Array.from(pattern.children)
  const backgroundFill = background.getAttribute('fill')
  const marks: Mark[] = rest
    .filter((mark) => mark.getAttribute('fill') !== backgroundFill)
    .map((mark) => {
      const n = (name: string) => Number(mark.getAttribute(name) ?? 0)
      if (mark.tagName.toLowerCase() === 'circle') return { kind: 'circle', cx: n('cx'), cy: n('cy'), r: n('r') }
      return { kind: 'rect', x: n('x'), y: n('y'), width: n('width'), height: n('height') }
    })
  return {
    width: Number(pattern.getAttribute('width')),
    height: Number(pattern.getAttribute('height')),
    rotation: rotate ? (Number(rotate[1]) * Math.PI) / 180 : 0,
    marks,
  }
}

const AREA = 64
const STEP = 0.5
const N = AREA / STEP

function covered(tile: Tile, wx: number, wy: number): boolean {
  // World -> tile space: the inverse of the tile's rotation.
  const cos = Math.cos(tile.rotation)
  const sin = Math.sin(tile.rotation)
  const u = wx * cos + wy * sin
  const v = -wx * sin + wy * cos
  const x = ((u % tile.width) + tile.width) % tile.width
  const y = ((v % tile.height) + tile.height) % tile.height
  return tile.marks.some((m) =>
    m.kind === 'rect'
      ? x >= m.x && x < m.x + m.width && y >= m.y && y < m.y + m.height
      : (x - m.cx) ** 2 + (y - m.cy) ** 2 <= m.r ** 2,
  )
}

/** Screen directions: `\` runs down-right, `/` runs up-right. */
const DIRECTIONS = [
  { name: '-', dx: 1, dy: 0 },
  { name: '|', dx: 0, dy: 1 },
  { name: '\\', dx: 1, dy: 1 },
  { name: '/', dx: 1, dy: -1 },
] as const

/** A line is a run of cut-out pixels this long (the square is 64 px). */
const LINE_PX = 40

interface Shape {
  name: string
  coverage: number
}

/** Names the shape a tile draws: `none`, `lines:<dirs>`, `dashes:-` / `dashes:|`, or `dots`. */
function classify(tile: Tile): Shape {
  const grid: boolean[][] = []
  let hits = 0
  for (let i = 0; i < N; i++) {
    grid.push([])
    for (let j = 0; j < N; j++) {
      const hit = covered(tile, i * STEP + STEP / 2, j * STEP + STEP / 2)
      grid[i].push(hit)
      if (hit) hits += 1
    }
  }
  const coverage = hits / (N * N)
  if (hits === 0) return { name: 'none', coverage }
  const longest = new Map<string, number>()
  for (const { name, dx, dy } of DIRECTIONS) {
    const run: number[][] = Array.from({ length: N }, () => new Array<number>(N).fill(0))
    let best = 0
    // Order the walk so the previous cell along (dx, dy) is always filled first.
    const js = dy < 0 ? [...Array(N).keys()].reverse() : [...Array(N).keys()]
    for (let i = 0; i < N; i++) {
      for (const j of js) {
        if (!grid[i][j]) continue
        const pi = i - dx
        const pj = j - dy
        const prev = pi >= 0 && pj >= 0 && pj < N ? run[pi][pj] : 0
        run[i][j] = prev + 1
        if (run[i][j] > best) best = run[i][j]
      }
    }
    longest.set(name, best * STEP * Math.hypot(dx, dy))
  }
  const lines = DIRECTIONS.map((d) => d.name).filter((name) => (longest.get(name) ?? 0) >= LINE_PX)
  if (lines.length > 0) return { name: `lines:${lines.join('+')}`, coverage }
  const h = longest.get('-') ?? 0
  const v = longest.get('|') ?? 0
  if (h >= 1.5 * v) return { name: 'dashes:-', coverage }
  if (v >= 1.5 * h) return { name: 'dashes:|', coverage }
  return { name: 'dots', coverage }
}

/** What each `STATUS_ENCODING` decal name must look like, drawn. */
const DRAWN_SHAPE: Record<DecalKind, string> = {
  solid: 'none',
  diagonal: 'lines:/',
  crosshatch: 'lines:\\+/',
  dashes: 'dashes:-',
  dots: 'dots',
}

// ── the chart ─────────────────────────────────────────────────────────────────

/** One cell of every status, left to right. */
const EVERY_STATUS: StatusMatrix = {
  kind: 'matrix',
  value_type: 'status',
  x_labels: VIZ_STATUSES.map((s) => `run ${s}`),
  y_labels: ['suite'],
  cells: VIZ_STATUSES.map((value, x) => ({ x, y: 0, value, n: 1 })),
}

interface CellItem {
  value: number[]
  itemStyle: { color: string; decal?: ChartDecal }
}

async function renderEveryStatus() {
  engine.load.mockResolvedValue({ init: engine.init })
  const view = render(<HeatmapChart data={EVERY_STATUS} description="One cell per status." />)
  await waitFor(() => expect(engine.instance.setOption).toHaveBeenCalled())
  const calls = engine.instance.setOption.mock.calls
  const option = calls[calls.length - 1]?.[0] as unknown as { series: { data: CellItem[] }[] }
  const cellDecal = (status: VizStatus) => {
    const index = EVERY_STATUS.cells.findIndex((c) => c.value === status)
    return option.series[0].data[index].itemStyle.decal ?? null
  }
  const legendPattern = (status: VizStatus) => {
    const swatch = view.container.querySelector(`[data-legend-status="${status}"] [data-legend-swatch]`)
    const id = /^url\(#(.+)\)$/.exec(swatch?.getAttribute('fill') ?? '')?.[1]
    const pattern = id ? view.container.ownerDocument.getElementById(id) : null
    if (!pattern || pattern.tagName.toLowerCase() !== 'pattern') throw new Error(`no legend pattern for ${status}`)
    return pattern
  }
  return { cellDecal, legendPattern }
}

describe('status decals: the heatmap cell, its legend swatch and STATUS_ENCODING agree', () => {
  beforeEach(() => {
    engine.load.mockReset()
    engine.init.mockClear()
    engine.instance.setOption.mockClear()
  })

  it('every status cell draws the shape its legend swatch draws, and its STATUS_ENCODING names', async () => {
    const { cellDecal, legendPattern } = await renderEveryStatus()
    const drawn: Record<string, { cell: Shape; legend: Shape }> = {}
    for (const status of VIZ_STATUSES) {
      const decal = cellDecal(status)
      const cell = decal ? classify(await echartsTile(decal)) : { name: 'none', coverage: 0 }
      const legend = classify(svgTile(legendPattern(status)))
      drawn[status] = { cell, legend }
    }
    const names = Object.fromEntries(VIZ_STATUSES.map((s) => [s, { cell: drawn[s].cell.name, legend: drawn[s].legend.name }]))
    const expected = Object.fromEntries(
      VIZ_STATUSES.map((s) => [s, { cell: DRAWN_SHAPE[STATUS_ENCODING[s].decal], legend: DRAWN_SHAPE[STATUS_ENCODING[s].decal] }]),
    )
    expect(names).toEqual(expected)
    // …and about as dense: a legend swatch that is mostly cut-out next to a
    // cell that is barely marked is not "the same pattern" to a reader.
    for (const status of VIZ_STATUSES) {
      expect(Math.abs(drawn[status].cell.coverage - drawn[status].legend.coverage), status).toBeLessThan(0.12)
    }
  })

  it('no two statuses draw the same shape on the canvas', async () => {
    const { cellDecal } = await renderEveryStatus()
    const shapes: string[] = []
    for (const status of VIZ_STATUSES) {
      const decal = cellDecal(status)
      shapes.push(decal ? classify(await echartsTile(decal)).name : 'none')
    }
    expect(new Set(shapes).size, shapes.join(', ')).toBe(VIZ_STATUSES.length)
  })

  it('the no-data hatch runs the same way as every other diagonal hatch in the kit', async () => {
    const { decalOf } = await import('../../tokens')
    const hatch = decalOf('diagonal', CHART_VARS.axis)
    if (!hatch) throw new Error('no hatch')
    expect(classify(await echartsTile(hatch)).name).toBe(DRAWN_SHAPE.diagonal)
  })

  it('the text a cell is read as (tooltip, announcement, table) is its STATUS_ENCODING label', () => {
    for (const [index, cell] of EVERY_STATUS.cells.entries()) {
      const status = cell.value as VizStatus
      expect(heatmapTooltipContent(EVERY_STATUS, EVERY_STATUS.cells[index]).rows[0].value).toBe(STATUS_ENCODING[status].label)
    }
  })

  it('the classifier itself tells the five shapes apart (a self-check on synthetic tiles)', () => {
    const rect = (x: number, y: number, width: number, height: number): Mark => ({ kind: 'rect', x, y, width, height })
    expect(classify({ width: 8, height: 8, rotation: 0, marks: [] }).name).toBe('none')
    expect(classify({ width: 8, height: 8, rotation: Math.PI / 4, marks: [rect(0, 0, 2, 8)] }).name).toBe('lines:/')
    expect(classify({ width: 8, height: 8, rotation: Math.PI / 4, marks: [rect(0, 0, 8, 2)] }).name).toBe('lines:\\')
    expect(classify({ width: 8, height: 8, rotation: Math.PI / 4, marks: [rect(0, 0, 2, 8), rect(0, 0, 8, 2)] }).name).toBe(
      'lines:\\+/',
    )
    expect(classify({ width: 8, height: 8, rotation: 0, marks: [rect(1, 3, 4, 2)] }).name).toBe('dashes:-')
    expect(classify({ width: 8, height: 8, rotation: 0, marks: [{ kind: 'circle', cx: 4, cy: 4, r: 1.5 }] }).name).toBe('dots')
  })
})
