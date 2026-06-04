import { Page } from '@playwright/test';

/**
 * Shared Playwright API-mocking helpers for deterministic e2e flows.
 *
 * The suite runs against a live backend whose data may be empty or vary
 * between runs, so flows that need specific data (CRUD, modals, error paths)
 * intercept the relevant endpoints with `page.route` instead. Register the
 * mocks BEFORE the navigation that triggers the request.
 */

/** Fulfil every request matching `glob` with a JSON body + status. */
export async function mockJson(
  page: Page,
  glob: string,
  body: unknown,
  status = 200,
): Promise<void> {
  await page.route(glob, async (route) => {
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
}

/** Fail every request matching `glob` with an error status + `{detail}` body. */
export async function mockError(
  page: Page,
  glob: string,
  status: number,
  detail = 'mocked error',
): Promise<void> {
  await page.route(glob, async (route) => {
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify({ detail }),
    });
  });
}

export interface SeedProject {
  id: string;
  name: string;
}

/**
 * Make a project-gated page (API Keys, Project Data, …) render against a known
 * project instead of the "Select a project" all-projects gate:
 *   1. seed the persisted projectStore selection in localStorage (init script,
 *      so it is present before the app boots), and
 *   2. mock GET /api/v1/projects so refreshProjects() validates that id and
 *      promotes it to the active project.
 *
 * Call before navigating. Returns the seeded project.
 */
export async function seedActiveProject(
  page: Page,
  project: SeedProject = {
    id: '00000000-0000-4000-8000-0000000000e2',
    name: 'E2E Project',
  },
): Promise<SeedProject> {
  await page.addInitScript((proj) => {
    localStorage.setItem(
      'testlookup-active-project',
      JSON.stringify({ state: { activeProjectId: proj.id }, version: 0 }),
    );
  }, project);

  await mockJson(page, '**/api/v1/projects', [
    {
      id: project.id,
      name: project.name,
      description: 'Seeded by e2e',
      is_active: true,
    },
  ]);

  return project;
}
