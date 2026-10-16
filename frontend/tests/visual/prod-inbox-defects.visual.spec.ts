/**
 * W06 DEF-06 (`docs/deferred-scope-2026-10-13/work/W06-contract.md` §2.6, §4.6): Inbox › Approvals › Defects,
 * the queue of promoted defects waiting for a QA lead. NEW region `inbox-defect-approvals`: the Defects
 * section with its one pending defect (title, the HIGH pill, component · team, "Waiting because:" and the
 * policy reasons, the Jira project it would be filed in, its age, Open run, Approve / Reject). No inbox visual
 * spec existed before this one.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`. Fixtures: `fixtures-pages.ts` (`inboxHandlers`).
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, USER } from './production/fixtures'
import { inboxHandlers, PENDING_DEFECT_TITLE } from './production/fixtures-pages'

test.use(PINNED)

for (const theme of THEMES) {
  test(`inbox defect approvals — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, '/my-failures?tab=approvals&source=defects', {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: inboxHandlers(),
      ready: (p) => p.locator('[data-primary]').getByRole('region', { name: 'Defects' }),
    })
    const queue = page.locator('[data-primary]').getByRole('region', { name: 'Defects' })
    await expect(queue.getByTestId('defect-approval-row')).toHaveCount(1)
    await expect(queue).toContainText(PENDING_DEFECT_TITLE)
    await expect(queue.getByTestId('defect-approve')).toBeEnabled()
    await visualRegion(page, 'inbox-defect-approvals', theme, queue)
    assertHermetic(api, errors)
  })
}
