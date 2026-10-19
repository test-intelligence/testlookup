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
import {
  ENGINE_DIRS,
  RECHARTS_BOUNDARY_ENFORCED,
  RECHARTS_DIRS,
  SOURCE_FILE,
  importBoundaryViolations,
  inEngineDir,
  inRechartsDir,
  optionViolations,
  rechartsBoundaryViolations,
  runSelfTest,
} from './chart-guard.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const CSS = readFileSync(join(ROOT, 'src', 'index.css'), 'utf8')
const HTML = readFileSync(join(ROOT, 'index.html'), 'utf8')
const STORE = readFileSync(join(ROOT, 'src', 'store', 'themeStore.ts'), 'utf8')

const DARK = ['signal', 'console', 'slate', 'ember', 'midnight']
// W29a UIX-01: two light themes (Lab, and Paper "soft light · low glare").
const LIGHT = ['lab', 'paper']
const ALL = [...DARK, ...LIGHT]
const STATUSES = ['status-passed', 'status-failed', 'status-broken', 'status-skipped', 'status-flaky', 'status-unknown']
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

/**
 * The value of `--name` in a theme block. Written `\\s`: in a template literal
 * `\s` is just "s", so the pattern was `--names*:s*` -- a token written
 * `--name : value` was not read at all, and `--status-passeds: x` answered
 * for `status-passed` (CodeQL js/useless-regexp-character-escape).
 */
function tokenIn(block, name) {
  const m = new RegExp(`--${name}\\s*:\\s*([^;]+);`).exec(block)
  return m ? m[1].trim().toLowerCase() : null
}

function token(id, name) {
  const block = themeBlock(id)
  return block ? tokenIn(block, name) : null
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

// 2. Each light theme keeps its own darker equivalents (contrast on light cards).
for (const light of LIGHT) {
  for (const status of STATUSES) {
    if (token(light, status) === token('signal', status)) {
      fail.push(`--${status} on "${light}" matches the dark themes — the contrast-adjusted light palette was lost`)
    }
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

// 6. The chart kit (VIZ-102 / VIZ-103).
//
//    a. NO colour literal of any kind under src/components/charts/** — not just
//       the status hues above. Every chart colour comes from
//       components/charts/tokens.ts, which reads the per-theme --chart-* /
//       --status-* tokens; a literal is a colour that ignores the theme
//       (TrendChart hard-coded its grid, axis and tooltip before this rule).
//       Test files are scanned too: a fixture colour is written as a token
//       name or a CSS keyword, never as hex.
//    b. The ENGINE guards (ADR decision 4), in ./chart-guard.mjs, on a real
//       TypeScript parse — the regex rule they replace missed 7 of 8 bypasses
//       in the 2026-09-22 security review:
//         - IMPORT BOUNDARY over every file in src/ (tests included): a value
//           import of echarts / zrender / echarts-gl / three is allowed only
//           under ENGINE_DIRS; `import type` is allowed everywhere.
//         - OPTION RULES over ENGINE_DIRS, src/components/charts/** and any
//           file that imports an engines/ module: a formatter is only ever
//           `formatter: domTooltipFormatter(…)`; no computed/bracket/spread/
//           Object.assign/defineProperty route around that; no `rich:`
//           without an allow-list reason. Test files are not scanned for the
//           option rules; they mock formatters.
//
// Every rule runs a SELF-TEST first, against planted violations held in
// memory: a matcher that silently stopped matching would otherwise turn this
// into a guard that always passes.
const CHART_DIR = join(SRC_DIR, 'components', 'charts')
const COLOUR_LITERAL = [
  /(?<![\w&])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/gi,
  /\b(?:rgba?|hsla?|hwb|oklch|oklab|lch|lab)\s*\(/gi,
]

function lineOf(text, index) {
  return text.slice(0, index).split('\n').length
}

function chartColourViolations(rel, text) {
  const out = []
  for (const pattern of COLOUR_LITERAL) {
    for (const m of text.matchAll(pattern)) {
      out.push(`${rel}:${lineOf(text, m.index)} has colour literal "${m[0].trim()}" — chart colours come from components/charts/tokens.ts (CHART_VARS / useChartTokens)`)
    }
  }
  return out
}

function walkAll(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) out.push(...walkAll(full))
    // Every extension the build compiles, not just ts/tsx (R1 F2): a page.jsx
    // importing recharts is as real as a page.tsx doing it.
    else if (SOURCE_FILE.test(entry)) out.push(full)
  }
  return out
}

let selfTestCount = 0
{
  const selfTest = []
  const expectHit = (name, found) => {
    selfTestCount += 1
    if (found.length === 0) selfTest.push(`self-test: the planted ${name} was NOT caught`)
  }
  const expectClean = (name, found) => {
    selfTestCount += 1
    if (found.length !== 0) selfTest.push(`self-test: the clean ${name} was flagged: ${found.join('; ')}`)
  }
  expectHit('hex literal', chartColourViolations('planted.tsx', "const grid = '#1e293b'"))
  expectHit('short hex literal', chartColourViolations('planted.tsx', 'fill="#fff"'))
  expectHit('rgb() literal', chartColourViolations('planted.tsx', "stroke: 'rgb(12 34 56)'"))
  expectHit('hsla() literal', chartColourViolations('planted.tsx', "color: 'hsla(210, 10%, 20%, 0.5)'"))
  expectClean('token reference', chartColourViolations('clean.tsx', "fill={CHART_VARS.grid} stroke=\"var(--chart-axis)\" fill=\"url(#totalGrad)\""))
  // The token reader itself: whitespace around the colon, and a longer name
  // that merely starts with the one asked for.
  const expectToken = (label, block, name, expected) => {
    selfTestCount += 1
    const got = tokenIn(block, name)
    if (got !== expected) selfTest.push(`self-test: token reader, ${label}: expected ${expected}, read ${got}`)
  }
  expectToken('space before the colon', '{ --status-passed : #00FF00; }', 'status-passed', '#00ff00')
  expectToken('compact declaration', '{ --status-passed:#0f0; }', 'status-passed', '#0f0')
  expectToken('a longer token name first', '{ --status-passeds: red; --status-passed: blue; }', 'status-passed', 'blue')
  const engine = runSelfTest()
  selfTestCount += engine.count
  selfTest.push(...engine.problems)
  if (selfTest.length) {
    console.error('Theme token check FAILED (chart-kit guard self-test — the guard itself is broken):')
    for (const f of selfTest) console.error(`  - ${f}`)
    process.exit(1)
  }
}

let chartFiles = 0
let engineFiles = 0
let boundaryFiles = 0
let optionFiles = 0
// VIZ-104: files outside the kit the recharts boundary covers, and (while it is
// off) the ones that would still fail it — the ratchet's progress, printed.
let rechartsFiles = 0
const rechartsPending = []
const ENGINE_IMPORT = /\bfrom\s+['"][^'"]*\/engines\/|\bimport\(\s*['"][^'"]*\/engines\//
for (const file of walkAll(SRC_DIR)) {
  const rel = file.slice(ROOT.length + 1).split('\\').join('/')
  const text = readFileSync(file, 'utf8')
  const isTest = /\.(test|spec)\./.test(file)
  const inCharts = file.startsWith(CHART_DIR)
  boundaryFiles += 1
  fail.push(...importBoundaryViolations(rel, text))
  if (!inRechartsDir(rel)) {
    rechartsFiles += 1
    if (!RECHARTS_BOUNDARY_ENFORCED && rechartsBoundaryViolations(rel, text).length) rechartsPending.push(rel)
  }
  if (inCharts) {
    chartFiles += 1
    fail.push(...chartColourViolations(rel, text))
  }
  if (inEngineDir(rel)) engineFiles += 1
  if (!isTest && (inCharts || inEngineDir(rel) || ENGINE_IMPORT.test(text))) {
    optionFiles += 1
    fail.push(...optionViolations(rel, text))
  }
}
if (chartFiles === 0) fail.push('scanned 0 files under src/components/charts — the chart-kit guard is measuring nothing')
if (rechartsFiles === 0) fail.push(`scanned 0 files outside ${RECHARTS_DIRS.join(', ')} — the recharts boundary is measuring nothing`)
if (engineFiles === 0) fail.push(`scanned 0 files under ${ENGINE_DIRS.join(', ')} — ENGINE_DIRS no longer matches the tree, so the import boundary allows nothing and guards nothing`)

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

// ── W29a UI standard: rules 7-12 ──────────────────────────────────────────────
//
// Each rule's matcher runs a SELF-TEST first (a planted violation caught, the
// clean form passed), as rule 6a's does. Test files are not scanned: a test may
// name a class or a token to prove it is ABSENT. The hits in other files are
// the W29a sweep groups' checklists.

/** `code[i]` is ' or ": the index past its closing quote (a newline ends an unterminated one). */
function skipQuoted(code, i) {
  const quote = code[i]
  for (let j = i + 1; j < code.length; j++) {
    if (code[j] === '\\') { j++; continue }
    if (code[j] === quote || code[j] === '\n') return j + 1
  }
  return code.length
}

/** `code[i]` is a backtick: the index past the closing one (`${…}` read as expressions). */
function skipTemplate(code, i) {
  for (let j = i + 1; j < code.length; j++) {
    if (code[j] === '\\') { j++; continue }
    if (code[j] === '`') return j + 1
    if (code[j] === '$' && code[j + 1] === '{') { j = skipExpression(code, j + 2) - 1 }
  }
  return code.length
}

/** From just inside a `{`: the index past its matching `}`. */
function skipExpression(code, i) {
  let depth = 0
  for (let j = i; j < code.length; j++) {
    const c = code[j]
    if (c === '"' || c === "'") { j = skipQuoted(code, j) - 1; continue }
    if (c === '`') { j = skipTemplate(code, j) - 1; continue }
    if (c === '{') depth++
    else if (c === '}') { if (depth === 0) return j + 1; depth-- }
  }
  return code.length
}

/** Every line and block comment OUTSIDE a string becomes spaces (newlines kept). */
function blankComments(text) {
  let out = ''
  let i = 0
  const blank = (s) => s.replace(/[^\n]/g, ' ')
  while (i < text.length) {
    const c = text[i]
    if (c === '/' && text[i + 1] === '/') {
      const nl = text.indexOf('\n', i)
      const end = nl === -1 ? text.length : nl
      out += blank(text.slice(i, end))
      i = end
      continue
    }
    if (c === '/' && text[i + 1] === '*') {
      const close = text.indexOf('*/', i + 2)
      const end = close === -1 ? text.length : close + 2
      out += blank(text.slice(i, end))
      i = end
      continue
    }
    if (c === '"' || c === "'" || c === '`') {
      const end = c === '`' ? skipTemplate(text, i) : skipQuoted(text, i)
      out += text.slice(i, end)
      i = end
      continue
    }
    out += c
    i++
  }
  return out
}

const STRING_LITERAL = /"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`/g

/** `[index, body]` of every string literal (quotes stripped) in comment-blanked code. */
function stringLiterals(code) {
  return [...code.matchAll(STRING_LITERAL)].map((m) => [m.index, m[0].slice(1, -1)])
}

const relOf = (file) => file.slice(ROOT.length + 1).split('\\').join('/')

// 7 (UIX-01). No white border (the spinner's invisible arc was `border-t-white`),
// and no class string with text-white on a status / ui TINT (white on Lab's 15 %
// failed tint is 1.26:1, the destructive buttons; `.btn-danger` replaces them).
const WHITE_BORDER = /(?<![\w-])border-(?:[trblxy]-)?white(?![\w-])/
const TEXT_WHITE = /(?<![\w-])text-white(?![\w-])/
const STATUS_TINT = /bg-\[var\(--(?:status|ui)-[a-z]+-bg/
function rule7Violations(rel, text) {
  const out = []
  const code = blankComments(text)
  for (const [index, body] of stringLiterals(code)) {
    const border = WHITE_BORDER.exec(body)
    if (border) {
      out.push(`${rel}:${lineOf(code, index)} a white border ("${border[0]}") vanishes on the light themes; use a token (border-t-[var(--color-accent)], border-[var(--color-border)])`)
    }
    if (TEXT_WHITE.test(body) && STATUS_TINT.test(body)) {
      out.push(`${rel}:${lineOf(code, index)} text-white on a status / ui tint (1.26:1 on Lab); a destructive button is .btn-danger, status text on its tint reads var(--status-<s>-ink)`)
    }
  }
  return out
}

// 8 (UIX-02). The nine --cat-* / --kind-* aliases are gone: the failure-category
// map (components/charts/failureTaxonomy.ts) is the only source.
const CAT_KIND = /--(?:cat|kind)-[\w-]*/
function rule8Violations(rel, text) {
  const m = CAT_KIND.exec(text)
  if (!m) return []
  return [`${rel}:${lineOf(text, m.index)} names "${m[0]}"; failure category / kind colours come from failureCategoryColor / failureKindColor (components/charts/failureTaxonomy)`]
}

// 9 (UIX-03). Conditional is the WARNING hue (--gate-conditional = broken), never skipped-gold.
const CONDITIONAL_GOLD = /conditional[^\n]{0,160}--status-skipped/i
function rule9Violations(rel, text) {
  const code = blankComments(text)
  const m = CONDITIONAL_GOLD.exec(code)
  if (!m) return []
  return [`${rel}:${lineOf(code, m.index)} paints "conditional" with --status-skipped; conditional is the warning hue: DECISION_TOKEN.conditional (components/ui/statusVocabulary)`]
}

// 10 (UIX-13). No colour literal in a string of a non-test source outside the
// picker swatches, the Mermaid fallbacks and the chart kit (rule 6a). The ten
// stat-tile sheens are W29b's (UIX-09): tolerated up to these counts, which may
// only fall.
const RULE10_EXEMPT = new Set(['src/store/themeStore.ts', 'src/components/guide/mermaidConfig.ts'])
const PENDING_W29B_SHEEN = 'rgba(255,255,255,0.025)'
const PENDING_W29B = {
  'src/pages/CoveragePage.tsx': 1,
  'src/pages/DefectsPage.tsx': 1,
  'src/pages/FailureAnalysisPage.tsx': 2,
  'src/pages/RunsPage.tsx': 1,
  'src/pages/TrendsPage.tsx': 1,
  'src/pages/RunIntelligencePage.tsx': 3,
  'src/pages/DeepInvestigationPage.tsx': 1,
}
const STRING_COLOUR = [
  /(?<![\w&])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/gi,
  /\b(?:rgba?|hsla?|oklch)\s*\(/gi,
]
function rule10Violations(rel, text, pending = PENDING_W29B) {
  if (RULE10_EXEMPT.has(rel) || rel.startsWith('src/components/charts/')) return []
  const out = []
  const code = blankComments(text)
  let sheens = 0
  for (const [index, body] of stringLiterals(code)) {
    // A pending sheen is taken out of the string first, so it is counted once, as a sheen.
    const parts = body.split(PENDING_W29B_SHEEN)
    sheens += parts.length - 1
    const rest = parts.join(' ')
    for (const pattern of STRING_COLOUR) {
      for (const m of rest.matchAll(pattern)) {
        out.push(`${rel}:${lineOf(code, index)} colour literal "${m[0].trim()}" in a string; use a theme token (var(--name), or color-mix(in srgb, var(--name) N%, transparent))`)
      }
    }
  }
  const allowed = pending[rel] ?? 0
  if (sheens > allowed) {
    out.push(`${rel} has ${sheens} stat-tile sheens "${PENDING_W29B_SHEEN}" (pending W29b UIX-09: ${allowed}); the count may only fall`)
  }
  return out
}

// 11 (UIX-07). Every var(--name) names a token index.css defines (in a theme
// block or a :root), a Tailwind runtime variable (--tw-*), or a property the
// same file declares itself. An undefined token silently inherits the parent's
// colour (`--color-danger` drew a failed step's tint as nothing).
function definedTokens(css) {
  return new Set([...blankComments(css).matchAll(/--([\w-]+)\s*:/g)].map((m) => m[1]))
}
const VAR_READ = /var\(\s*--([\w-]+)/g
const LOCAL_DECLARATION = /--([\w-]+)['"`]?\s*:/g
function rule11Violations(rel, text, defined) {
  const code = blankComments(text)
  const local = new Set([...code.matchAll(LOCAL_DECLARATION)].map((m) => m[1]))
  const out = []
  for (const m of code.matchAll(VAR_READ)) {
    const name = m[1]
    // var(--chart-${i}): a dynamic family, read from the kit's own token lists.
    if (code[m.index + m[0].length] === '$') continue
    if (name.startsWith('tw-') || defined.has(name) || local.has(name)) continue
    out.push(`${rel}:${lineOf(code, m.index)} reads var(--${name}), which no theme block or :root in index.css defines; it silently inherits the parent's colour`)
  }
  return out
}

// 12 (families). The derived :root defines the W29a token families, and the
// light override (the solid destructive button) names exactly the light themes.
const FAMILY_TOKENS = [
  'status-passed-ink', 'status-failed-ink', 'status-broken-ink', 'status-skipped-ink', 'status-flaky-ink',
  'ui-danger', 'ui-danger-bg', 'ui-danger-bd', 'ui-danger-ink',
  'ui-warning', 'ui-warning-bg', 'ui-warning-bd', 'ui-warning-ink',
  'ui-success', 'ui-success-bg', 'ui-success-bd', 'ui-success-ink',
  'ui-info', 'ui-info-bg', 'ui-info-bd', 'ui-info-ink',
  'state-quarantined', 'state-quarantined-bd', 'color-text-on-status',
  'color-btn-danger-bg', 'color-btn-danger-bd', 'color-btn-danger-text', 'color-btn-danger-hover',
]

/** `{ selector, body }` of every innermost rule of a stylesheet. */
function cssRules(css) {
  return [...blankComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1].trim(), body: m[2] }))
}

function rule12Violations(css, light = LIGHT) {
  const out = []
  const rules = cssRules(css)
  const root = rules.find((r) => r.selector.endsWith(':root') && /--status-passed-bg\s*:/.test(r.body))
  if (!root) return ['index.css: no derived :root (the one defining --status-passed-bg) found']
  for (const name of FAMILY_TOKENS) {
    if (tokenIn(root.body, name) === null) out.push(`index.css: the derived :root does not define --${name}`)
  }
  const lightRule = rules.find((r) => r.selector.includes('[data-theme=') && /--color-btn-danger-bg\s*:/.test(r.body))
  if (!lightRule) {
    out.push('index.css: no light-theme rule sets --color-btn-danger-bg')
    return out
  }
  const named = [...lightRule.selector.matchAll(/\[data-theme="([\w-]+)"\]/g)].map((m) => m[1]).sort()
  if (named.join(',') !== [...light].sort().join(',')) {
    out.push(`index.css: the danger-button light rule names ${named.join(', ')}; it must name exactly the light themes (${light.join(', ')})`)
  }
  return out
}

{
  const selfTest = []
  const expectHit = (name, found) => {
    selfTestCount += 1
    if (found.length === 0) selfTest.push(`self-test: the planted ${name} was NOT caught`)
  }
  const expectClean = (name, found) => {
    selfTestCount += 1
    if (found.length !== 0) selfTest.push(`self-test: the clean ${name} was flagged: ${found.join('; ')}`)
  }
  expectHit('rule 7 white spinner arc', rule7Violations('p.tsx', "const c = 'animate-spin border-2 border-t-white'"))
  expectHit('rule 7 white border', rule7Violations('p.tsx', 'const c = "rounded border-white/40"'))
  expectHit('rule 7 white text on a failed tint', rule7Violations('p.tsx', '<button className="bg-[var(--status-failed-bg)] px-4 text-white">x</button>'))
  expectHit('rule 7 white text on a ui tint (template)', rule7Violations('p.tsx', 'const c = `px-2 ${a} bg-[var(--ui-danger-bg)] text-white`'))
  expectClean('rule 7 accent arc and btn-danger', rule7Violations('p.tsx', "const a = 'border-t-[var(--color-accent)]'; const b = 'btn-danger' // border-t-white"))
  expectHit('rule 8 kind token', rule8Violations('p.tsx', "color: 'var(--kind-product)'"))
  expectHit('rule 8 cat token in css', rule8Violations('index.css', '  --cat-assertion: var(--status-failed);'))
  expectClean('rule 8 kind colour from the map', rule8Violations('p.tsx', "color: failureKindColor('product')"))
  expectHit('rule 9 conditional gold', rule9Violations('p.tsx', "  conditional: { fg: 'var(--status-skipped)' },"))
  expectClean('rule 9 conditional warning', rule9Violations('p.tsx', "  conditional: { fg: 'var(--status-broken-ink)' },\n  skipped: 'var(--status-skipped)',\n// conditional was --status-skipped once"))
  expectHit('rule 10 hex in a string', rule10Violations('src/main.tsx', "style: { background: '#1e293b' }"))
  expectHit('rule 10 rgba in a JSX attribute', rule10Violations('src/pages/X.tsx', '<div style={{ boxShadow: "0 8px 28px rgba(0,0,0,0.25)" }} />'))
  expectHit('rule 10 a pending sheen over its count', rule10Violations('src/pages/X.tsx', "const a = 'rgba(255,255,255,0.025)'", { 'src/pages/X.tsx': 0 }))
  expectClean('rule 10 a comment, a token and a pending sheen', rule10Violations('src/pages/X.tsx', "// see #588 and rgba(0,0,0,0.5)\nconst a = 'var(--shadow-md)'; const b = 'rgba(255,255,255,0.025)'", { 'src/pages/X.tsx': 1 }))
  expectClean('rule 10 the picker swatches', rule10Violations('src/store/themeStore.ts', "swatch: ['#eae8e2', '#f5f4f0', '#2f5fb3']"))
  const planted = definedTokens(':root { --status-failed: #b3352c; --ui-danger: var(--status-failed); }')
  expectHit('rule 11 undefined token', rule11Violations('p.tsx', "const c = 'text-[var(--color-danger)]'", planted))
  expectClean('rule 11 defined, tw, local, dynamic and commented reads', rule11Violations('p.tsx', "const a = 'var(--ui-danger)'; const b = 'var(--tw-ring-color)'; const s = { '--bar-w': '4px', width: 'var(--bar-w)' }; const d = `var(--chart-${i})` // var(--nope)", planted))
  const complete = `:root { --status-passed-bg: x; ${FAMILY_TOKENS.map((t) => `--${t}: v;`).join(' ')} }`
  expectHit('rule 12 a missing family token', rule12Violations(':root { --status-passed-bg: x; }\n[data-theme="lab"] { --color-btn-danger-bg: y; }', ['lab']))
  expectHit('rule 12 a light rule naming a dark theme', rule12Violations(`${complete}\n[data-theme="lab"], [data-theme="signal"] { --color-btn-danger-bg: y; }`, ['lab']))
  expectClean('rule 12 complete families', rule12Violations(`${complete}\n[data-theme="lab"], [data-theme="paper"] { --color-btn-danger-bg: y; }`, ['lab', 'paper']))
  if (selfTest.length) {
    console.error('Theme token check FAILED (W29a rules 7-12 self-test: the guard itself is broken):')
    for (const f of selfTest) console.error(`  - ${f}`)
    process.exit(1)
  }
}

const DEFINED_TOKENS = definedTokens(CSS)
let standardFiles = 0
for (const file of walk(SRC_DIR)) {
  const rel = relOf(file)
  const text = readFileSync(file, 'utf8')
  standardFiles += 1
  fail.push(...rule7Violations(rel, text))
  fail.push(...rule8Violations(rel, text))
  fail.push(...rule9Violations(rel, text))
  fail.push(...rule10Violations(rel, text))
  fail.push(...rule11Violations(rel, text, DEFINED_TOKENS))
}
fail.push(...rule8Violations('src/index.css', CSS))
fail.push(...rule11Violations('src/index.css', CSS, DEFINED_TOKENS))
fail.push(...rule12Violations(CSS))
if (standardFiles === 0) fail.push('scanned 0 source files for rules 7-11: the W29a rules are measuring nothing')

if (fail.length) {
  console.error('\nTheme token check FAILED:')
  for (const f of fail) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`OK — ${ALL.length} themes (${ALL.join(', ')}): status hues unified across ${DARK.length} dark themes, ` +
  `light palettes (${LIGHT.join(', ')}) distinct, system fonts only, no remote font origins, picker swatches match their tokens; ` +
  `W29a rules over ${standardFiles} files: no white border or white text on a tint (7), no --cat-/--kind- (8), ` +
  `conditional never skipped-gold (9), no colour literal in a string outside the swatches / Mermaid / chart kit (10), ` +
  `every var(--…) defined (11), the token families and the light override (12); ` +
  `chart kit (${chartFiles} files): no colour literals; engine packages value-imported only under ` +
  `${ENGINE_DIRS.join(', ')} (${boundaryFiles} files checked, ${engineFiles} inside); ` +
  (RECHARTS_BOUNDARY_ENFORCED
    ? `recharts value-imported only under ${RECHARTS_DIRS.join(', ')} (${rechartsFiles} files outside checked); `
    : `recharts boundary OFF until Wave 2.5 migrates the pages (${rechartsPending.length} of ${rechartsFiles} files ` +
      `outside ${RECHARTS_DIRS.join(', ')} still import it${rechartsPending.length ? `: ${rechartsPending.join(', ')}` : ''}); `) +
  `ECharts options (${optionFiles} files): formatters only via domTooltipFormatter, no rich: ` +
  `(self-test: ${selfTestCount} cases passed).`)
