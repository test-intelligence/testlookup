/**
 * UX redesign P4 item 5 (`02-design-spec.md` §5 "Inbox", owner decision D4),
 * on /my-failures, hermetic: one inbox, tabs Assigned to me · Approvals (AI
 * reports, quarantine proposals and test-case approvals, filtered by source).
 *
 * At 1440 x 900 the page's primary content (`[data-primary]`, the assigned
 * failures table) starts at most 300 px below the top of `#main-content`. The
 * scroller's `scrollHeight` is printed (FOLD line) so the page's height before
 * / after can be compared: measured on the unchanged page first (P4 agent C's
 * report, `docs/viz-work/p4-agent-C.md`: 2029 px, the table at 140 px with
 * 74 px rows).
 *
 * Also held here: the request inventory of one load (the Approvals count asks
 * for its three queues; nothing else), the Approvals tab and its source
 * filter, a decision taken from the inbox, and `/reviews` landing on Approvals
 * (the All Projects prompt is unit-tested: `MyFailuresPage.inbox.test.tsx`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, SHELL_BASE, expectInventory, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { PROJECT_ID } from '../visual/production/fixtures'
import { inboxHandlers } from '../visual/production/fixtures-pages'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const P = PROJECT_ID

const ready = (p: Page) => p.locator('[data-page-header] h1')

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface FoldGeometry {
  scrollHeight: number
  primaryTop: number | null
  primaries: number
  blocks: Record<string, [number, number] | null>
}

/** The scroller's height and the primary content's top, px below the scroller's top (scrolled to 0). */
async function foldGeometry(page: Page): Promise<FoldGeometry> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const span = (css: string): [number, number] | null => {
      const box = document.querySelector(css)?.getBoundingClientRect()
      return box ? [Math.round(box.top - top), Math.round(box.bottom - top)] : null
    }
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
      blocks: {
        header: span('[data-page-header]'),
        tabs: span('[data-page-header] [role="tablist"]'),
        firstTable: span('table'),
        firstRow: span('tbody tr'),
      },
    }
  }, MAIN)
}

const ASSIGNED_LINE = `GET /api/v1/me/assigned-failures?project_id=${P}&days=30&page=1&size=25&scope=team`
/** The Approvals tab label's count: the three queues, asked once each. */
const APPROVAL_COUNT_LINES = [
  `GET /api/v1/projects/${P}/reviews?limit=200&state=pending_review`,
  `GET /api/v1/quarantine?project_id=${P}&live_only=true&limit=200`,
  `GET /api/v1/test-management/cases?project_id=${P}&status=review_requested&size=50`,
  `GET /api/v1/test-management/cases?project_id=${P}&status=under_review&size=50`,
]

test('the assigned failures table starts within the fold budget at 1440 x 900', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: inboxHandlers(), ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /my-failures ${JSON.stringify(geometry)}`)
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  // It is the failures table, its first row on screen, and the rows are two lines (were up to four).
  const table = page.locator('[data-primary]').getByRole('table', { name: 'Assigned failures' })
  await expect(table).toBeVisible()
  await expect(table.getByRole('row').nth(1)).toBeInViewport()
  const [rowTop, rowBottom] = geometry.blocks.firstRow ?? [0, 0]
  expect(rowBottom - rowTop, 'a two-line row').toBeLessThanOrEqual(56)
  // The tabs, with their counts: 40 open, 1 report + 2 proposals + 2 cases waiting.
  // A lead opens on the team scope, so the tab is the team's (it read
  // "Assigned to me" over the team's count; browser E2E pass, 2026-10-08).
  await expect(page.getByRole('tab', { name: /^Team failures/ })).toHaveText('Team failures40')
  await expect(page.getByRole('tab', { name: /^Approvals/ })).toHaveText('Approvals5')
  // §5 Delete: no Status column, no static status chip.
  await expect(table.getByRole('columnheader', { name: 'Status' })).toHaveCount(0)
  await expect(page.getByText('FAILED, BROKEN')).toHaveCount(0)
  expectInventory(api, errors, [...SHELL_BASE, ASSIGNED_LINE, ...APPROVAL_COUNT_LINES], '/my-failures, Assigned to me')
})

test('Approvals: the three sources, the source filter, and a decision taken from the inbox', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: inboxHandlers(), ready })
  await networkQuiet(page, api)
  await page.getByRole('tab', { name: /^Approvals/ }).click()
  await expect(page).toHaveURL(/\/my-failures\?tab=approvals$/)

  const primary = page.locator('[data-primary]')
  await expect(primary.getByRole('region', { name: 'AI reports' }).getByTestId('review-row')).toHaveCount(1)
  await expect(primary.getByRole('region', { name: 'Quarantine proposals' }).getByTestId('quarantine-row')).toHaveCount(2)
  await expect(primary.getByRole('region', { name: 'Test cases' }).getByTestId('case-approval-row')).toHaveCount(2)
  // The report opens on its run's Analysis tab (P4), not the old Run Intelligence URL.
  await expect(primary.getByRole('link', { name: 'Open report' })).toHaveAttribute(
    'href',
    '/runs/00000000-0000-4000-8000-000000000500?tab=analysis',
  )

  // Narrow to one source; the choice is in the URL.
  await page.getByRole('radio', { name: 'AI reports 1' }).click()
  await expect(page).toHaveURL(/\/my-failures\?tab=approvals&source=reports$/)
  await expect(primary.getByRole('region', { name: 'Quarantine proposals' })).toHaveCount(0)

  // Accept the report: the row leaves the pending queue and the counts follow.
  await page.getByTestId('review-accept').click()
  await page.getByTestId('review-notes').fill('Evidence checked')
  await page.getByTestId('review-confirm').click()
  await expect(page.getByTestId('review-row')).toHaveCount(0)
  await expect(page.getByRole('tab', { name: /^Approvals/ })).toHaveText('Approvals4')

  // Opening the tab asked for the quarantine counts the proposals table refreshes after a decision.
  expect(requestsTo(api, '/api/v1/quarantine/stats').length).toBeGreaterThan(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('/reviews lands on Approvals', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/my-failures', { handlers: inboxHandlers(), ready })
  await page.goto('/reviews')
  await expect(page).toHaveURL(/\/my-failures\?tab=approvals$/)
  await expect(page.getByRole('tab', { name: /^Approvals/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('review-row')).toHaveCount(1)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
