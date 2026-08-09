/**
 * Live probe: the retheme as the browser actually renders it, plus runtime
 * request behaviour.
 *
 * Everything about the retheme so far has been verified structurally — tokens
 * in the stylesheet, hex values in the served bundle. That proves the CSS
 * shipped, not that a rendered pill is the colour it should be. `getComputedStyle`
 * is the difference: it resolves the var() chain and any color-mix() the derived
 * tokens apply on top.
 *
 * Also counts network requests per page load. SWR + Axios setups tend to
 * accumulate duplicate fetches for the same key, which no unit test notices.
 *
 * Run against the live homelab:
 *   npx playwright test tests/probe-retheme.spec.ts --config=probe-live.config.ts
 */
import { expect, test } from '@playwright/test'

const BASE = process.env.PROBE_BASE_URL ?? 'http://testlookup.local'
const USER = process.env.PROBE_USER ?? 'admin'
const PASS = process.env.PROBE_PASS ?? 'Admin@2026!'

/** Expected after the retheme. Lab (light) carries its own darker values.
 *  Custom properties come back from getComputedStyle as authored — hex here,
 *  not the rgb() form that resolved colour properties return. */
const DARK_TOKENS = {
  '--status-passed': '#34a06b',
  '--status-failed': '#d1554e',
  '--status-broken': '#cf8542',
  '--status-flaky': '#9d85d6',
}
// Accents are deliberately NOT unified — only status hues are. Each theme keeps
// its own identity colour, which is the point of having six of them.
const THEME_ACCENT: Record<string, string> = {
  signal: '#3b82f6', console: '#6366f1', slate: '#5e9ed6',
  ember: '#d08c3a', midnight: '#3b82f6',
}
const RETIRED = ['rgb(184, 242, 74)', 'rgb(124, 224, 160)'] // lime, neon green

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input').first().fill(USER)
  await page.locator('input[type="password"]').fill(PASS)
  await page.getByRole('button', { name: /sign in|log in|login/i }).first().click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30_000 })
}

test('themes resolve to the normalized tokens in a real browser', async ({ page }) => {
  await login(page)

  const themes = ['signal', 'console', 'slate', 'ember', 'midnight']
  for (const theme of themes) {
    await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)
    const resolved = await page.evaluate((names) => {
      const cs = getComputedStyle(document.documentElement)
      return Object.fromEntries(names.map((n) => [n, cs.getPropertyValue(n).trim()]))
    }, [...Object.keys(DARK_TOKENS), '--color-accent'])

    for (const [name, expected] of Object.entries(DARK_TOKENS)) {
      expect(
        resolved[name].toLowerCase(),
        `theme "${theme}" ${name} resolved to "${resolved[name]}"`,
      ).toBe(expected)
    }
    expect(
      resolved['--color-accent'].toLowerCase(),
      `theme "${theme}" accent resolved to "${resolved['--color-accent']}"`,
    ).toBe(THEME_ACCENT[theme])
  }
})

test('no retired colour renders anywhere on the overview', async ({ page }) => {
  await login(page)
  await page.goto(`${BASE}/overview`, { waitUntil: 'networkidle' })

  const offenders = await page.evaluate((retired) => {
    const hits: string[] = []
    for (const el of Array.from(document.querySelectorAll('*')).slice(0, 4000)) {
      const cs = getComputedStyle(el)
      for (const prop of ['color', 'backgroundColor', 'borderTopColor']) {
        const v = cs[prop as 'color']
        if (retired.some((r) => v.replace(/\s/g, '') === r.replace(/\s/g, ''))) {
          hits.push(`${el.tagName}.${el.className}`.slice(0, 80) + ` ${prop}=${v}`)
        }
      }
    }
    return hits
  }, RETIRED)

  expect(offenders, `retired colours still rendering: ${offenders.slice(0, 5).join(' | ')}`).toHaveLength(0)
})

test('the logo follows the theme font rather than a hardcoded family', async ({ page }) => {
  await login(page)
  // AppLogo hardcoded 'Inter, sans-serif' and 'JetBrains Mono' before the fix;
  // neither was ever loaded, so it silently fell back to generic families.
  const fonts = await page.evaluate(() => {
    const spans = Array.from(document.querySelectorAll('header span, aside span, nav span'))
    return spans.map((s) => getComputedStyle(s).fontFamily).filter(Boolean)
  })
  const bad = fonts.filter((f) => /Inter|JetBrains|IBM Plex|Sora/i.test(f))
  expect(bad, `hardcoded webfont families still rendering: ${bad.join(' | ')}`).toHaveLength(0)
})

test('no webfont is requested over the network', async ({ page }) => {
  const fontRequests: string[] = []
  page.on('request', (r) => {
    const u = r.url()
    if (/fonts\.(googleapis|gstatic)\.com/.test(u) || /\.(woff2?|ttf|otf)(\?|$)/.test(u)) {
      fontRequests.push(u)
    }
  })
  await login(page)
  await page.goto(`${BASE}/overview`, { waitUntil: 'networkidle' })
  expect(fontRequests, `webfonts fetched: ${fontRequests.join(', ')}`).toHaveLength(0)
})

test('page load does not fire duplicate API requests', async ({ page }) => {
  await login(page)

  const calls: string[] = []
  page.on('request', (r) => {
    const u = new URL(r.url())
    if (u.pathname.startsWith('/api/')) calls.push(u.pathname + u.search)
  })

  await page.goto(`${BASE}/overview`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(2500) // let any post-mount refetch settle

  const counts = new Map<string, number>()
  for (const c of calls) counts.set(c, (counts.get(c) ?? 0) + 1)
  const dupes = [...counts.entries()].filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1])

  console.log(`overview issued ${calls.length} API requests, ${counts.size} distinct`)
  for (const [path, n] of dupes) console.log(`  x${n}  ${path}`)

  // Reported, not asserted to zero: SWR revalidation can legitimately repeat a
  // key. A request firing 3+ times on a single load is the shape worth flagging.
  const heavy = dupes.filter(([, n]) => n >= 3)
  expect(heavy, `requests fired 3+ times on one page load: ${JSON.stringify(heavy)}`).toHaveLength(0)
})
