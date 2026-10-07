/**
 * UX redesign P5 items 3 + 4 on a real browser, hermetic (agent B's pages).
 *
 * - GitHub is configured in ONE place: the Integrations page has no GitHub
 *   card, only a link to `/settings/github`.
 * - The two API-key surfaces are named apart: the project's "Streaming API
 *   keys" (`/settings/api-keys`, asked for with the project) and the user's
 *   own "My API keys" (a tab of `/users`, asked for without one). Each links
 *   to the other.
 * - `/users` keeps its tab in `?tab=`: the settings sub-nav's "Members &
 *   access" opens Project access.
 * - Forms go two-column at >= 1280 px: two cards share a row.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, openProductionPage, type ApiHandlers } from '../lib/production-pages'
import { LAYOUT, NOW, PROJECT_ID, USER } from '../visual/production/fixtures'

const ADMIN = { ...USER, username: 'admin', full_name: 'Admin', role: 'ADMIN' }

const INTEGRATIONS_CONFIG = {
  jira_enabled: true,
  jira_domain: 'acme.atlassian.net',
  jira_email: 'qa@acme.test',
  jira_token_set: true,
  jira_default_project_key: 'QA',
  splunk_enabled: false,
  splunk_base_url: null,
  splunk_token_set: false,
  ocp_enabled: false,
  ocp_api_url: null,
  ocp_token_set: false,
  ocp_default_namespace: '',
  slack_enabled: false,
  slack_webhook_url: null,
  slack_webhook_set: false,
  slack_default_channel: '',
  teams_enabled: false,
  teams_webhook_url: null,
  teams_webhook_set: false,
  github_repo: 'acme/web',
  github_token_set: true,
}

const key = (id: string, name: string, projectId: string | null) => ({
  id,
  name,
  key_hint: `qai_${id}...`,
  scopes: projectId ? ['stream:write'] : [],
  project_id: projectId,
  is_active: true,
  expires_at: null,
  last_used_at: null,
  created_at: '2026-09-01T10:00:00Z',
})

/** `GET /api/v1/keys`: the project's keys when asked with one, the caller's own without. */
const keysAsked: (string | null)[] = []
const KEYS: ApiHandlers = [
  [
    '/api/v1/keys',
    ({ url }) => {
      const projectId = url.searchParams.get('project_id')
      keysAsked.push(projectId)
      return projectId ? [key('ci', 'ci-runner-prod', projectId)] : [key('mine', 'my-laptop-script', null)]
    },
  ],
]

const USERS: ApiHandlers = [
  ['/api/v1/users', () => [{ ...ADMIN, created_at: '2026-01-01T00:00:00Z' }]],
  [/^\/api\/v1\/projects\/[^/]+\/members$/, () => []],
]

const MFA: ApiHandlers = [
  [
    '/api/v1/auth/mfa/status',
    () => ({
      enabled: false,
      enrolled_at: null,
      recovery_codes_remaining: 0,
      required_by_policy: false,
      sso_managed: false,
      secret_unreadable: false,
    }),
  ],
]

const HANDLERS: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/settings/integrations', () => INTEGRATIONS_CONFIG],
  ...KEYS,
  ...USERS,
  ...MFA,
]

test.use({ viewport: { width: 1280, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

function open(page: Page, path: string, ready: (p: Page) => Locator) {
  return openProductionPage(page, path, {
    theme: 'signal',
    now: NOW,
    me: ADMIN,
    user: ADMIN,
    projectId: PROJECT_ID,
    handlers: HANDLERS,
    ready,
  })
}

const h1 = (name: string) => (p: Page) => p.getByRole('heading', { level: 1, name, exact: true })

/** Two boxes side by side: one top, the second to the right of the first. */
async function expectSideBySide(left: Locator, right: Locator) {
  const a = await left.boundingBox()
  const b = await right.boundingBox()
  if (!a || !b) throw new Error('both boxes must render')
  expect(Math.abs(a.y - b.y)).toBeLessThan(1)
  expect(b.x).toBeGreaterThanOrEqual(a.x + a.width)
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.locator('#main-content').evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
}

test('Integrations: no GitHub card, one link to /settings/github, cards two-up at 1280 px', async ({ page }) => {
  const { api, errors } = await open(page, '/settings/integrations', h1('Integrations'))
  const card = (name: string) => page.locator('.card').filter({ has: page.getByRole('heading', { level: 3, name, exact: true }) })

  await expect(card('Jira')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'GitHub', exact: true })).toHaveCount(0)
  await expect(page.getByLabel('Repository', { exact: true })).toHaveCount(0)
  const link = page.getByRole('link', { name: 'GitHub settings' })
  await expect(link).toHaveAttribute('href', '/settings/github')

  await expectSideBySide(card('Jira'), card('Splunk'))
  await expectSideBySide(card('OpenShift / Kubernetes'), card('Slack'))
  await expectNoHorizontalOverflow(page)
  assertHermetic(api, errors)
})

test('Streaming API keys: the project\'s keys, current in the sub-nav, linking to My API keys', async ({ page }) => {
  keysAsked.length = 0
  const { api, errors } = await open(page, '/settings/api-keys', h1('Streaming API keys'))

  await expect(page.locator('[data-settings-item="api-keys"]')).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('cell', { name: 'ci-runner-prod' })).toBeVisible()
  expect(keysAsked).toContain(PROJECT_ID)

  await page.getByRole('link', { name: 'My API keys' }).click()
  await expect(page).toHaveURL(/\/users\?tab=api-keys$/)
  await expect(page.getByRole('tab', { name: 'My API keys' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('cell', { name: 'my-laptop-script' })).toBeVisible()
  // No sub-nav item is 'My API keys' (the lead's call): the page's own entry, Users, is current.
  await expect(page.locator('[data-settings-item="users"]')).toHaveAttribute('aria-current', 'page')
  // The user's own keys are asked for without a project.
  expect(keysAsked).toContain(null)
  // The tab's own pointer back (the sub-nav carries a link of the same name).
  await expect(
    page.locator('[data-my-api-keys-intro]').getByRole('link', { name: 'Streaming API keys' }),
  ).toHaveAttribute('href', '/settings/api-keys')
  assertHermetic(api, errors)
})

test('/users: the sub-nav\'s "Members & access" opens Project access, and the tab is in the URL', async ({ page }) => {
  const { api, errors } = await open(page, '/users', h1('User Management'))
  await expect(page.getByRole('tab', { name: 'Users' })).toHaveAttribute('aria-selected', 'true')

  await page.locator('[data-settings-item="members"]').click()
  await expect(page).toHaveURL(/\/users\?tab=project-members$/)
  await expect(page.getByRole('tab', { name: 'Project access' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-settings-item="members"]')).toHaveAttribute('aria-current', 'page')
  await expect(page.getByRole('tab', { name: 'API Keys', exact: true })).toHaveCount(0)
  assertHermetic(api, errors)
})

test('Profile: the profile and password cards share a row at 1280 px', async ({ page }) => {
  const { api, errors } = await open(page, '/settings/profile', h1('My Profile'))
  const section = (name: string) => page.locator('section.card').filter({ has: page.getByRole('heading', { level: 2, name }) })

  await expectSideBySide(section('Profile Information'), section('Change Password'))
  await expectNoHorizontalOverflow(page)
  assertHermetic(api, errors)
})
