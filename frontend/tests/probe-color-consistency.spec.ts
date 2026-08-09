/**
 * Live probe: the same status meaning renders the same colour on every page.
 *
 * Reported: the "passed" green in the Failure-signature table on /runs did not
 * match Suite-coverage-breakdown on /coverage. It didn't — /coverage resolved
 * var(--status-passed) (#34a06b) while /runs hardcoded #34d399 inline.
 *
 * This asserts on *rendered* colour via getComputedStyle, so it fails if any
 * page reintroduces a literal. The static guard in check-theme-tokens.mjs
 * catches the source; this catches the result.
 */
import { expect, test } from '@playwright/test'

const BASE = process.env.PROBE_BASE_URL ?? 'http://testlookup.local'

/** Colours retired by the retheme — none should render anywhere. */
const RETIRED = [
  'rgb(52, 211, 153)',  // #34d399 emerald-400 — the reported mismatch
  'rgb(134, 239, 172)', // #86efac green-300
  'rgb(34, 197, 94)',   // #22c55e green-500
  'rgb(252, 165, 165)', // #fca5a5 red-300
  'rgb(252, 211, 77)',  // #fcd34d amber-300
  'rgb(184, 242, 74)',  // #b8f24a the old lime accent
]

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input').first().fill('admin')
  await page.locator('input[type="password"]').fill('Admin@2026!')
  await page.getByRole('button', { name: /sign in|log in|login/i }).first().click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30_000 })
}

/** Every distinct colour rendered on the page, with how many nodes use it. */
async function renderedColours(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const counts: Record<string, number> = {}
    for (const el of Array.from(document.querySelectorAll('*'))) {
      const cs = getComputedStyle(el)
      for (const prop of ['color', 'backgroundColor', 'borderTopColor'] as const) {
        const v = cs[prop]
        if (!v || v === 'rgba(0, 0, 0, 0)' || v === 'transparent') continue
        counts[v] = (counts[v] ?? 0) + 1
      }
    }
    return counts
  })
}

test('the token value is what actually renders', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/runs`, { waitUntil: 'networkidle' })
  const passed = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--status-passed').trim(),
  )
  expect(passed.toLowerCase()).toBe('#34a06b')
})

const PAGES = ['/runs', '/coverage', '/overview', '/test-management', '/trends',
               '/failures', '/defects', '/release-gate', '/search', '/live']

for (const path of PAGES) {
  test(`no retired colour renders on ${path}`, async ({ page }) => {
    await login(page)
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)

    const colours = await renderedColours(page)
    const offenders = RETIRED.filter((r) => colours[r])
    expect(
      offenders,
      `${path} renders retired colours: ${offenders.map((o) => `${o} x${colours[o]}`).join(', ')}`,
    ).toHaveLength(0)
  })
}

test('runs and coverage render the SAME passed-green', async ({ page }) => {
  await login(page)

  // Browsers report colour-mix results as `color(srgb r g b / a)` with 0-1
  // channels, and plain colours as `rgb(r, g, b)`. Normalise both to a base
  // "r,g,b" ignoring alpha — different tint strengths of one token are correct
  // (badges use 16%, borders 35%); different BASE colours are the bug.
  const baseOf = (c: string): string | null => {
    const srgb = c.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/)
    if (srgb) return [1, 2, 3].map((i) => Math.round(Number(srgb[i]) * 255)).join(',')
    const rgb = c.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/)
    if (rgb) return [1, 2, 3].map((i) => Number(rgb[i])).join(',')
    return null
  }
  const isGreen = (base: string) => {
    const [r, g, b] = base.split(',').map(Number)
    return g > 90 && g > r + 25 && g > b + 15
  }

  const greenBasesOn = async (path: string) => {
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1200)
    const colours = await renderedColours(page)
    const bases = new Set<string>()
    for (const c of Object.keys(colours)) {
      const b = baseOf(c)
      if (b && isGreen(b)) bases.add(b)
    }
    return [...bases].sort()
  }

  const runs = await greenBasesOn('/runs')
  const coverage = await greenBasesOn('/coverage')

  // Each page must use exactly one green, and it must be the same one.
  expect(runs, `/runs renders multiple distinct greens: ${runs.join(' | ')}`).toHaveLength(1)
  expect(coverage, `/coverage renders multiple distinct greens: ${coverage.join(' | ')}`).toHaveLength(1)
  expect(runs[0], `/runs green ${runs[0]} != /coverage green ${coverage[0]}`).toBe(coverage[0])
  expect(runs[0], 'the shared green should be --status-passed #34a06b').toBe('52,160,107')
})


test('every page draws each status hue from the same base colour', async ({ page }) => {
  await login(page)

  // Base colour ignoring alpha; browsers report colour-mix as color(srgb ...).
  const baseOf = (c: string): string | null => {
    const srgb = c.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/)
    if (srgb) return [1, 2, 3].map((i) => Math.round(Number(srgb[i]) * 255)).join(',')
    const rgb = c.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/)
    if (rgb) return [1, 2, 3].map((i) => Number(rgb[i])).join(',')
    return null
  }
  const band = (base: string): string | null => {
    const [r, g, b] = base.split(',').map(Number)
    if (r < 40 && g < 40 && b < 40) return null            // near-black surfaces
    if (r > 215 && g > 215 && b > 215) return null          // near-white text
    if (Math.max(r, g, b) - Math.min(r, g, b) < 30) return null  // greys
    if (g > r + 25 && g > b + 15) return 'green'
    if (r > g + 40 && r > b + 40) return 'red'
    if (r > 140 && g > 90 && b < 110 && r > b + 60) return 'amber'
    if (b > r + 40 && b > g + 20) return 'blue'
    if (r > g + 20 && b > g + 30) return 'violet'
    return null
  }

  const perPage: Record<string, Record<string, Set<string>>> = {}
  for (const path of PAGES) {
    await page.goto(`${BASE}${path}`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
    const colours = await renderedColours(page)
    const bands: Record<string, Set<string>> = {}
    for (const c of Object.keys(colours)) {
      const b = baseOf(c)
      if (!b) continue
      const k = band(b)
      if (!k) continue
      ;(bands[k] ??= new Set()).add(b)
    }
    perPage[path] = bands
  }

  // For each hue band, every page that renders it must use the SAME base.
  const problems: string[] = []
  for (const hue of ['green', 'red', 'amber', 'blue', 'violet']) {
    const seen = new Map<string, string[]>()
    for (const [path, bands] of Object.entries(perPage)) {
      for (const base of bands[hue] ?? []) {
        seen.set(base, [...(seen.get(base) ?? []), path])
      }
    }
    if (seen.size > 1) {
      problems.push(`${hue}: ${[...seen.entries()].map(([b, ps]) => `${b} on ${ps.join('+')}`).join('  |  ')}`)
    }
  }
  expect(problems, 'pages disagree on a status hue: ' + problems.join(' ;; ')).toHaveLength(0)
})
