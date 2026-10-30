/**
 * W23c (§3.4): the run page's Export PDF says WHY a download was refused, once
 * (`docs/deferred-scope-2026-10-13/work/W23c-contract.md` §3.4, §6.3), hermetic.
 *
 * Once the review gate is enforced, `GET /api/v1/reports/runs/{id}/pdf` answers an unreviewed AI report with 409
 * `{"detail": {"code": "report_pending_review", "message": …}}` — a Blob to the SPA (the request asks for one). The
 * action shows ONE toast with the server's message: not "PDF export failed", not the interceptor's "Request failed
 * with status code 409". A network failure still says "PDF export failed".
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: the run page's `RUN_PAGE`
 * (`fixtures-run.ts`), with the PDF route answered first.
 */
import { expect, test, type Page } from '@playwright/test'
import { respond, type ApiHandler, type ApiHandlers } from '../lib/production-pages'
import { assertHermetic, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { RUN_PAGE } from '../visual/production/fixtures-run'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } })

const PDF = `/api/v1/reports/runs/${RUN_ID}/pdf`
const PENDING_MESSAGE =
  'This AI-generated report has not been accepted by a human reviewer, so it cannot be distributed.'

const ready = (p: Page) => p.locator('[data-primary] table')

async function exportPdf(page: Page, pdf: ApiHandler) {
  const handlers: ApiHandlers = [[PDF, pdf], ...RUN_PAGE]
  const opened = await openRollout(page, `/runs/${RUN_ID}`, { handlers, ready })
  await networkQuiet(page, opened.api)
  await page.locator('[data-page-header]').getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: 'Export PDF' }).click()
  return opened
}

test('a 409 report_pending_review reads as the server’s sentence, in ONE toast', async ({ page }) => {
  const { api, errors } = await exportPdf(page, () => respond(409, {
    detail: { code: 'report_pending_review', message: PENDING_MESSAGE },
  }))
  await expect(page.getByText(PENDING_MESSAGE)).toHaveCount(1)
  await networkQuiet(page, api)
  await expect(page.getByText(PENDING_MESSAGE)).toHaveCount(1)
  await expect(page.getByText('PDF export failed')).toHaveCount(0)
  await expect(page.getByText(/Request failed with status code/)).toHaveCount(0)
  expect(requestsTo(api, PDF)).toEqual([`GET ${PDF}?layout=executive`])
  assertHermetic(api, errors)
})

test('a failure with no words of the server’s still says "PDF export failed", once', async ({ page }) => {
  const { api, errors } = await exportPdf(page, () => respond(500))
  await expect(page.getByText('PDF export failed')).toHaveCount(1)
  await networkQuiet(page, api)
  await expect(page.getByText('PDF export failed')).toHaveCount(1)
  await expect(page.getByText(/Request failed with status code/)).toHaveCount(0)
  assertHermetic(api, errors)
})
