/**
 * Action sweep: on every page, exercise the read-only controls a person uses --
 * tabs, filter dropdowns, disclosure and sort toggles, view/open/details
 * buttons, one row per table -- and FAIL on any 5xx, uncaught page error or
 * error screen an action causes. Unexpected 4xx, console errors and error
 * toasts are recorded to sweep-results/action.jsonl (the 2026-10-10 run
 * found viewers offered a QA-engineer-only "Refresh" this way).
 *
 * Never clicks anything that writes, sends, runs, deletes or downloads: an
 * allowlist picks candidates and a denylist vetoes them. Settings pages get
 * their tabs only.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'

import { BASE, HAVE_FIXTURES, ROLES, ROUTES, hardFailures, record, scrape, settle, signIn, watch } from './common'

test.skip(!HAVE_FIXTURES, 'set SWEEP_FIXTURES (backend/scripts/sweep_fixtures.py) to run the sweeps')

const MAX_ACTIONS = Number(process.env.SWEEP_MAX_ACTIONS ?? 25)

const DENY = /delete|remove|deprecate|archive|purge|reset|revoke|disable|enable|deactivat|activat|publish|approve|reject|send|submit|save|create|\badd\b|\bnew\b|import|upload|\brun\b|re-?run|trigger|analy[sz]e|generate|promote|merge|unlink|\blink\b|assign|quarantine|\brelease\b|sync|test connection|probe|invite|log ?out|sign out|clear|apply|confirm|mute|notify|correct|classify|acknowledge|dismiss|resolve|mark|rotate|copy|download|export|regenerate|restore|accept|decline|cancel|stop|pause|resume|retire|snooze|file\b|jira|edit|update|change|set |toggle|request|start|launch|investigate|ask|chat|feedback|vote|pin|star|follow|subscribe|bulk|select all|ai |explain|refresh/i
const SAFE = /^(view|open|show|details?|more|less|expand|collapse|next|previous|prev|insights|history|filters?|sort|all|retry|help|back|load more|see all|why|summary|overview|trend|chart|table|list|grid|week|day|month|\d+d)\b/i
const SKIP_SELECT = /project|release|language|theme/i

interface Candidate { kind: 'tab' | 'button' | 'select' | 'row'; label: string; locator: Locator }

async function nameOf(l: Locator): Promise<string> {
  const aria = await l.getAttribute('aria-label').catch(() => null)
  const text = aria ?? (await l.innerText().catch(() => ''))
  return text.trim().replace(/\s+/g, ' ').slice(0, 80)
}

async function visible(l: Locator): Promise<boolean> {
  return (await l.isVisible().catch(() => false)) && (await l.isEnabled().catch(() => false))
}

async function candidates(page: Page, tabsOnly: boolean): Promise<Candidate[]> {
  const out: Candidate[] = []
  const seen = new Set<string>()
  const push = (c: Candidate) => {
    const key = `${c.kind}:${c.label}`
    if (!c.label || seen.has(key) || DENY.test(c.label)) return
    seen.add(key)
    out.push(c)
  }
  const main = page.locator('main')
  for (const l of await main.locator('[role="tab"]').all()) {
    if (await visible(l)) push({ kind: 'tab', label: await nameOf(l), locator: l })
  }
  if (tabsOnly) return out.slice(0, MAX_ACTIONS)
  for (const l of await main.locator('select').all()) {
    if (!(await visible(l))) continue
    const label = (await nameOf(l)) || (await l.getAttribute('name').catch(() => '')) || 'select'
    if (!SKIP_SELECT.test(label)) push({ kind: 'select', label: `select ${label}`.slice(0, 80), locator: l })
  }
  for (const l of await main.locator('button, [role="button"]').all()) {
    if (!(await visible(l))) continue
    const label = await nameOf(l)
    const disclosure = (await l.getAttribute('aria-expanded').catch(() => null)) !== null
    if (disclosure || SAFE.test(label)) push({ kind: 'button', label, locator: l })
  }
  const first = main.locator('tbody tr').first()
  if (await first.isVisible().catch(() => false)) {
    const label = (await first.innerText().catch(() => '')).split('\n')[0].trim().slice(0, 60)
    push({ kind: 'row', label: `row: ${label}`, locator: first.locator('td').first() })
  }
  return out.slice(0, MAX_ACTIONS)
}

async function act(c: Candidate): Promise<void> {
  if (c.kind === 'select') {
    const values = await c.locator.locator('option').evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value))
    if (values.length > 1) await c.locator.selectOption(values[1])
    return
  }
  await c.locator.click({ timeout: 5_000 })
}

for (const role of ROLES) {
  test.describe(`action sweep as ${role}`, () => {
    for (const route of ROUTES) {
      test(`${role} ${route}`, async ({ browser }) => {
        const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } })
        await signIn(ctx, role)
        const page = await ctx.newPage()
        await page.goto(route, { waitUntil: 'domcontentloaded', timeout: 30_000 })
        await settle(page)
        const tabsOnly = route.startsWith('/settings') || route === '/users' || route === '/projects'
        const list = await candidates(page, tabsOnly)
        const routePath = new URL(page.url()).pathname
        const failures: string[] = []
        let tried = 0
        for (const c of list) {
          const w = watch(page)
          try {
            await act(c)
          } catch {
            w.stop()
            continue // covered or detached: not a product finding
          }
          tried++
          await settle(page, 5_000)
          await scrape(page, w.errors)
          w.stop()
          const landedOn = new URL(page.url()).pathname
          if (Object.values(w.errors).some((v) => v.length)) {
            record('action.jsonl', { role, route, action: `${c.kind}: ${c.label}`, landedOn, ...w.errors })
          }
          failures.push(...hardFailures(w.errors).map((f) => `[${c.kind}: ${c.label}] ${f}`))
          await page.keyboard.press('Escape').catch(() => undefined)
          if (landedOn !== routePath) {
            await page.goto(route, { waitUntil: 'domcontentloaded' }).catch(() => undefined)
            await settle(page, 6_000)
          }
        }
        record('action.jsonl', { role, route, summary: true, candidates: list.length, tried })
        await ctx.close()
        expect(failures, `${role} ${route}`).toEqual([])
      })
    }
  })
}
