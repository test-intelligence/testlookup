/**
 * Route sweep: every page in App.tsx, as every role in the fixtures, against a
 * live deployment. FAILS on a 5xx, an uncaught page error, or an error screen;
 * records unexpected 4xx, console errors and error toasts to
 * sweep-results/route.jsonl for triage. See common.ts for how to run.
 */
import { expect, test } from '@playwright/test'

import { BASE, HAVE_FIXTURES, ROLES, ROUTES, hardFailures, record, scrape, settle, signIn, watch } from './common'

test.skip(!HAVE_FIXTURES, 'set SWEEP_FIXTURES (backend/scripts/sweep_fixtures.py) to run the sweeps')

for (const role of ROLES) {
  test.describe(`route sweep as ${role}`, () => {
    for (const route of ROUTES) {
      test(`${role} ${route}`, async ({ browser }) => {
        const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } })
        await signIn(ctx, role)
        const page = await ctx.newPage()
        const w = watch(page)
        await page.goto(route, { waitUntil: 'domcontentloaded', timeout: 30_000 })
        await settle(page)
        await scrape(page, w.errors)
        const finalPath = new URL(page.url()).pathname
        w.stop()
        record('route.jsonl', { role, route, finalPath, ...w.errors })
        await ctx.close()
        expect(finalPath, 'sent to sign-in: the fixtures token was refused').not.toMatch(/^\/login/)
        expect(hardFailures(w.errors), `${role} ${route}`).toEqual([])
      })
    }
  })
}
