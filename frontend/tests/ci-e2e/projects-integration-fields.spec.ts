/**
 * W10 INT-03 + INT-03b on `/projects` › Edit Project, hermetic (`docs/deferred-scope-2026-10-13/work/W10-contract.md`
 * §2.3, §4.1), at 1280 and 1440:
 *
 *  - as QA LEAD Edit Project has no Jenkins Job Pattern (the stored value is kept by the API, D22) and both help lines
 *    under Splunk Index (`#ep-splunk-help`) and OCP Namespace (`#ep-ocp-help`), each input described by its line;
 *  - `main | delete` → Save → the world's 422 (`ProjectUpdate`'s SPL-safe validator) → SPLUNK_INDEX_SENTENCE under
 *    Splunk Index, in the page, no toast, the form open with what was typed, nothing applied;
 *  - `payments` → exactly one `PUT /api/v1/projects/{id}` whose body is `{"splunk_index":"payments"}`; clearing it
 *    again sends `{"splunk_index":""}` (a cleared field hands the key back to the deployment's index);
 *  - a QA ENGINEER has no Edit control: `/projects` is a management route (`App.tsx` `ManagementGuard`), so the page
 *    sends them to `/overview` and nothing of `/projects` is asked or written;
 *  - `#main-content` never scrolls sideways and no column of the Integrations block overflows (printed `INPAGE`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `fixtures-integrations-runtime.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { expectNoOverlay, opened } from '../lib/inPage'
import { boxOf, expectMainFits, inPageLine, minContentSlack, ownOverflow } from '../lib/inPageGeometry'
import { assertHermetic, freezeClock, mockApi, seedSession, stillMotion, watchPageErrors } from '../lib/production-pages'
import { networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { NOW, OVERVIEW_ON, PROJECT_ID } from '../visual/production/fixtures'
import {
  BAD_SPLUNK_INDEX,
  EDITED_PROJECT,
  OCP_NAMESPACE_SENTENCE,
  projectsIntegrationWorld,
  QA_ENGINEER,
  QA_LEAD,
  SPLUNK_INDEX_SENTENCE,
} from '../visual/production/fixtures-integrations-runtime'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const SPLUNK_HELP = "Used by AI log lookups for this project. Empty: the deployment's index."
const OCP_HELP = "Used when a run names a pod but no namespace. Empty: the deployment's default namespace."
const PUT_PATH = `/api/v1/projects/${EDITED_PROJECT.id}`
const h1 = (p: Page) => p.getByRole('heading', { level: 1, name: 'Projects', exact: true })
const projectCard = (page: Page): Locator => page.locator('.card').filter({ has: page.getByText(EDITED_PROJECT.name, { exact: true }) })
const toast = (page: Page, text: string | RegExp) => page.getByRole('status').filter({ hasText: text })

async function openEdit(page: Page): Promise<Locator> {
  await projectCard(page).hover()
  await projectCard(page).getByRole('button', { name: 'Edit' }).click()
  const form = opened(page, 'Edit Project')
  await expect(form).toBeVisible()
  return form
}

/** The ids an input is described by. */
const describedBy = async (input: Locator) => ((await input.getAttribute('aria-describedby')) ?? '').split(' ').filter(Boolean)

/** No column of the Integrations block overflows, `#main-content` fits; the widths are printed. */
async function expectIntegrationColumnsFit(page: Page, form: Locator, width: number, what: string) {
  await expectMainFits(page, `${what} @${width}`)
  const numbers: Record<string, number> = {}
  for (const id of ['ep-jira', 'ep-splunk', 'ep-ocp']) {
    const column = form.locator(`#${id}`).locator('xpath=..')
    numbers[`${id}.width`] = (await boxOf(column)).width
    numbers[`${id}.slack`] = await minContentSlack(column)
    const overflow = await ownOverflow(column)
    numbers[`${id}.overflow`] = overflow
    expect(overflow, `${what}: the ${id} column does not overflow`).toBeLessThanOrEqual(0)
  }
  inPageLine(`/projects edit ${what}`, width, numbers)
}

for (const width of [1280, 1440]) {
  test.describe(`at ${width} px`, () => {
    test.use({ viewport: { width, height: 1200 } })

    test(`Edit Project: no Jenkins Job Pattern, the two help lines, each input described by its line — ${width}`, async ({ page }) => {
      const world = projectsIntegrationWorld()
      const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      await expect(projectCard(page)).toBeVisible()
      const form = await openEdit(page)
      await expect(form.getByLabel('Jenkins Job Pattern')).toHaveCount(0)
      await expect(form.getByText(/Jenkins/)).toHaveCount(0)
      await expect(form.getByLabel('Jira Project Key', { exact: true })).toHaveValue(EDITED_PROJECT.jira_project_key)
      const index = form.getByLabel('Splunk Index', { exact: true })
      const namespace = form.getByLabel('OCP Namespace', { exact: true })
      await expect(form.locator('#ep-splunk-help')).toHaveText(SPLUNK_HELP)
      await expect(form.locator('#ep-ocp-help')).toHaveText(OCP_HELP)
      expect(await describedBy(index)).toEqual(['ep-splunk-help'])
      expect(await describedBy(namespace)).toEqual(['ep-ocp-help'])
      await expectNoOverlay(page)
      await expectIntegrationColumnsFit(page, form, width, 'help lines')
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      assertHermetic(api, errors)
    })

    test(`an index the server refuses: its sentence under Splunk Index, no toast; then exactly the changed field — ${width}`, async ({ page }) => {
      const world = projectsIntegrationWorld()
      const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      await expect(projectCard(page)).toBeVisible()
      await networkQuiet(page, api)
      const form = await openEdit(page)
      const index = form.getByLabel('Splunk Index', { exact: true })

      await index.fill(BAD_SPLUNK_INDEX)
      await form.getByRole('button', { name: 'Save Changes' }).click()
      const error = form.locator('[data-field-error="splunk_index"]')
      await expect(error).toHaveText(SPLUNK_INDEX_SENTENCE)
      await expect(error).toHaveAttribute('role', 'alert')
      await expect(index).toHaveAttribute('aria-invalid', 'true')
      expect(await describedBy(index)).toEqual(['ep-splunk-help', String(await error.getAttribute('id'))])
      await expect(form, 'the form stays open').toBeVisible()
      await expect(index, 'with what was typed').toHaveValue(BAD_SPLUNK_INDEX)
      await expect(form.locator('[data-field-error]'), 'only that field has an error').toHaveCount(1)
      await expect(form.getByText(OCP_NAMESPACE_SENTENCE)).toHaveCount(0)
      await networkQuiet(page, api)
      await expect(toast(page, /Failed to update|Project updated|Splunk index/), 'no toast').toHaveCount(0)
      expect(world.refused).toEqual([{ line: `PUT ${PUT_PATH}`, body: { splunk_index: BAD_SPLUNK_INDEX } }])
      expect(world.writes, 'nothing applied').toEqual([])
      await expectIntegrationColumnsFit(page, form, width, 'index refused')

      await index.fill('payments')
      await expect(error, 'editing the field clears its error').toHaveCount(0)
      await form.getByRole('button', { name: 'Save Changes' }).click()
      await expect(form).toHaveCount(0)
      await expect(toast(page, 'Project updated')).toBeVisible()
      await networkQuiet(page, api)
      expect(world.writes).toEqual([{ line: `PUT ${PUT_PATH}`, body: { splunk_index: 'payments' } }])
      expect(requestsTo(api, PUT_PATH).filter((line) => line.startsWith('PUT'))).toEqual([`PUT ${PUT_PATH}`, `PUT ${PUT_PATH}`])

      // Cleared again: sent as "" (a null would be dropped by the PUT and could never clear it).
      const again = await openEdit(page)
      await expect(again.getByLabel('Splunk Index', { exact: true })).toHaveValue('payments')
      await again.getByLabel('Splunk Index', { exact: true }).fill('')
      await again.getByRole('button', { name: 'Save Changes' }).click()
      await expect(again).toHaveCount(0)
      await networkQuiet(page, api)
      expect(world.writes.map((w) => w.body)).toEqual([{ splunk_index: 'payments' }, { splunk_index: '' }])
      assertHermetic(api, errors)
    })

    test(`a namespace that is not a DNS label: its sentence under OCP Namespace — ${width}`, async ({ page }) => {
      const world = projectsIntegrationWorld()
      const { api, errors } = await openRollout(page, '/projects', { handlers: world.handlers, ready: h1, user: QA_LEAD })
      await expect(projectCard(page)).toBeVisible()
      const form = await openEdit(page)
      await form.getByLabel('OCP Namespace', { exact: true }).fill('QA Testing')
      await form.getByRole('button', { name: 'Save Changes' }).click()
      await expect(form.locator('[data-field-error="ocp_namespace"]')).toHaveText(OCP_NAMESPACE_SENTENCE)
      await expect(form.locator('[data-field-error]')).toHaveCount(1)
      await networkQuiet(page, api)
      expect(world.writes).toEqual([])
      await expectIntegrationColumnsFit(page, form, width, 'namespace refused')
      assertHermetic(api, errors)
    })
  })
}

test('a QA ENGINEER has no Edit control: /projects sends them to /overview, and nothing of it is asked', async ({ page }) => {
  const world = projectsIntegrationWorld()
  const errors = watchPageErrors(page)
  await seedSession(page, { theme: 'signal', user: QA_ENGINEER, projectId: PROJECT_ID })
  await freezeClock(page, NOW)
  await stillMotion(page)
  const api = await mockApi(page, QA_ENGINEER, [...world.handlers.filter(([, , verb]) => verb && verb !== 'GET'), ...OVERVIEW_ON])
  await page.goto('/projects')
  await expect(page).toHaveURL(/\/overview(\?|$)/)
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { level: 1, name: 'Projects', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Edit' })).toHaveCount(0)
  await networkQuiet(page, api)
  expect(world.writes).toEqual([])
  expect(world.refused).toEqual([])
  expect([...api.seen, ...api.unhandled].filter((line) => / \/api\/v1\/projects\/[^/?]+(\?|$)/.test(line)), 'no project read or write by id').toEqual([])
  assertHermetic(api, errors)
})
