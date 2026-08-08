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
 */
import { gzipSync } from 'node:zlib'
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = join(ROOT, 'dist')
const ASSETS = join(DIST, 'assets')

// Headroom over the measured 142,305 so ordinary feature work does not trip it,
// while a regression of the kind this guards (+109KB) cannot fit underneath.
const EAGER_GZIP_BUDGET = 180_000

// Heavy libraries that must never sit on the critical path. Matched against
// chunk *contents*, not filenames — the defect renamed nothing.
const MUST_BE_LAZY = ['recharts']

if (!existsSync(DIST)) {
  console.error('dist/ not found — run `npm run build` first.')
  process.exit(1)
}

const html = readFileSync(join(DIST, 'index.html'), 'utf8')
const eager = [...new Set([...html.matchAll(/assets\/([A-Za-z0-9_-]+\.(?:js|css))/g)].map((m) => m[1]))]

if (eager.length === 0) {
  // A guard that silently measures nothing is worse than no guard.
  console.error('FAIL: parsed 0 eager assets out of index.html — the parser is broken,')
  console.error('      not the bundle. Refusing to report a pass.')
  process.exit(1)
}

let rawTotal = 0
let gzipTotal = 0
const rows = []
for (const name of eager) {
  const file = join(ASSETS, name)
  if (!existsSync(file)) continue
  const buf = readFileSync(file)
  const gz = gzipSync(buf).length
  rawTotal += buf.length
  gzipTotal += gz
  rows.push({ name, raw: buf.length, gz })
}

rows.sort((a, b) => b.gz - a.gz)
console.log('Eager assets (fetched before first render, every route):')
for (const r of rows) {
  console.log(`  ${String(r.gz).padStart(8)} gz  ${String(r.raw).padStart(9)} raw  ${r.name}`)
}
console.log(`  ${'-'.repeat(30)}`)
console.log(`  ${String(gzipTotal).padStart(8)} gz  ${String(rawTotal).padStart(9)} raw  TOTAL`)

const failures = []

for (const lib of MUST_BE_LAZY) {
  const offenders = rows.filter((r) => {
    if (!r.name.endsWith('.js')) return false
    return readFileSync(join(ASSETS, r.name)).includes(lib)
  })
  if (offenders.length) {
    failures.push(
      `${lib} is in an eagerly-preloaded chunk (${offenders.map((o) => o.name).join(', ')}). ` +
        `It is only used by lazy routes, so it must not be modulepreloaded on every page. ` +
        `Check whether a manualChunks rule named it — naming a shared chunk hoists it into ` +
        `the entry's static imports.`,
    )
  }
}

if (gzipTotal > EAGER_GZIP_BUDGET) {
  failures.push(
    `eager gzip total ${gzipTotal} exceeds the ${EAGER_GZIP_BUDGET} budget ` +
      `(+${gzipTotal - EAGER_GZIP_BUDGET}).`,
  )
}

// Sanity: dist should contain lazy chunks too. If everything collapsed into the
// entry, the totals above could pass while code-splitting was silently lost.
const allJs = readdirSync(ASSETS).filter((f) => f.endsWith('.js'))
if (allJs.length <= eager.filter((e) => e.endsWith('.js')).length) {
  failures.push('no lazy chunks found — code splitting appears to have been lost entirely.')
}

if (failures.length) {
  console.error('\nBundle budget FAILED:')
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}

console.log(`\nOK — eager gzip ${gzipTotal} within budget ${EAGER_GZIP_BUDGET}; ` +
  `${MUST_BE_LAZY.join(', ')} not on the critical path.`)
