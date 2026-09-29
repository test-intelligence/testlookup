/**
 * Wave 2.5 (VIZ-104): the kit pieces that draw with plain elements — the day
 * strip, the gauge bar and the sparkline — must not pull a chart engine into
 * the pages that use them.
 *
 * `DayStrip` uses the keyboard cursor, and the cursor draws the tooltip body.
 * That body used to live in `ChartTooltip.tsx`, which value-imports Recharts
 * (`usePlotArea`, for `PinnedTip`), so Runs, Coverage and FailureAnalysis —
 * pages with a day strip and no Recharts chart — each fetched about 60 kB gzip
 * of Recharts' store and hooks they never ran (measured in the Wave 2.5 bundle
 * table). The body is its own module now; this walks each piece's static
 * import graph, from the source, and fails on any `recharts` or `echarts`
 * value import in it, so the next convenient import cannot bring it back.
 */
import { describe, expect, it } from 'vitest'

/** Every non-test source file under src/, as text, keyed `/src/...`. */
const SOURCES = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.ts', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const ENGINE = /^(?:recharts|echarts|zrender)(?:\/|$)/

/** Value imports and re-exports of one file (`import type` / `export type` are erased). */
function valueSpecifiers(source: string): string[] {
  const out: string[] = []
  const pattern = /^\s*(import|export)\s+(type\s+)?[^'"]*?from\s+['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]/gm
  for (const match of source.matchAll(pattern)) {
    if (match[2]) continue
    out.push(match[3] ?? match[4])
  }
  return out
}

/** `/src/a/b/../c` -> `/src/a/c`. */
function normalise(path: string): string {
  const parts: string[] = []
  for (const part of path.split('/')) {
    if (part === '..') parts.pop()
    else if (part !== '.' && part !== '') parts.push(part)
  }
  return `/${parts.join('/')}`
}

function resolveLocal(from: string, spec: string): string | null {
  const dir = from.slice(0, from.lastIndexOf('/'))
  const base = spec.startsWith('@/') ? `/src/${spec.slice(2)}` : spec.startsWith('.') ? normalise(`${dir}/${spec}`) : null
  if (!base) return null
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]) {
    if (candidate in SOURCES) return candidate
  }
  return null
}

/** Every engine import reachable from `entry`, as "file -> specifier". */
function engineImportsFrom(entry: string): string[] {
  const seen = new Set<string>()
  const hits: string[] = []
  const walk = (file: string) => {
    if (seen.has(file)) return
    seen.add(file)
    for (const spec of valueSpecifiers(SOURCES[file])) {
      if (ENGINE.test(spec)) hits.push(`${file} -> ${spec}`)
      const next = resolveLocal(file, spec)
      if (next) walk(next)
    }
  }
  walk(entry)
  return hits
}

const KIT = '/src/components/charts'

describe('kit pieces drawn without a chart engine', () => {
  it.each(['DayStrip.tsx', 'GaugeBar.tsx', 'Sparkline.tsx', 'ChartCursor.tsx', 'ChartTooltipBody.tsx'])(
    '%s reaches no recharts / echarts value import',
    (file) => {
      expect(`${KIT}/${file}` in SOURCES, file).toBe(true)
      expect(engineImportsFrom(`${KIT}/${file}`)).toEqual([])
    },
  )

  it('the walk does find one: ChartTooltip itself imports recharts (the control)', () => {
    expect(engineImportsFrom(`${KIT}/ChartTooltip.tsx`)).toContain(`${KIT}/ChartTooltip.tsx -> recharts`)
  })
})
