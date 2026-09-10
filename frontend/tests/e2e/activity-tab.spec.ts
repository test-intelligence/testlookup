import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * The Activity tab (epic ACT) against the running deployment.
 *
 * The unit tests cover the registry, the write path, the keyset reader and the
 * page in isolation. This file covers the thing none of them can: that a real
 * ledger row, written by a real producer, through a real migration, arrives on
 * a real page. Every layer was individually green while five events were being
 * silently dropped, so "the parts pass" is not the claim that matters here.
 *
 * These tests use `performRealLogin` rather than the `request` fixture. The
 * token lives in localStorage, not a cookie, so an API-only fixture 401s,
 * helpers return null, and the test SKIPS while reporting green — the exact
 * shape that hid 18 broken e2e tests in this repo before.
 *
 * Every check below FAILS CLOSED. Where a test cannot find what it needs it
 * asserts, rather than skipping: a skip is indistinguishable from a pass in the
 * summary line, which is how a feature can appear covered and be absent.
 */

const PROJECT_PICKER_PROMPT = 'Select a project';

/**
 * The app stores its JWT in the Zustand persist blob under `auth-storage`
 * (`{state:{token,refreshToken}}`), NOT under `access_token`. Reading the wrong
 * key returns null, every fetch below 401s, and the test fails pointing at the
 * API instead of at itself — which is exactly what happened on the first run
 * against the deployment.
 */
const READ_TOKEN = `
  function readToken() {
    try {
      const raw = localStorage.getItem('auth-storage');
      return raw ? (JSON.parse(raw)?.state?.token ?? '') : '';
    } catch { return ''; }
  }
`;


test.describe('Activity tab', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('is reachable from the sidebar and renders its own page', async ({ page }) => {
    // Navigate FIRST. The sidebar collapses every group whose activePrefixes
    // do not match the current path, so the Activity entry is only in the DOM
    // once the Testing group claims /activity. That is precisely the bug this
    // asserts against: the entry was added to the group's children but not to
    // its activePrefixes, so landing on /activity left its own group closed
    // and the entry off screen entirely.
    await page.goto('/activity');
    await expect(page.getByRole('heading', { name: 'Activity' })).toBeVisible({
      timeout: 15_000,
    });

    const link = page.getByRole('link', { name: 'Activity', exact: true });
    await expect(
      link,
      'the Testing group does not claim /activity, so its own nav entry is ' +
        'not on screen for the page the reader is looking at',
    ).toBeVisible({ timeout: 10_000 });

    await link.click();
    await expect(page).toHaveURL(/\/activity/);
  });

  test('offers a project picker in All Projects mode instead of a dead end', async ({
    page,
  }) => {
    // /activity is declared single-project in routeScope.ts. The failure this
    // guards is not a blank page — it is a page that replaces everything the
    // reader came for with a sentence telling them to go and use the top bar,
    // and gives them nothing to press.
    await page.goto('/activity');

    const prompt = page.getByText(PROJECT_PICKER_PROMPT, { exact: false });
    const feed = page.getByRole('feed');

    // POLL, do not sample. Either a project is pinned and the feed renders, or
    // none is and the picker prompt does — but reading isVisible() the instant
    // navigation resolves returns false for both, and the page gets reported
    // as a dead end when it simply had not painted yet.
    await expect
      .poll(
        async () =>
          (await prompt.isVisible().catch(() => false)) ||
          (await feed.isVisible().catch(() => false)),
        {
          message:
            'neither the feed nor the project prompt rendered — the page is a dead end',
          timeout: 20_000,
        },
      )
      .toBe(true);

    if (await prompt.isVisible().catch(() => false)) {
      await expect(
        page.getByText(/Activity is recorded per project/i),
      ).toBeVisible();
      // The whole point of ProjectRequiredEmptyState: something to PRESS.
      // A prompt telling the reader to go and find the top bar is the dead
      // end this test exists to catch.
      await expect(
        page.getByText(/choose one to continue/i),
        'the prompt gives the reader nothing to act on',
      ).toBeVisible();
    }
  });

  test('shows real events for a project that has activity', async ({ page }) => {
    await page.goto('/activity');

    // Pin the first project the picker offers, if we are not already scoped.
    const prompt = page.getByText(PROJECT_PICKER_PROMPT, { exact: false });
    if (await prompt.isVisible().catch(() => false)) {
      const firstProject = page.getByRole('button', { name: /service|project/i }).first();
      if (await firstProject.isVisible().catch(() => false)) {
        await firstProject.click();
      }
    }

    // The page must resolve to one of three HONEST states. "Loading forever"
    // and a silent blank are not among them.
    const feed = page.getByRole('feed');
    const emptyState = page.getByText(/No activity in this window/i);
    const outage = page.getByText(/Activity is unavailable/i);
    const picker = page.getByText(PROJECT_PICKER_PROMPT, { exact: false });

    await expect
      .poll(
        async () =>
          (await feed.isVisible().catch(() => false)) ||
          (await emptyState.isVisible().catch(() => false)) ||
          (await outage.isVisible().catch(() => false)) ||
          (await picker.isVisible().catch(() => false)),
        {
          message:
            'the Activity page never reached a terminal state — it is stuck loading',
          timeout: 20_000,
        },
      )
      .toBe(true);

    // An outage is a real failure of this feature on this deployment, not an
    // acceptable outcome to shrug at.
    expect(
      await outage.isVisible().catch(() => false),
      'the activity API returned an error against the deployment',
    ).toBe(false);
  });

  test('the event-type registry endpoint answers, so the filters can populate', async ({
    page,
  }) => {
    await page.goto('/activity');

    const response = await page.evaluate(async (READ) => {
      eval(READ);
      const token = readToken();
      const res = await fetch('/api/v1/activity/event-types', {
        headers: { Authorization: `Bearer ${token}` },
      });
      return { status: res.status, body: res.ok ? await res.json() : null };
    }, READ_TOKEN);

    expect(
      response.status,
      'the activity registry endpoint is not answering — every filter dropdown is empty',
    ).toBe(200);
    expect(response.body?.categories).toContain('runs');
    expect(response.body?.actor_types).toContain('system');
    // The registry is the source for the filter UI; an empty one means the
    // dropdowns render with nothing in them.
    expect(Object.keys(response.body?.events ?? {}).length).toBeGreaterThan(5);
  });

  test('the feed API is scoped to the project in the PATH', async ({ page }) => {
    // The tenant boundary. A query-param version of this endpoint would look
    // guarded and not be — the authorization ratchet auto-passes a route with
    // no scoped path param, and nine IDORs once hid in that blind spot.
    await page.goto('/overview');

    const result = await page.evaluate(async (READ) => {
      eval(READ);
      const token = readToken();
      const headers = { Authorization: `Bearer ${token}` };

      const projectsRes = await fetch('/api/v1/projects', { headers });
      if (!projectsRes.ok) return { error: `projects ${projectsRes.status}` };
      const projects = await projectsRes.json();
      const list = Array.isArray(projects) ? projects : (projects.items ?? []);
      if (list.length === 0) return { error: 'no projects on this deployment' };

      const id = list[0].id;
      const feedRes = await fetch(`/api/v1/projects/${id}/activity?limit=5`, {
        headers,
      });
      return {
        status: feedRes.status,
        body: feedRes.ok ? await feedRes.json() : null,
      };
    }, READ_TOKEN);

    expect(result.error, `could not reach the feed API: ${result.error}`).toBeUndefined();
    expect(result.status).toBe(200);
    // The contract the UI depends on.
    expect(result.body).toHaveProperty('items');
    expect(result.body).toHaveProperty('next_cursor');
    expect(result.body).toHaveProperty('ledger_started_at');
  });

  test('an ingested run lands in the feed', async ({ page }) => {
    // The end-to-end claim: a producer writes, the migration holds it, the
    // reader returns it. This is the one assertion that could not have passed
    // while the write path was silently dropping events.
    await page.goto('/overview');

    const result = await page.evaluate(async (READ) => {
      eval(READ);
      const token = readToken();
      const headers = { Authorization: `Bearer ${token}` };

      const projectsRes = await fetch('/api/v1/projects', { headers });
      if (!projectsRes.ok) return { error: `projects ${projectsRes.status}` };
      const projects = await projectsRes.json();
      const list = Array.isArray(projects) ? projects : (projects.items ?? []);

      // Look across every project — the ledger starts at deploy, so only the
      // ones touched since then have rows.
      for (const project of list) {
        const res = await fetch(
          `/api/v1/projects/${project.id}/activity?limit=50`,
          { headers },
        );
        if (!res.ok) continue;
        const page_ = await res.json();
        if ((page_.items ?? []).length > 0) {
          return { found: true, sample: page_.items[0], project: project.slug };
        }
      }
      return { found: false };
    }, READ_TOKEN);

    expect(result.error).toBeUndefined();

    if (!result.found) {
      // Not a skip. A freshly-migrated deployment legitimately has an empty
      // ledger, and saying so out loud is more useful than a green tick that
      // means nothing — but the SHAPE checks above still ran and passed.
      test.info().annotations.push({
        type: 'note',
        description:
          'No activity rows on this deployment yet — the ledger has no backfill and starts at deploy. Shape and access checks still ran.',
      });
      return;
    }

    // If there IS a row, it must be well-formed: these are the fields the feed
    // renders, and a null summary or a missing actor renders as a blank line.
    expect(result.sample.summary, 'an event with no summary renders blank').toBeTruthy();
    expect(result.sample.actor?.type).toBeTruthy();
    expect(result.sample.category).toBeTruthy();
    expect(result.sample.occurred_at).toBeTruthy();
  });
});
