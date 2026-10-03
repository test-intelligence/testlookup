/**
 * d3 confinement ratchet (plan 3.3.3, OD-5, R15).
 *
 * `d3-hierarchy` and `d3-force` are direct dependencies for ONE purpose: the
 * failure-group layouts. Wave 2.5 removed the direct `d3` dependency because an
 * eager import of it cost every page; this keeps the two pins from bringing
 * that back, two ways:
 *
 *   1. WHO may import d3: only `components/charts/failureGroups/**` names a
 *      `d3` / `d3-*` module (static, side-effect or dynamic `import()`).
 *   2. WHO may reach that folder: no page's STATIC value-import closure (what
 *      its chunk and the chunks it loads up front contain) and not the app
 *      entry's reaches a `failureGroups/` module — only a `lazy()` import of a
 *      section does. A page that imported the frame for a helper would put d3
 *      on its flag-off first visit; this fails first.
 *
 * Each rule has a planted-violation self-test, so a scanner that finds nothing
 * because it is blind fails too.
 */
import { describe, expect, it } from 'vitest'

/** Every non-test source file under src/, as text, keyed `/src/...`. */
const SOURCES = import.meta.glob(['/src/**/*.ts', '/src/**/*.tsx', '!/src/**/*.test.ts', '!/src/**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const FOLDER = '/src/components/charts/failureGroups/'

/** Any import of a d3 package: `from 'd3-x'`, `import 'd3-x'`, `import('d3-x')`, `require('d3-x')`. */
const D3_IMPORT = /(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"](d3(?:-[a-z]+)?)(?:\/[^'"]*)?['"]/g

/** The files outside the folder that import d3, with the package. */
function d3Importers(sources: Record<string, string>): string[] {
  const out: string[] = []
  for (const [path, text] of Object.entries(sources)) {
    if (path.startsWith(FOLDER)) continue
    for (const match of text.matchAll(D3_IMPORT)) out.push(`${path} -> ${match[1]}`)
  }
  return out.sort()
}

/**
 * Static value imports and re-exports of one file (type-only imports are
 * erased; `import('...')` is its own chunk and is not followed). The same
 * reading as `sectionOnlyModules.test.ts`.
 */
function valueSpecifiers(source: string): string[] {
  const out: string[] = []
  const pattern = /^\s*(import|export)\s+(type\s+)?([\w\s{},*$]*?)from\s+['"]([^'"]+)['"]|^\s*import\s+['"]([^'"]+)['"]/gm
  for (const match of source.matchAll(pattern)) {
    if (match[2]) continue
    const braces = /^\s*\{([^}]*)\}\s*$/.exec(match[3] ?? '')
    const names = braces
      ? braces[1]
          .split(',')
          .map((n) => n.trim())
          .filter(Boolean)
      : []
    if (names.length > 0 && names.every((n) => n.startsWith('type '))) continue
    out.push(match[4] ?? match[5])
  }
  return out
}

function normalise(path: string): string {
  const parts: string[] = []
  for (const part of path.split('/')) {
    if (part === '..') parts.pop()
    else if (part !== '.' && part !== '') parts.push(part)
  }
  return `/${parts.join('/')}`
}

function resolveLocal(sources: Record<string, string>, from: string, spec: string): string | null {
  const dir = from.slice(0, from.lastIndexOf('/'))
  const base = spec.startsWith('@/') ? `/src/${spec.slice(2)}` : spec.startsWith('.') ? normalise(`${dir}/${spec}`) : null
  if (!base) return null
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`, `${base}/index.tsx`]) {
    if (candidate in sources) return candidate
  }
  return null
}

/** Every edge "importer -> module" from `entry`'s static closure into the folder, or to a d3 package. */
function edgesIntoFolder(sources: Record<string, string>, entry: string): string[] {
  const seen = new Set([entry])
  const queue = [entry]
  const edges = new Set<string>()
  while (queue.length > 0) {
    const file = queue.shift() as string
    for (const spec of valueSpecifiers(sources[file])) {
      if (/^d3(?:-[a-z]+)?(?:\/|$)/.test(spec)) {
        edges.add(`${file} -> ${spec}`)
        continue
      }
      const next = resolveLocal(sources, file, spec)
      if (!next) continue
      if (next.startsWith(FOLDER)) {
        edges.add(`${file} -> ${next}`)
        continue
      }
      if (seen.has(next)) continue
      seen.add(next)
      queue.push(next)
    }
  }
  return [...edges].sort()
}

/**
 * Every page under `src/pages/`, AT ANY DEPTH (`settings/`, `test-management/`
 * …: R1-B R1B-8 — the walk once read only `src/pages/*.tsx`), except `dev/`:
 * the chart gallery is a dev-only page that draws every frame on purpose.
 */
function pagesOf(paths: readonly string[]): string[] {
  return paths.filter((path) => /^\/src\/pages\/.+\.tsx$/.test(path) && !path.startsWith('/src/pages/dev/'))
}

const PAGES = pagesOf(Object.keys(SOURCES))
const ENTRIES = ['/src/main.tsx', '/src/App.tsx'].filter((path) => path in SOURCES)

describe('d3 is confined to components/charts/failureGroups (ratchet)', () => {
  it('the folder itself imports both pinned packages (the scan is not vacuous)', () => {
    const inside = Object.entries(SOURCES).filter(([path]) => path.startsWith(FOLDER))
    const packages = new Set(inside.flatMap(([, text]) => [...text.matchAll(D3_IMPORT)].map((m) => m[1])))
    expect([...packages].sort()).toEqual(['d3-force', 'd3-hierarchy'])
  })

  it('no file outside the folder imports d3', () => {
    expect(d3Importers(SOURCES)).toEqual([])
  })

  it('catches every spelling (planted)', () => {
    const planted = {
      '/src/pages/A.tsx': "import { pack } from 'd3-hierarchy'",
      '/src/pages/B.tsx': "import * as d3 from 'd3'",
      '/src/pages/C.tsx': "const m = await import('d3-force')",
      '/src/pages/D.tsx': "import 'd3-selection/dist/x.js'",
      '/src/components/charts/failureGroups/ok.ts': "import { forceSimulation } from 'd3-force'",
      '/src/pages/E.tsx': "import { x } from './d3-helpers'",
    }
    expect(d3Importers(planted)).toEqual([
      '/src/pages/A.tsx -> d3-hierarchy',
      '/src/pages/B.tsx -> d3',
      '/src/pages/C.tsx -> d3-force',
      '/src/pages/D.tsx -> d3-selection',
    ])
  })
})

describe('no page and not the app entry statically reaches the failure-group folder', () => {
  it('there are pages and an entry to walk', () => {
    expect(PAGES).toContain('/src/pages/FailureAnalysisPage.tsx')
    expect(ENTRIES.length).toBeGreaterThan(0)
    // Pages in subfolders are walked too (R1B-8), the dev gallery is not.
    expect(PAGES.some((path) => /^\/src\/pages\/[^/]+\/.+\.tsx$/.test(path))).toBe(true)
    expect(PAGES.filter((path) => path.startsWith('/src/pages/dev/'))).toEqual([])
  })

  it('a planted page in a SUBFOLDER that imports the frame is walked and caught (R1B-8)', () => {
    const planted: Record<string, string> = {
      '/src/pages/Top.tsx': '',
      '/src/pages/settings/Deep.tsx': "import FailureGroupsFrame from '@/components/charts/FailureGroupsFrame'\n",
      '/src/pages/test-management/runs/Deeper.tsx': "import { packLayout } from '../../../components/charts/failureGroups/packLayout'\n",
      '/src/pages/dev/Gallery.tsx': "import FailureGroupsFrame from '@/components/charts/FailureGroupsFrame'\n",
      '/src/pages/Note.ts': '',
      '/src/components/charts/FailureGroupsFrame.tsx': "import Bubbles from './failureGroups/FailureGroupBubbles'\n",
      '/src/components/charts/failureGroups/FailureGroupBubbles.tsx': '',
      '/src/components/charts/failureGroups/packLayout.ts': '',
    }
    const pages = pagesOf(Object.keys(planted))
    expect(pages).toEqual(['/src/pages/Top.tsx', '/src/pages/settings/Deep.tsx', '/src/pages/test-management/runs/Deeper.tsx'])
    expect(pages.flatMap((page) => edgesIntoFolder(planted, page))).toEqual([
      '/src/components/charts/FailureGroupsFrame.tsx -> /src/components/charts/failureGroups/FailureGroupBubbles.tsx',
      '/src/pages/test-management/runs/Deeper.tsx -> /src/components/charts/failureGroups/packLayout.ts',
    ])
  })

  it.each([...ENTRIES, ...PAGES])('%s', (entry) => {
    expect(edgesIntoFolder(SOURCES, entry)).toEqual([])
  })

  it('the walk does find it from the lazy section (the control)', () => {
    expect(edgesIntoFolder(SOURCES, '/src/components/reports/catalogue/FailureGroupsSection.tsx')).toContain(
      '/src/components/reports/catalogue/FailureGroupsSection.tsx -> /src/components/charts/failureGroups/FailureGroupPanel.tsx',
    )
    expect(edgesIntoFolder(SOURCES, '/src/components/charts/failureGroups/packLayout.ts')).toEqual([
      '/src/components/charts/failureGroups/packLayout.ts -> /src/components/charts/failureGroups/failureGroups.model.ts',
      '/src/components/charts/failureGroups/packLayout.ts -> d3-hierarchy',
    ])
  })

  it('a planted static import from a page is found; a lazy import() is not followed', () => {
    const planted = {
      '/src/pages/P.tsx': "import FailureGroupsFrame from '@/components/charts/FailureGroupsFrame'\n",
      '/src/components/charts/FailureGroupsFrame.tsx': "import Bubbles from './failureGroups/FailureGroupBubbles'\n",
      '/src/components/charts/failureGroups/FailureGroupBubbles.tsx': '',
      '/src/pages/Q.tsx': "const S = lazy(() => import('@/components/charts/FailureGroupsFrame'))\n",
    }
    expect(edgesIntoFolder(planted, '/src/pages/P.tsx')).toEqual([
      '/src/components/charts/FailureGroupsFrame.tsx -> /src/components/charts/failureGroups/FailureGroupBubbles.tsx',
    ])
    expect(edgesIntoFolder(planted, '/src/pages/Q.tsx')).toEqual([])
  })
})
