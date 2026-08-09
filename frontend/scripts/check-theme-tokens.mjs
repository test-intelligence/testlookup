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
import { readdirSync, readFileSync, statSync } from 'node:fs'
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

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else if (/\.(ts|tsx)$/.test(entry) && !/\.(test|spec)\./.test(entry)) out.push(full)
  }
  return out
}

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
// Source files outside index.css can put a webfont name into the built CSS —
// tailwind.config.js's fontFamily fallbacks did exactly that ('Sora',
// 'IBM Plex Mono'), and an inline style in AppLogo.tsx hardcoded another. The
// theme blocks were clean while the shipped stylesheet was not, which is why
// this scans the sources that feed it rather than only index.css.
const TAILWIND = readFileSync(join(ROOT, 'tailwind.config.js'), 'utf8')
const fontFamilyBlock = /fontFamily:\s*\{[^}]*\}/.exec(TAILWIND)?.[0] ?? ''
for (const family of WEBFONTS) {
  if (fontFamilyBlock.includes(family)) {
    fail.push(`tailwind.config.js fontFamily still falls back to "${family}" — it is no longer loaded, so it only resolves on a machine that has it installed locally`)
  }
}

// Inline fontFamily styles in components bypass the token system entirely.
const SRC_DIR = join(ROOT, 'src')
for (const file of walk(SRC_DIR)) {
  const text = readFileSync(file, 'utf8')
  for (const m of text.matchAll(/fontFamily:\s*['\`]([^'\`]+)['\`]/g)) {
    const value = m[1]
    if (!value.includes('var(--font-')) {
      fail.push(`${file.slice(ROOT.length + 1)} hardcodes fontFamily "${value}" — use var(--font-mono|sans|display) so it follows the theme`)
    }
  }
}

// Status colours must come from tokens, not inline literals. Components that
// hardcoded hex bypassed the theme entirely, which is how /runs rendered a
// "passed" bar in #34d399 while /coverage rendered one in var(--status-passed)
// (#34a06b) — two different greens for the same meaning, on two pages.
//
// 1,167 colour literals existed across 42 files before this was enforced.
const STATUS_RGB = {
  'passed #34a06b': [52, 160, 107], 'retired emerald': [52, 211, 153],
  'retired green-500': [34, 197, 94], 'retired red-400': [248, 113, 113],
  'retired red-500': [239, 68, 68], 'github green': [63, 185, 80],
  'github red': [248, 81, 73], 'github amber': [210, 153, 34],
}
const STATUS_LITERALS = [
  // passed
  '#86efac', '#34d399', '#22c55e', '#10b981', '#3fb950', '#4ade80', '#7ce0a0',
  // failed
  '#fca5a5', '#ef4444', '#f85149', '#f87171', '#ff8a6b',
  // broken / skipped
  '#fcd34d', '#f59e0b', '#fbbf24', '#eab308', '#ffb347', '#f5d76e',
  // flaky
  '#c4b5fd', '#c084fc', '#a371f7', '#a78bfa', '#e0b3ff',
  // accent
  '#93c5fd', '#4493f8', '#58a6ff',
]
for (const file of walk(SRC_DIR)) {
  if (!file.endsWith('.tsx')) continue
  const text = readFileSync(file, 'utf8')
  const rel = file.slice(ROOT.length + 1)
    // CSS Color 4 allows `rgb(R G B)` with SPACES. Every earlier sweep used a
  // comma-based pattern and silently missed 36 of these — caught only by a live
  // probe seeing the colour render. Match both forms.
  for (const m of text.matchAll(/rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/g)) {
    const rgb = [1, 2, 3].map((i) => Number(m[i]))
    const hit = Object.entries(STATUS_RGB).find(([, v]) => v.every((c, i) => c === rgb[i]))
    if (hit) {
      fail.push(`${rel} hardcodes rgb(${rgb.join(' ')}) — that is ${hit[0]}; use the token so it follows the theme`)
    }
  }
  for (const lit of STATUS_LITERALS) {
    if (text.toLowerCase().includes(lit)) {
      fail.push(`${rel} hardcodes ${lit} — use var(--status-passed|failed|broken|skipped|flaky) or var(--color-accent) so every page renders the same colour for the same meaning`)
    }
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
