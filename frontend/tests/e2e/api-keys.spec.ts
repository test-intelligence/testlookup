import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson, seedActiveProject } from './apiMock';

/**
 * API Keys management (/settings/api-keys) — deep, mocked CRUD coverage.
 *
 * The page is project-scoped: in "All Projects" mode it shows a "Select a
 * project" gate. We seed an active project + mock the projects list so the
 * real management UI renders. Each test still tolerates the gate (if project
 * seeding doesn't engage in a given env) by asserting that valid state instead
 * of false-failing — matching the resilient style of the existing specs.
 */

const KEY_ROW = {
  id: 'key-1',
  name: 'ci-runner-prod',
  key_hint: 'tl_abcd...',
  scopes: ['stream:write'],
  project_id: '00000000-0000-4000-8000-0000000000e2',
  is_active: true,
  expires_at: null,
  last_used_at: null,
  created_at: '2026-06-01T10:00:00Z',
};

async function onManagementUi(page: import('@playwright/test').Page): Promise<boolean> {
  // True when the real keys UI rendered; false when the all-projects gate shows.
  const gate = await page.getByText(/select a project/i).isVisible().catch(() => false);
  return !gate;
}

test.describe('API Keys — management', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('lists existing keys for the active project', async ({ page }) => {
    await seedActiveProject(page);
    await mockJson(page, '**/api/v1/keys*', [KEY_ROW]);

    await page.goto('/settings/api-keys');
    // `exact` matters: the empty state renders <h3>No API keys yet</h3>, which
    // also matches /api keys/i, so a loose name resolves to two headings and
    // dies on strict mode whenever the list happens to be empty.
    await expect(
      page.getByRole('heading', { name: 'API Keys', exact: true }),
    ).toBeVisible({ timeout: 10000 });

    if (await onManagementUi(page)) {
      await expect(page.getByText('ci-runner-prod')).toBeVisible({ timeout: 8000 });
      await expect(page.getByText('stream:write')).toBeVisible();
    } else {
      await expect(page.getByText(/select a project/i)).toBeVisible();
    }
  });

  test('generate-key flow reveals the one-time raw key', async ({ page }) => {
    await seedActiveProject(page);
    await mockJson(page, '**/api/v1/keys*', []); // start with no keys
    // POST create → return a freshly minted key with raw_key (shown once).
    await page.route('**/api/v1/keys', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ ...KEY_ROW, raw_key: 'tl_secret_raw_key_value_e2e' }),
        });
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      }
    });

    await page.goto('/settings/api-keys');
    // `exact` matters: the empty state renders <h3>No API keys yet</h3>, which
    // also matches /api keys/i, so a loose name resolves to two headings and
    // dies on strict mode whenever the list happens to be empty.
    await expect(
      page.getByRole('heading', { name: 'API Keys', exact: true }),
    ).toBeVisible({ timeout: 10000 });

    // Fail closed: the management UI not rendering is the failure this test
    // exists to catch, so it must not be the reason the test opts out.
    await expect(
      page.getByRole('button', { name: /generate streaming key/i }),
      'the API-keys management UI did not render for a seeded admin session',
    ).toBeVisible({ timeout: 10000 });
    const generate = page.getByRole('button', { name: /generate streaming key/i });

    await generate.click();
    await page.locator('input[placeholder="ci-runner-prod"]').fill('e2e-key');
    await page.getByRole('button', { name: /^generate$/i }).click();

    // The created-key modal shows the raw key once with a copy affordance.
    // The raw key is shown in its own <code> element AND echoed into three
    // ready-to-paste snippets, so an unanchored getByText matches 4 elements
    // and dies on strict mode -- a product that got MORE helpful reading as a
    // broken one. Assert the reveal element itself.
    await expect(
      page.getByText('tl_secret_raw_key_value_e2e', { exact: true }).first(),
    ).toBeVisible({ timeout: 8000 });
    await expect(page.getByText(/copy the key now/i)).toBeVisible();

    // Dismiss the modal.
    await page.getByRole('button', { name: /^done$/i }).click();
    await expect(page.getByText('tl_secret_raw_key_value_e2e')).toBeHidden({ timeout: 8000 });
  });

  test('revoke asks for confirmation before deleting', async ({ page }) => {
    await seedActiveProject(page);
    await mockJson(page, '**/api/v1/keys*', [KEY_ROW]);
    let deleteCalled = false;
    await page.route('**/api/v1/keys/key-1', async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteCalled = true;
        await route.fulfill({ status: 204, body: '' });
      } else {
        await route.continue();
      }
    });

    await page.goto('/settings/api-keys');
    // `exact` matters: the empty state renders <h3>No API keys yet</h3>, which
    // also matches /api keys/i, so a loose name resolves to two headings and
    // dies on strict mode whenever the list happens to be empty.
    await expect(
      page.getByRole('heading', { name: 'API Keys', exact: true }),
    ).toBeVisible({ timeout: 10000 });

    const revoke = page.getByRole('button', { name: /revoke/i });
    await expect(
      revoke.first(),
      'the revoke control did not render for a seeded admin session',
    ).toBeVisible({ timeout: 10000 });

    // First, dismiss the confirm() → no delete.
    page.once('dialog', (d) => d.dismiss());
    await revoke.click();
    expect(deleteCalled).toBe(false);

    // Then accept it → DELETE fires + success toast.
    page.once('dialog', (d) => d.accept());
    await revoke.click();
    await expect(page.getByText('API key revoked')).toBeVisible({ timeout: 8000 });
    expect(deleteCalled).toBe(true);
  });
});
