/**
 * Wave 2.6 fix round (R1-3): code that only a flag-on catalogue section runs
 * must not be statically reachable from the five report pages.
 *
 * The sections are `lazy()` imports, so whatever they need should arrive with
 * them. It did not: convenient imports from kit modules every chart page loads
 * (`chartState` -> `chartApi` for two header helpers, `StackedColumnChart` ->
 * `BarChart.model` for `middleTruncate`, that model -> `chartCatalog` for one
 * percent helper, `zoomModel` -> `multiSeriesModel` for `finishLine`) made
 * rolldown place the chart request queue, the chart registry and the release
 * alignment in shared chunks, and every flag-off visit to Overview, Trends and
 * Suite detail downloaded 7-13 kB gzip it never ran.
 *
 * Rolldown chunks by STATIC REACHABILITY, not by the names used: a module
 * re-exported through a section-only one, or imported for one small helper,
 * puts the whole importee in the importer's chunk (measured: `ChartFrame`
 * reading `hasChartData` through `chartState`'s re-export still placed the
 * resolver in the frame chunk). So the helpers live in leaf modules, and this
 * walks each page's static value-import graph, from the source, and lists
 * every edge into a section-only module.
 */
import { describe, expect, it } from 'vitest'

/** Every non-test source file under src/, as text, keyed `/src/...`. */
const SOURCES = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.ts', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/**
 * Static value imports and re-exports of one file. `import type` / `export
 * type` are erased by the compiler, and so is an import whose every name is
 * `type`-qualified; `import('...')` is a separate chunk, so it is not followed.
 */
function valueSpecifiers(source: string): string[] {
  const out: string[] = []
  // The clause between the keyword and `from` is names, braces, commas, `*`
  // and `as` only: anything wider lets `export type X = …` swallow the lines
  // down to the NEXT `from '…'` and skip a real re-export as a type.
  const pattern = /^\s*(import|export)\s+(type\s+)?([\w\s{},*$]*?)from\s+['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]/gm
  for (const match of source.matchAll(pattern)) {
    if (match[2]) continue
    // `{ type A, type B }` and nothing else (no default import, no namespace).
    const braces = /^\s*\{([^}]*)\}\s*$/.exec(match[3] ?? '')
    const names = braces ? braces[1].split(',').map((n) => n.trim()).filter(Boolean) : []
    if (names.length > 0 && names.every((n) => n.startsWith('type '))) continue
    out.push(match[4] ?? match[5])
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

/**
 * Every value-import EDGE from a module in `entry`'s closure into a forbidden
 * module, as "importer -> forbidden". The walk does not enter a forbidden
 * module: what it imports is reached only through that edge.
 */
function edgesInto(entry: string, forbidden: ReadonlySet<string>): string[] {
  const seen = new Set([entry])
  const queue = [entry]
  const edges = new Set<string>()
  while (queue.length > 0) {
    const file = queue.shift() as string
    for (const spec of valueSpecifiers(SOURCES[file])) {
      const next = resolveLocal(file, spec)
      if (!next) continue
      if (forbidden.has(next)) {
        edges.add(`${file.replace('/src/', '')} -> ${next.replace('/src/', '')}`)
        continue
      }
      if (seen.has(next)) continue
      seen.add(next)
      queue.push(next)
    }
  }
  return [...edges].sort()
}

/** Modules only a catalogue section runs: none may be in a page's flag-off closure. */
const SECTION_ONLY = new Set([
  '/src/services/chartApi.ts',
  '/src/components/charts/chartCatalog.ts',
  '/src/components/charts/chartCatalogSources.ts',
  '/src/components/charts/seriesAlignment.ts',
  '/src/components/charts/multiSeriesModel.ts',
  '/src/components/charts/BarChart.model.ts',
  // The request-state resolver: flag-off code reads the states from `chartStateCore`.
  '/src/components/charts/chartState.ts',
  // The duration-band and multi-series zoom slicers, and the duration model they bring.
  '/src/components/charts/zoom/zoomSlices.ts',
  '/src/components/charts/durationBuckets.ts',
  // The gate section's model: its imports are the section's (chart states, adapters, scope).
  '/src/components/reports/catalogue/GateCatalogue.model.ts',
])

/**
 * Edges still to cut, each in a file another fixer owns (Wave 2.6 fix round,
 * `F-K1.md` "requests"). A RATCHET: an edge not listed fails, and a listed
 * edge that is gone fails too, so whoever cuts one deletes its line here.
 */
const PENDING = new Set<string>([])

const PAGES = ['OverviewPage', 'TrendsPage', 'SummaryReportPage', 'SuiteDetailPage', 'ReleaseGatePage']

const pageEdges = (page: string) => edgesInto(`/src/pages/${page}.tsx`, SECTION_ONLY)

describe('section-only kit modules stay out of the flag-off page closure', () => {
  it.each(PAGES)('%s statically reaches none of them (beyond the pending edges)', (page) => {
    expect(`/src/pages/${page}.tsx` in SOURCES, page).toBe(true)
    expect(pageEdges(page).filter((edge) => !PENDING.has(edge))).toEqual([])
  })

  it('every pending edge still exists (delete its line here when it is cut)', () => {
    const all = new Set(PAGES.flatMap(pageEdges))
    expect([...PENDING].filter((edge) => !all.has(edge))).toEqual([])
  })

  it('the walk does find them: the Overview section reaches them (the control)', () => {
    const edges = edgesInto('/src/components/reports/catalogue/OverviewCatalogue.tsx', SECTION_ONLY)
    expect(edges).toContain('components/reports/catalogue/OverviewCatalogue.tsx -> components/charts/chartCatalogSources.ts')
    expect(edges).toContain('components/reports/catalogue/OverviewCatalogue.tsx -> components/charts/BarChart.model.ts')
    expect(edgesInto('/src/components/charts/chartCatalogSources.ts', new Set(['/src/services/chartApi.ts']))).toEqual([
      'components/charts/chartCatalogSources.ts -> services/chartApi.ts',
    ])
  })

  it('an import whose names are all `type`-qualified is not followed (it is erased)', () => {
    expect(valueSpecifiers("import { type A, type B } from './x'\n")).toEqual([])
    expect(valueSpecifiers("import { a, type B } from './x'\n")).toEqual(['./x'])
    expect(valueSpecifiers("import type { A } from './x'\n")).toEqual([])
    expect(valueSpecifiers("import X, { type B } from './x'\n")).toEqual(['./x'])
  })

  it('a type alias above a re-export does not hide the re-export', () => {
    const source = "export type A = B & { c?: number }\n\n// note\nexport { d } from './y'\n"
    expect(valueSpecifiers(source)).toEqual(['./y'])
  })
})
