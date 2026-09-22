#!/usr/bin/env node
/**
 * Guard the *eager* bundle — what index.html makes the browser fetch before
 * anything renders, on every route including the login page.
 *
 * Why this exists: `manualChunks` forced recharts/d3 into a named `charts`
 * chunk. Naming it made it a shared chunk, which rolldown then hoisted into the
 * entry's static imports, so `charts-*.js` (529,975 raw / 183,037 gzip) was
 * modulepreloaded on every page load — including login, where no chart renders.
 *
 * A chunk being *split out* is not the same as a chunk being *deferred*. That
 * distinction is invisible in a chunk listing and obvious in index.html, so this
 * checks index.html.
 *
 *   before   891,879 raw / 251,676 gzip eager
 *   after    502,199 raw / 142,305 gzip eager   (-44% / -43%)
 *
 * Runs against an existing dist/ — CI already builds the frontend, so this adds
 * no build cost.
 *
 * VIZ-103 added the chart ENGINES (architecture/VISUALIZATION_ENGINES_ADR.md):
 * ECharts (and its renderer, zrender) and three.js join recharts as libraries
 * that must never be eager, and the lazy ECharts base chunk gets a size
 * ceiling of its own. The rules live in `evaluate()`, a pure function, and a
 * SELF-TEST runs it against fake manifests before every real check — a guard
 * whose matcher silently stopped matching would otherwise pass forever.
 */
import { gzipSync } from 'node:zlib'
import { randomBytes } from 'node:crypto'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
// `--dist <dir>` measures a build written elsewhere (e.g. `vite build --outDir <tmp>`);
// the default is the dist/ that `npm run build` writes.
const distFlag = process.argv.indexOf('--dist')
const DIST = distFlag !== -1 && process.argv[distFlag + 1] ? resolve(process.argv[distFlag + 1]) : join(ROOT, 'dist')
const ASSETS = join(DIST, 'assets')

// Headroom over the measured 142,305 so ordinary feature work does not trip it,
// while a regression of the kind this guards (+109KB) cannot fit underneath.
const EAGER_GZIP_BUDGET = 180_000

// Heavy libraries that must never sit on the critical path. Matched against
// chunk *contents*, not filenames — the defect renamed nothing.
const MUST_BE_LAZY = ['recharts', 'echarts', 'zrender', 'three']

// The substrings each library leaves in minified code — string LITERALS from
// its core, because identifiers are mangled and the package name itself is not
// reliably present. A chunk carrying ANY of a library's markers counts. Verified
// 2026-09-21 by importing echarts eagerly from main.tsx and building (the name
// "zrender" was NOT in the output):
//   recharts  its own name (the original rule)
//   echarts   `_echarts_instance_` — the DOM attribute echarts/core stamps on a chart root
//   zrender   `__zr_normal__`      — zrender/lib/Element.js PRESERVED_NORMAL_STATE
//   three     `__THREE__`          — the global three's core sets; "three" is an English
//                                    word any UI string may contain. That assignment is a
//                                    top-level side effect in three's entry, and a
//                                    `sideEffects: false`-driven tree-shake MAY drop it
//                                    while WebGLRenderer itself ships — so a second marker:
//             `WebGLRenderer: Context Lost.` — the message WebGLRenderer logs from its
//                                    own `webglcontextlost` handler, so it is present
//                                    whenever the renderer class is. Written without the
//                                    `THREE.` prefix on purpose: older releases log the
//                                    literal 'THREE.WebGLRenderer: Context Lost.', and a
//                                    newer logging helper may prepend 'THREE.' at runtime;
//                                    the suffix is in both. UNVERIFIED (from memory of
//                                    three's source; three is not installed here).
// VIZ-508 must PROVE both three markers with an eager-import build (import
// WebGLRenderer from main.tsx, build, grep the entry chunk) before anyone relies
// on this rule to keep three off the critical path.
const SIGNATURES = {
  echarts: ['_echarts_instance_'],
  zrender: ['__zr_normal__'],
  three: ['__THREE__', 'WebGLRenderer: Context Lost.'],
}
const signaturesOf = (lib) => SIGNATURES[lib] ?? [lib]

// The lazy ECharts BASE chunk (echarts/core + zrender + canvas renderer + grid,
// tooltip, legend, aria, and — while heatmap is the only registered type — the
// heatmap type too, because rolldown folds a base with one importer into it).
// Identified by `_echarts_instance_`, which only echarts/core defines.
//
//   measured 2026-09-21: 542,374 raw / 180,562 gzip (echarts 6.1.0, heatmap only)
//   ceiling = measured + 10%
//
// A second chart type splits the base out (≈170 kB per the engine spike), so
// the ceiling holds; a barrel import (`import * as echarts from 'echarts'`,
// 253 kB) or a stray SVG renderer does not fit under it.
const ECHARTS_BASE_SIGNATURE = '_echarts_instance_'
const ECHARTS_BASE_GZIP_CEILING = 198_600

/**
 * @param {{ html: string, assets: Map<string, Buffer> }} build
 * @returns {{ rows: {name: string, raw: number, gz: number}[], gzipTotal: number, rawTotal: number,
 *             engineChunks: {name: string, raw: number, gz: number}[], failures: string[] }}
 */
function evaluate({ html, assets }) {
  const failures = []
  const eager = [...new Set([...html.matchAll(/assets\/([A-Za-z0-9_-]+\.(?:js|css))/g)].map((m) => m[1]))]
  if (eager.length === 0) {
    // A guard that silently measures nothing is worse than no guard.
    failures.push('parsed 0 eager assets out of index.html — the parser is broken, not the bundle. Refusing to report a pass.')
    return { rows: [], gzipTotal: 0, rawTotal: 0, engineChunks: [], failures }
  }

  let rawTotal = 0
  let gzipTotal = 0
  const rows = []
  for (const name of eager) {
    const buf = assets.get(name)
    if (!buf) continue
    const gz = gzipSync(buf).length
    rawTotal += buf.length
    gzipTotal += gz
    rows.push({ name, raw: buf.length, gz })
  }
  rows.sort((a, b) => b.gz - a.gz)

  for (const lib of MUST_BE_LAZY) {
    const offenders = rows.filter(
      (r) => r.name.endsWith('.js') && signaturesOf(lib).some((marker) => assets.get(r.name).includes(marker)),
    )
    if (offenders.length) {
      failures.push(
        `${lib} is in an eagerly-preloaded chunk (${offenders.map((o) => o.name).join(', ')}). ` +
          `It is only used by lazy routes, so it must not be modulepreloaded on every page. ` +
          `Check whether a manualChunks rule named it — naming a shared chunk hoists it into ` +
          `the entry's static imports — or whether something eager imports it statically.`,
      )
    }
  }

  if (gzipTotal > EAGER_GZIP_BUDGET) {
    failures.push(`eager gzip total ${gzipTotal} exceeds the ${EAGER_GZIP_BUDGET} budget (+${gzipTotal - EAGER_GZIP_BUDGET}).`)
  }

  const engineChunks = []
  for (const [name, buf] of assets) {
    if (!name.endsWith('.js') || !buf.includes(ECHARTS_BASE_SIGNATURE)) continue
    const row = { name, raw: buf.length, gz: gzipSync(buf).length }
    engineChunks.push(row)
    if (row.gz > ECHARTS_BASE_GZIP_CEILING) {
      failures.push(
        `ECharts base chunk ${name} is ${row.gz} gzip, over its ${ECHARTS_BASE_GZIP_CEILING} ceiling ` +
          `(+${row.gz - ECHARTS_BASE_GZIP_CEILING}). Import from 'echarts/core' and register each chart type ` +
          `in its own module (components/charts/engines/echarts/) — never the 'echarts' barrel.`,
      )
    }
  }

  // Sanity: dist should contain lazy chunks too. If everything collapsed into the
  // entry, the totals above could pass while code-splitting was silently lost.
  const allJs = [...assets.keys()].filter((f) => f.endsWith('.js'))
  if (allJs.length <= eager.filter((e) => e.endsWith('.js')).length) {
    failures.push('no lazy chunks found — code splitting appears to have been lost entirely.')
  }

  return { rows, gzipTotal, rawTotal, engineChunks, failures }
}

/**
 * The human-readable report for one evaluated build — pure, so the self-test can
 * assert on what a reader sees. Two things it must never let a reader assume:
 * that a missing ECharts base chunk means the ceiling was measured (it was not),
 * and how close the eager total sits to its budget.
 */
function report({ rows, gzipTotal, rawTotal, engineChunks }) {
  const lines = ['Eager assets (fetched before first render, every route):']
  for (const r of rows) lines.push(`  ${String(r.gz).padStart(8)} gz  ${String(r.raw).padStart(9)} raw  ${r.name}`)
  lines.push(`  ${'-'.repeat(30)}`)
  lines.push(`  ${String(gzipTotal).padStart(8)} gz  ${String(rawTotal).padStart(9)} raw  TOTAL`)
  const headroom = EAGER_GZIP_BUDGET - gzipTotal
  const pct = ((headroom / EAGER_GZIP_BUDGET) * 100).toFixed(1)
  lines.push(
    headroom >= 0
      ? `  headroom: ${headroom} bytes gzip (${pct}% of the ${EAGER_GZIP_BUDGET} budget) left before the eager budget fails`
      : `  headroom: NONE — ${-headroom} bytes gzip (${(-pct).toFixed(1)}%) OVER the ${EAGER_GZIP_BUDGET} budget`,
  )
  if (engineChunks.length) {
    lines.push('', `Lazy ECharts base chunk(s) (ceiling ${ECHARTS_BASE_GZIP_CEILING} gz):`)
    for (const r of engineChunks) {
      lines.push(`  ${String(r.gz).padStart(8)} gz  ${String(r.raw).padStart(9)} raw  ${r.name}  (${ECHARTS_BASE_GZIP_CEILING - r.gz} under the ceiling)`)
    }
  } else {
    lines.push(
      '',
      `ECharts base-chunk ceiling (${ECHARTS_BASE_GZIP_CEILING} gz) NOT EXERCISED: no production route renders ECharts yet, ` +
        'so this build has no base chunk to measure. A pass here says nothing about the ceiling.',
    )
  }
  return lines
}

// ── Self-test: every rule must catch a planted violation, and pass a clean build.
{
  const problems = []
  let cases = 0
  const html = '<script type="module" src="/assets/index-a1.js"></script><link rel="stylesheet" href="/assets/index-b2.css">'
  const fake = (entry, extra = {}) =>
    new Map(Object.entries({ 'index-a1.js': Buffer.from(entry), 'index-b2.css': Buffer.from('body{}'), 'lazy-c3.js': Buffer.from('lazy'), ...extra }))
  const caught = (label, result, needle) => {
    cases += 1
    if (!result.failures.some((f) => f.includes(needle))) problems.push(`the planted ${label} was NOT caught`)
  }
  const says = (label, lines, needle) => {
    cases += 1
    if (!lines.some((l) => l.includes(needle))) problems.push(`the report for ${label} does not say "${needle}": ${lines.join(' / ')}`)
  }

  cases += 1
  const clean = evaluate({ html, assets: fake('export const ok = "three little words"') })
  if (clean.failures.length) problems.push(`a clean fake build failed: ${clean.failures.join(' | ')}`)
  if (clean.rows.length !== 2) problems.push(`expected 2 eager assets in the fake build, parsed ${clean.rows.length}`)
  // EVERY marker of every library, alone, must be caught — a second marker nobody
  // tests is a marker nobody knows has stopped matching.
  for (const lib of MUST_BE_LAZY) {
    for (const marker of signaturesOf(lib)) {
      caught(`eager ${lib} (marker "${marker}")`, evaluate({ html, assets: fake(`/* bundled */ var x = "${marker}";`) }), `${lib} is in an eagerly-preloaded chunk`)
    }
  }
  // three.js tree-shaken so that `__THREE__` is gone but the renderer shipped.
  caught(
    'eager three without __THREE__',
    evaluate({ html, assets: fake('function onContextLost(e){e.preventDefault(),console.log("THREE.WebGLRenderer: Context Lost."),x=!0}') }),
    'three is in an eagerly-preloaded chunk',
  )
  // An engine that is LAZY is fine, as long as its base fits the ceiling.
  cases += 1
  const lazyEngine = evaluate({ html, assets: fake('ok', { 'heatmap-d4.js': Buffer.from(`${ECHARTS_BASE_SIGNATURE} echarts zrender`) }) })
  if (lazyEngine.failures.length) problems.push(`a lazy ECharts chunk was flagged: ${lazyEngine.failures.join(' | ')}`)
  if (lazyEngine.engineChunks.length !== 1) problems.push('the lazy ECharts base chunk was not identified')
  says('a build with a base chunk', report(lazyEngine), 'under the ceiling')
  says('a build with no base chunk', report(clean), 'NOT EXERCISED')
  says('a build under budget', report(clean), `headroom: ${EAGER_GZIP_BUDGET - clean.gzipTotal} bytes gzip`)
  // Random bytes do not compress, so gzip size ≈ raw size: this one is over the ceiling.
  const noise = randomBytes(Math.max(ECHARTS_BASE_GZIP_CEILING, EAGER_GZIP_BUDGET) + 5_000)
  const heavy = Buffer.concat([Buffer.from(ECHARTS_BASE_SIGNATURE), noise])
  caught('oversized ECharts base chunk', evaluate({ html, assets: fake('ok', { 'core-e5.js': heavy }) }), 'over its')
  const heavyEntry = fake('ok')
  heavyEntry.set('index-a1.js', noise.subarray(0, EAGER_GZIP_BUDGET + 1_000))
  const overBudget = evaluate({ html, assets: heavyEntry })
  caught('eager total over budget', overBudget, 'exceeds the')
  says('a build over budget', report(overBudget), 'headroom: NONE')
  caught('empty index.html', evaluate({ html: '<html></html>', assets: fake('ok') }), 'parsed 0 eager assets')
  caught('lost code splitting', evaluate({ html, assets: new Map([['index-a1.js', Buffer.from('ok')], ['index-b2.css', Buffer.from('')]]) }), 'no lazy chunks')

  if (problems.length) {
    console.error('Bundle budget FAILED (self-test — the guard itself is broken):')
    for (const p of problems) console.error(`  - ${p}`)
    process.exit(1)
  }
  console.log(`Bundle budget self-test: ${cases} cases passed.\n`)
}

if (!existsSync(join(DIST, 'index.html')) || !existsSync(ASSETS)) {
  console.error(`${DIST} has no index.html + assets/ — run \`npm run build\` first (or pass --dist <dir>).`)
  process.exit(1)
}

const assets = new Map(readdirSync(ASSETS).map((name) => [name, readFileSync(join(ASSETS, name))]))
const result = evaluate({
  html: readFileSync(join(DIST, 'index.html'), 'utf8'),
  assets,
})
for (const line of report(result)) console.log(line)

if (result.failures.length) {
  console.error('\nBundle budget FAILED:')
  for (const f of result.failures) console.error(`  - ${f}`)
  process.exit(1)
}

console.log(`\nOK — eager gzip ${result.gzipTotal} within budget ${EAGER_GZIP_BUDGET} ` +
  `(${EAGER_GZIP_BUDGET - result.gzipTotal} bytes headroom); ` +
  `${MUST_BE_LAZY.join(', ')} not on the critical path` +
  `${result.engineChunks.length ? '' : '; ECharts base ceiling not exercised'} (self-test passed).`)
