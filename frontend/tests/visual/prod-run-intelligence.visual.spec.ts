/**
 * BEFORE/AFTER baseline of /runs/<run>/intelligence (Wave 2.5, VIZ-104):
 * the verdict card with its composite-risk gradient fill (G6), at risk 58
 * (a conditional go). The card has no landmark name, so it is found from
 * its "Composite risk score" label.
 * Harness and fail-closed rules: `tests/lib/production-pages.ts`.
 */
import { expect, test } from '@playwright/test'
import { assertHermetic, cardAround, openProductionPage, PINNED, THEMES, visualRegion } from '../lib/production-pages'
import { NOW, PROJECT_ID, RUN_ID, RUN_INTELLIGENCE, USER } from './production/fixtures'

test.use(PINNED)

for (const theme of THEMES) {
  test(`run intelligence regions — ${theme}`, async ({ page }) => {
    const { api, errors } = await openProductionPage(page, `/runs/${RUN_ID}/intelligence`, {
      theme,
      now: NOW,
      me: USER,
      user: USER,
      projectId: PROJECT_ID,
      handlers: RUN_INTELLIGENCE,
      ready: (p) => p.getByText('Composite risk score', { exact: true }),
    })

    const verdict = cardAround(page.getByText('Composite risk score', { exact: true }))
    await expect(verdict).toContainText('58')

    await visualRegion(page, 'intelligence-verdict', theme, verdict)
    assertHermetic(api, errors)
  })
}
