/**
 * W36 STRAT-03 on `/failures` › Groups › Systemic flake clusters, hermetic (`W36-contract.md` §1, §3): the
 * suggest-only "Create epic for this cluster" action, opt-in per project.
 *
 *  - opted in: every recurring cluster (`co_failure_runs >= 3`) offers "Create epic for this cluster", a cluster
 *    below 3 offers nothing; the form shows the server's preview first (nothing posted), Create sends exactly
 *    `{ jira_project_key, summary, confirm_not_filed: false }`, the filed link appears and the status is re-read;
 *    closed, the row shows "Epic <KEY>";
 *  - a stored link: "Epic <KEY>" opening Jira in a new tab, the other clusters still offer Create;
 *  - Jira offline: the button disabled with the server's reason as visible text, nothing read or posted;
 *  - opt-in off (the default): no epic action at all; ONE status read per visit to the tab.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). Fixtures: `FAILURES_ON` + `tests/lib/w36-fixtures.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { landmark, respond, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import {
  FAILURES_ON,
  PROJECT_ID,
  SYSTEMIC_CLUSTERS,
  SYSTEMIC_CLUSTERS_PATH,
  systemicClustersBody,
} from '../visual/production/fixtures'
import { VIEWER } from '../visual/production/fixtures-releases'
import {
  clusterEpicLink,
  clusterEpicPath,
  clusterEpicPreview,
  clusterEpicsPath,
  clusterEpicsStatus,
} from '../lib/w36-fixtures'

test.use({ viewport: { width: 1280, height: 4000 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const P = PROJECT_ID
const CREATE = 'Create epic for this cluster'
const OFFLINE_REASON = 'Jira is unavailable: AI_OFFLINE_MODE is on, so outbound Jira calls are blocked.'
const STATUS_PATH = clusterEpicsPath(P)
const [FIRST, SECOND, THIRD] = SYSTEMIC_CLUSTERS
const ready = (p: Page) => landmark(p, 'Failure verdict')
const groups = (page: Page) => page.locator('[data-catalogue-section="failures-groups"]')
const clusterRow = (page: Page, membershipKey: string) => groups(page).locator(`[data-cluster-key="${membershipKey}"]`)

/** The clusters with the third one below the recurrence rule (2 runs failing together); a project is required. */
function clustersWithOneBelowTheRule(request: ApiRequest) {
  if (request.url.searchParams.get('project_id') !== P) return respond(422, { detail: 'project_id is required' })
  const body = systemicClustersBody(30)
  return { ...body, items: body.items.map((item, i) => (i === 2 ? { ...item, co_failure_runs: 2 } : item)) }
}

async function openClusters(page: Page, handlers: ApiHandlers, user?: typeof VIEWER) {
  const opened = await openRollout(page, '/failures', { handlers: [...handlers, ...FAILURES_ON], ready, user })
  const tab = groups(page).getByRole('tab', { name: 'Systemic flake clusters' })
  await expect(tab).toBeVisible({ timeout: 20_000 })
  await networkQuiet(page, opened.api)
  expect(requestsTo(opened.api, STATUS_PATH), 'the epic status is asked only with the clusters tab open').toEqual([])
  await tab.click()
  await expect(groups(page).locator('[data-clusters-list]')).toBeVisible({ timeout: 15_000 })
  return opened
}

test('opted in: recurring clusters offer Create; the preview first; Create posts exactly the body; the link appears', async ({ page }) => {
  let linked = false
  const posted: unknown[] = []
  const handlers: ApiHandlers = [
    [SYSTEMIC_CLUSTERS_PATH, clustersWithOneBelowTheRule],
    [STATUS_PATH, () => clusterEpicsStatus({ enabled: true, links: linked ? [clusterEpicLink(FIRST.membership_key, 'PAY-101', FIRST.label)] : [] })],
    [`${clusterEpicPath(P, FIRST.membership_key)}/preview`, () => clusterEpicPreview(FIRST.membership_key, FIRST.label, FIRST.size, FIRST.co_failure_runs)],
    [
      clusterEpicPath(P, FIRST.membership_key),
      ({ route }) => {
        posted.push(route.request().postDataJSON())
        linked = true
        return respond(201, { membership_key: FIRST.membership_key, jira_key: 'PAY-101', jira_url: 'https://jira.example.test/browse/PAY-101', created: true })
      },
      'POST',
    ],
  ]
  const { api, errors } = await openClusters(page, handlers)
  await expect(clusterRow(page, FIRST.membership_key).getByRole('button', { name: CREATE })).toBeEnabled()
  await expect(clusterRow(page, SECOND.membership_key).getByRole('button', { name: CREATE })).toBeEnabled()
  // 2 runs failing together: below the rule, no action at all.
  await expect(clusterRow(page, THIRD.membership_key).locator('[data-cluster-epic]')).toHaveCount(0)

  await clusterRow(page, FIRST.membership_key).getByRole('button', { name: CREATE }).click()
  const form = clusterRow(page, FIRST.membership_key).getByRole('region', { name: CREATE })
  await expect(form.getByRole('textbox', { name: 'Jira project key' })).toHaveValue('PAY')
  await expect(form.getByRole('textbox', { name: 'Summary' })).toHaveValue(`[TestLookup] Recurring failure cluster: ${FIRST.label}`)
  await expect(form.getByLabel('Prefilled description')).toContainText(`Failed together in ${FIRST.co_failure_runs} runs`)
  await expect(form).toContainText('Nothing is filed until you click Create epic.')
  await networkQuiet(page, api)
  expect(posted, 'nothing is filed by opening the form').toEqual([])

  await form.getByRole('textbox', { name: 'Jira project key' }).fill('OPS')
  await form.getByRole('button', { name: 'Create epic' }).click()
  await expect(form.getByRole('status')).toHaveText('Filed epic PAY-101 in Jira.')
  expect(posted).toEqual([
    { jira_project_key: 'OPS', summary: `[TestLookup] Recurring failure cluster: ${FIRST.label}`, confirm_not_filed: false },
  ])
  const filed = form.getByRole('link', { name: 'PAY-101' })
  await expect(filed).toHaveAttribute('href', 'https://jira.example.test/browse/PAY-101')
  await expect(filed).toHaveAttribute('target', '_blank')
  await networkQuiet(page, api)
  expect(requestsTo(api, STATUS_PATH), 'the status is re-read after the create').toHaveLength(2)

  await form.getByRole('button', { name: 'Close' }).click()
  const epic = clusterRow(page, FIRST.membership_key).getByRole('link', { name: 'Epic PAY-101' })
  await expect(epic).toHaveAttribute('href', 'https://jira.example.test/browse/PAY-101')
  await expect(clusterRow(page, FIRST.membership_key).getByRole('button', { name: CREATE })).toHaveCount(0)
  expect(posted).toHaveLength(1)
  assertHermetic(api, errors)
})

test('a stored link: "Epic <KEY>" opens Jira in a new tab; the other clusters still offer Create', async ({ page }) => {
  const handlers: ApiHandlers = [
    [STATUS_PATH, () => clusterEpicsStatus({ enabled: true, links: [clusterEpicLink(SECOND.membership_key, 'PAY-77')] })],
  ]
  const { api, errors } = await openClusters(page, handlers)
  const epic = clusterRow(page, SECOND.membership_key).getByRole('link', { name: 'Epic PAY-77' })
  await expect(epic).toHaveAttribute('href', 'https://jira.example.test/browse/PAY-77')
  await expect(epic).toHaveAttribute('target', '_blank')
  await expect(epic).toHaveAttribute('rel', 'noreferrer')
  await expect(clusterRow(page, SECOND.membership_key).getByRole('button', { name: CREATE })).toHaveCount(0)
  await expect(clusterRow(page, FIRST.membership_key).getByRole('button', { name: CREATE })).toBeEnabled()
  await expect(clusterRow(page, THIRD.membership_key).getByRole('button', { name: CREATE })).toBeEnabled()
  await networkQuiet(page, api)
  expect(requestsTo(api, STATUS_PATH), 'ONE status read for every cluster on the tab').toHaveLength(1)
  assertHermetic(api, errors)
})

test('Jira offline: the button is disabled with the server reason as visible text; nothing read or posted', async ({ page }) => {
  const handlers: ApiHandlers = [
    [STATUS_PATH, () => clusterEpicsStatus({ enabled: true, available: false, epic_type_available: null, reason: OFFLINE_REASON })],
  ]
  const { api, errors } = await openClusters(page, handlers)
  for (const cluster of SYSTEMIC_CLUSTERS) {
    const row = clusterRow(page, cluster.membership_key)
    await expect(row.getByRole('button', { name: CREATE })).toBeDisabled()
    await expect(row.locator('[data-cluster-epic-reason]')).toHaveText(OFFLINE_REASON)
    await expect(row.locator('[data-cluster-epic-reason]')).toBeVisible()
  }
  await networkQuiet(page, api)
  expect(observed(api).filter((line) => line.includes('/epic/') || line.startsWith('POST')), 'no preview, no POST').toEqual([])
  assertHermetic(api, errors)
})

test('opt-in off (the default): no epic action at all; a viewer of an opted-in project is offered no Create', async ({ page }) => {
  const off = await openClusters(page, [[STATUS_PATH, () => clusterEpicsStatus()]])
  await expect(groups(page).locator('[data-cluster-key]')).toHaveCount(SYSTEMIC_CLUSTERS.length)
  await networkQuiet(page, off.api)
  expect(requestsTo(off.api, STATUS_PATH)).toHaveLength(1)
  await expect(groups(page).locator('[data-cluster-epic]')).toHaveCount(0)
  await expect(groups(page).getByRole('button', { name: CREATE })).toHaveCount(0)
  assertHermetic(off.api, off.errors)

  const viewer = await openClusters(page, [[STATUS_PATH, () => clusterEpicsStatus({ enabled: true })]], VIEWER)
  await networkQuiet(page, viewer.api)
  await expect(groups(page).locator('[data-cluster-epic]')).toHaveCount(0)
  assertHermetic(viewer.api, viewer.errors)
})
