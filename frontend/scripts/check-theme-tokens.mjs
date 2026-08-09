#!/usr/bin/env node
/**
 * Theme-token normalization guard (2026-08 retheme).
 *
 * The retheme's whole point is consistency: one green, one red, one amber
 * across every dark theme, so a "passed" pill means the same colour whichever
 * theme is active. Nothing enforced that before — each of the six [data-theme]
 * blocks carried its own status hues, which is how #7ce0a0, #43e0a0 and #3fb950
 * all ended up meaning "passed".
 *
 * Lives here rather than in a vitest file for two reasons, both learned the
 * hard way: `npm run build` runs `tsc` over src/ and the app has no node types,
 * and vitest stubs CSS imports to an empty string (css: false), so `?raw` on a
 * stylesheet yields nothing. A node script has plain fs access and is the
 * pattern already used by check-bundle-budget.mjs.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const CSS = readFileSync(join(ROOT, 'src', 'index.css'), 'utf8')
const HTML = readFileSync(join(ROOT, 'index.html'), 'utf8')
const STORE = readFileSync(join(ROOT, 'src', 'store', 'themeStore.ts'), 'utf8')

const DARK = ['signal', 'console', 'slate', 'ember', 'midnight']
const LIGHT = 'lab'
const ALL = [...DARK, LIGHT]
const STATUSES = ['status-passed', 'status-failed', 'status-broken', 'status-skipped', 'status-flaky']
const WEBFONTS = ['Sora', 'Plus Jakarta Sans', 'Space Grotesk', 'Archivo', 'IBM Plex Mono', 'JetBrains Mono']
const RETIRED_GREENS = ['#7ce0a0', '#43e0a0', '#3fb950']

const fail = []

function themeBlock(id) {
  const start = CSS.indexOf(`[data-theme="${id}"]`)
  if (start === -1) return null
  const open = CSS.indexOf('{', start)
  const close = CSS.indexOf('}', open)
  return close === -1 ? null : CSS.slice(open, close)
}

function token(id, name) {
  const block = themeBlock(id)
  if (!block) return null
  const m = new RegExp(`--${name}\s*:\s*([^;]+);`).exec(block)
  return m ? m[1].trim().toLowerCase() : null
}

// Guard the guard: a parse failure must not silently pass everything.
for (const id of ALL) {
  if (!themeBlock(id)) fail.push(`no [data-theme="${id}"] block found in src/index.css`)
}
if (fail.length) {
  console.error('Theme token check FAILED (parse):')
  for (const f of fail) console.error(`  - ${f}`)
  process.exit(1)
}

// 1. Status hues unified across dark themes.
for (const status of STATUSES) {
  const values = [...new Set(DARK.map((id) => token(id, status)))]
  if (values.length !== 1) {
    fail.push(`--${status} differs across dark themes: ${values.join(', ')} — a status colour must not depend on the active theme`)
  }
}

// 2. Light theme keeps its own darker equivalents (contrast on white cards).
for (const status of STATUSES) {
  if (token(LIGHT, status) === token('signal', status)) {
    fail.push(`--${status} on "${LIGHT}" matches the dark themes — the contrast-adjusted light palette was lost`)
  }
}

// 3. Retired colours stay retired.
for (const id of ALL) {
  if (RETIRED_GREENS.includes(token(id, 'status-passed'))) {
    fail.push(`"${id}" reintroduced a retired neon passed-green (${token(id, 'status-passed')})`)
  }
  for (const key of ['color-accent', 'color-btn-primary-bg']) {
    if (token(id, key) === '#b8f24a') fail.push(`"${id}" reintroduced the neon lime accent on --${key}`)
  }
  if ((token(id, 'app-bg-image') || '').replace(/\s/g, '') !== 'none') {
    fail.push(`"${id}" has an aurora background glow again (--app-bg-image)`)
  }
}

// 4. System fonts only.
for (const id of ALL) {
  for (const family of WEBFONTS) {
    if (themeBlock(id).includes(family)) fail.push(`"${id}" names webfont "${family}" — themes use system stacks`)
  }
}
if (HTML.includes('fonts.googleapis.com/css2')) {
  fail.push('index.html loads Google Fonts again. TestLookup is offline-first: in an air-gapped deployment that request stalls until timeout rather than failing fast.')
}
const csp = /Content-Security-Policy" content="([^"]+)"/.exec(HTML)?.[1] ?? ''
if (!csp) fail.push('no CSP meta found in index.html')
for (const origin of ['fonts.googleapis.com', 'fonts.gstatic.com']) {
  if (csp.includes(origin)) fail.push(`CSP still allows ${origin} though no font is loaded from it`)
}

// 5. Picker swatches match the themes they advertise. These are hardcoded in
//    themeStore.ts, so a CSS-only edit leaves the chip showing the old palette —
//    exactly what a lime chip beside a blue theme would have looked like.
for (const id of ALL) {
  // Plain string slicing rather than a regex — the pattern needs enough
  // escaping to be a liability, and the row shape is fixed.
  const idAt = STORE.indexOf(`id: '${id}'`)
  const swAt = idAt === -1 ? -1 : STORE.indexOf('swatch: [', idAt)
  const endAt = swAt === -1 ? -1 : STORE.indexOf(']', swAt)
  if (idAt === -1 || swAt === -1 || endAt === -1) {
    fail.push(`themeStore.ts has no swatch row for "${id}"`)
    continue
  }
  const [bg, card, accent] = STORE.slice(swAt + 'swatch: ['.length, endAt)
    .split(',')
    .map((s) => s.trim().replace(/['"]/g, '').toLowerCase())
  const expect = { bg: token(id, 'color-bg'), card: token(id, 'color-bg-card'), accent: token(id, 'color-accent') }
  if (bg !== expect.bg) fail.push(`"${id}" swatch bg ${bg} != --color-bg ${expect.bg}`)
  if (card !== expect.card) fail.push(`"${id}" swatch card ${card} != --color-bg-card ${expect.card}`)
  if (accent !== expect.accent) fail.push(`"${id}" swatch accent ${accent} != --color-accent ${expect.accent}`)
}

if (fail.length) {
  console.error('\nTheme token check FAILED:')
  for (const f of fail) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`OK — ${ALL.length} themes: status hues unified across ${DARK.length} dark themes, ` +
  `light palette distinct, system fonts only, no remote font origins, picker swatches match their tokens.`)
