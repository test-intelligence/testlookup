import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';
import { mockJson } from './apiMock';

/**
 * Release as a dimension — the surfaces the epic actually shipped.
 *
 * `release-gate-policy.spec.ts` covers the OLDER per-run policy application.
 * This file covers the release axis itself: the global picker, the filter
 * reaching the pages it claims to filter, the Unattributed bucket, and the
 * endpoints wired late in the epic (external sync, phase gate).
 *
 * Written to run against a LIVE backend — that is the point. Every defect this
 * epic's confirmation gates found was invisible to unit tests: two 500s and two
 * silent scope mixes, all under passing source-assertion tests that never
 * called the code. A release filter that reaches the API and comes back 200
 * with a coherent payload is the property those tests could not express.
 *
 * Mocks appear only where a live deployment cannot supply the precondition
 * deterministically (a configured GitHub/Jira integration, a release with a
 * recorded verdict). Where the live system CAN answer, it is asked.
 */

const RELEASE_A = '9f8e7d6c-5b4a-4938-8271-0a1b2c3d4e5f';

function release(overrides: Record<string, unknown> = {}) {
  return {
    id: RELEASE_A,
    name: '2.4.0',
    version: '2.4.0',
    status: 'planning',
    is_active: true,
    is_auto_named: false,
    sort_key: '00002.00004.00000.00000',
    ...overrides,
  };
}


/** The active project, seeded the way the app persists it.
 *
 * `useReleaseScope` applies a release ONLY when a single project is pinned, so
 * a test that skips this is testing the enforcement, not the filter. Seeded
 * via `addInitScript` so it is in place before the store hydrates, rather than
 * racing the first render.
 */
async function pinProject(page: import('@playwright/test').Page, projectId: string) {
  await page.addInitScript((id) => {
    localStorage.setItem(
      'testlookup-active-project',
      JSON.stringify({ state: { activeProjectId: id, activeProject: null }, version: 0 }),
    );
  }, projectId);
}

/** The logged-in page's bearer token.
 *
 * Playwright's bare `request` fixture is a SEPARATE context and carries no
 * auth: this app keeps its token in `localStorage.auth-storage`, not a cookie,
 * so `request.get(...)` returns 401. The first version of this file used it
 * anyway, every lookup returned null, and two tests skipped — passing while
 * proving nothing. An e2e skip has to fail closed.
 */
async function bearer(page: import('@playwright/test').Page): Promise<string> {
  const token = await page.evaluate(() => {
    try {
      return JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.token ?? '';
    } catch {
      return '';
    }
  });
  expect(token, 'no bearer token after login — the harness is broken, not the app').toBeTruthy();
  return token;
}

/** First project on the deployment — the suite must not hardcode fixture ids.
 *
 * Fails rather than returns null: "there are no projects" on a seeded
 * deployment means the lookup is wrong, and a test that skips on it would
 * report success for having checked nothing.
 */
async function firstProjectId(
  page: import('@playwright/test').Page,
  request: import('@playwright/test').APIRequestContext,
): Promise<string> {
  const res = await request.get('/api/v1/projects?limit=1', {
    headers: { Authorization: `Bearer ${await bearer(page)}` },
  });
  expect(res.ok(), `projects lookup failed with ${res.status()}`).toBeTruthy();
  const body = await res.json();
  const rows = Array.isArray(body) ? body : body.items ?? [];
  expect(rows.length, 'deployment has no projects to scope a release to').toBeGreaterThan(0);
  return rows[0].id as string;
}

test.describe('Release axis — the global filter', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('the release picker is present and names what it filters', async ({ page }) => {
    // The picker is the epic's entry point. Its accessible name is the
    // contract: "Filter by release" tells a screen-reader user what the
    // control scopes, which a bare combobox does not.
    await page.goto('/overview');
    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeVisible();
  });

  test('the picker refuses to filter when no single project is pinned', async ({ page }) => {
    // Discovered against the live deployment, and it is the picker's most
    // important property: `useReleaseScope` applies a release only when one
    // project is pinned, because a release belongs to exactly one project. In
    // All-Projects scope the control DISABLES itself and reads "All releases"
    // rather than offering a filter it would not honour.
    //
    // A picker that looked active and quietly filtered nothing is the "inert
    // filter" this epic kept producing.
    await page.goto('/overview');
    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeDisabled();
    await expect(picker).toHaveValue('');
  });

  test('selecting a release puts it in the URL, so the view is shareable', async ({
    page,
    request,
  }) => {
    await performRealLogin(page);
    await pinProject(page, await firstProjectId(page, request));
    await page.goto('/overview');

    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeEnabled();

    // Whatever this deployment actually has — the suite must not depend on a
    // fixture release existing.
    const value = await picker.locator('option').nth(1).getAttribute('value');
    if (!value) throw new Error('the picker offers no release for a pinned project');
    await picker.selectOption(value);
    await expect(page).toHaveURL(/release=/);
  });

  test('the Unattributed bucket is offered and survives a reload', async ({ page }) => {
    // Runs that no release claims are a real state, not an empty one. The
    // sentinel travels as the literal string `unattributed` rather than a
    // UUID — which is exactly what made /analytics/flaky-scores 500 until the
    // gate found it.
    await mockJson(page, '**/api/v1/releases**', [release()]);
    await page.goto('/overview?release=unattributed');

    await expect(page).toHaveURL(/release=unattributed/);
    // The page must render, not error. A 500 here is the defect class this
    // epic's implementation gate found twice.
    await expect(page.locator('body')).not.toContainText(/Internal Server Error/i);
  });
});

test.describe('Release axis — the filter reaches the data', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('a release-scoped request is actually sent for the analytics pages', async ({
    page,
    request,
  }) => {
    // NFR1 in reverse: omitting the release must change nothing, but SELECTING
    // one must reach the API. A picker that sets a URL param the services
    // never forward is the "inert filter" this epic kept finding.
    //
    // Pinning the project is a REQUIRED precondition, not setup noise: without
    // it the release is deliberately not applied and this test would report a
    // missing filter that the app is correctly declining to use.
    await performRealLogin(page);
    await pinProject(page, await firstProjectId(page, request));

    await page.goto('/coverage');
    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeEnabled();
    const value = await picker.locator('option').nth(1).getAttribute('value');
    if (!value) throw new Error('the picker offers no release for a pinned project');

    // Wait for the REQUEST, not for the network to fall quiet.
    //
    // This counted matching requests seen before `networkidle` and then
    // asserted the count. That is a race: SWR can revalidate just after the
    // network settles, and the assertion had already run. It failed once in
    // webkit inside the full 63-test run and passed 3/3 in isolation — the
    // signature of waiting for the wrong thing, not of a broken filter.
    //
    // Armed BEFORE the selection so a fast response cannot land first.
    const scopedRequest = page
      .waitForRequest(
        req =>
          req.url().includes('/api/v1/analytics/') &&
          req.url().includes('release_id='),
        { timeout: 15_000 },
      )
      .catch(() => null);

    await picker.selectOption(value);

    expect(
      await scopedRequest,
      'no analytics request carried release_id — the picker is not connected to the data',
    ).toBeTruthy();
  });

  test('omitting the release sends no release_id at all', async ({ page }) => {
    // The other half of NFR1, and the reason the fragment is conditional
    // rather than a null-tolerant predicate: a bind that is always present
    // costs the index for every user who never asked for the feature.
    const leaked: string[] = [];
    await page.route('**/api/v1/analytics/**', async (route) => {
      const url = route.request().url();
      if (url.includes('release_id=')) leaked.push(url);
      await route.continue();
    });

    await page.goto('/coverage');
    await page.waitForLoadState('networkidle');

    expect(leaked, 'release_id was sent when no release was selected').toEqual([]);
  });

  test('a suite with no runs in the release renders an empty state, not a 500', async ({ page }) => {
    // The exact defect the implementation gate found: `suite_detail`'s
    // run-level fallback interpolated a release fragment with nothing bound,
    // so picking a release and opening a suite that only ran in another one
    // was a StatementError. Live, because the unit tests for that path never
    // executed it.
    const failures: number[] = [];
    page.on('response', (r) => {
      if (r.url().includes('/analytics/suite-detail') && r.status() >= 500) {
        failures.push(r.status());
      }
    });

    await page.goto(`/coverage/suite?suite=nonexistent-suite&release=${RELEASE_A}`);
    await page.waitForLoadState('networkidle');

    expect(failures, 'suite detail 500d under a release filter').toEqual([]);
  });
});

test.describe('Release axis — endpoints wired late in the epic', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('external sync refuses cleanly while offline, and says why', async ({
    page,
    request,
  }) => {
    // AI_OFFLINE_MODE defaults to True, so on a stock deployment this SHOULD
    // refuse — and the refusal is the assertion. A 503 naming offline mode is
    // correct behaviour; a 404 would mean the route never shipped, and a 500
    // would mean the gate raised instead of answering.
    const res = await request.post('/api/v1/releases/sync', {
      headers: { Authorization: `Bearer ${await bearer(page)}` },
      data: { project_id: await firstProjectId(page, request), source: 'github' },
      failOnStatusCode: false,
    });

    expect(
      res.status(),
      'POST /releases/sync is missing — sync_milestones has no caller again',
    ).not.toBe(404);
    // Authenticated, so 401 is no longer an acceptable pass. 403 stays allowed
    // for a role the seed user may not hold.
    expect([403, 503]).toContain(res.status());

    if (res.status() === 503) {
      // The refusal must name WHICH gate stopped it. That is the whole point
      // of keeping four distinct messages instead of delegating to
      // `availability_reason`, which collapses "no domain" and "no credential"
      // into one `not_configured` — an operator who cannot tell which half is
      // missing re-checks credentials that were never the issue.
      //
      // Not asserted as "offline" specifically: this deployment runs Ollama
      // locally, so AI_OFFLINE_MODE is False and the offline gate correctly
      // passes, leaving the integration gate to refuse. Pinning one message
      // here would test the deployment's config rather than the gate.
      const detail = (await res.text()).toLowerCase();
      expect(
        ['offline', 'integration', 'domain', 'credential'].some((r) => detail.includes(r)),
        `503 did not name which gate refused: ${detail}`,
      ).toBeTruthy();
    }
  });

  test('the phase gate answers for a release rather than 404ing', async ({
    page,
    request,
  }) => {
    // `release_phase_gate_service` had no router at all until W4. A 404 here
    // means it is unreachable again; 401/403 is fine (auth), and 200 with a
    // four-valued status is the shape the gate promises.
    const res = await request.get(`/api/v1/releases/${RELEASE_A}/phases/gate`, {
      headers: { Authorization: `Bearer ${await bearer(page)}` },
      failOnStatusCode: false,
    });

    expect(
      res.status(),
      'the phase gate has no route — the service is unreachable again',
    ).not.toBe(404);

    if (res.ok()) {
      const body = await res.json();
      expect(['BLOCKED', 'INCOMPLETE', 'READY', 'NO_PHASES']).toContain(body.status);
    }
  });

  test('a saved view reports whether its release applies to this reader', async ({
    page,
    request,
  }) => {
    // `saved_view_release` shipped complete with nothing importing it. The
    // response carrying a `release` verdict is what proves a request reaches
    // it — and the field is additive, so its ABSENCE on views without a
    // release is also correct.
    const res = await request.get('/api/v1/saved-views', {
      headers: { Authorization: `Bearer ${await bearer(page)}` },
      failOnStatusCode: false,
    });
    expect(res.ok(), `saved-views returned ${res.status()}`).toBeTruthy();

    const views = await res.json();
    if (!Array.isArray(views) || views.length === 0) return;

    for (const view of views) {
      if ('release' in view && view.release) {
        expect(typeof view.release.applied).toBe('boolean');
        // `applied: false` must always carry a reason, or the UI cannot tell
        // the reader why their result set is wider than the author intended.
        if (view.release.applied === false && view.release.release_id) {
          expect(view.release.reason).toBeTruthy();
        }
      }
    }
  });
});

test.describe('Release axis — the surfaces added after the first e2e pass', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('the summary report carries the release to the API', async ({ page, request }) => {
    // The reported bug: /reports/summary?release=... changed nothing, because
    // neither the router nor the service accepted a release at all. Asserted on
    // the WIRE rather than on rendered numbers: this deployment's data is not
    // fixed, so "the totals changed" is not a stable assertion, but "the
    // request carried the filter" is exactly the property that was missing.
    const projectId = await firstProjectId(page, request);
    await pinProject(page, projectId);

    const scoped: string[] = [];
    await page.route('**/api/v1/reports/summary**', async (route) => {
      if (route.request().url().includes('release_id=')) scoped.push(route.request().url());
      await route.continue();
    });

    await page.goto('/reports/summary');
    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeEnabled();
    const value = await picker.locator('option').nth(1).getAttribute('value');
    if (!value) throw new Error('the picker offers no release for a pinned project');

    await picker.selectOption(value);
    await page.waitForLoadState('networkidle');

    expect(
      scoped.length,
      'the summary report was requested project-wide under a release filter',
    ).toBeGreaterThan(0);
  });

  test('the release survives an in-app navigation', async ({ page, request }) => {
    // Bug #1, end to end. In-app links do not carry `?release=`, and the picker
    // used to read that absence as "clear the filter" — so the release vanished
    // between pages. The unit test covers the mechanism; this covers the actual
    // journey a user takes.
    await pinProject(page, await firstProjectId(page, request));
    await page.goto('/live');

    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeEnabled();
    const value = await picker.locator('option').nth(1).getAttribute('value');
    if (!value) throw new Error('no release to select on this project');
    await picker.selectOption(value);
    await expect(page).toHaveURL(/release=/);

    // Navigate the way a user does, not by editing the URL.
    await page.getByRole('link', { name: /coverage/i }).first().click();
    await page.waitForLoadState('networkidle');

    await expect(
      page.getByLabel('Filter by release'),
      'the release was dropped by moving between pages',
    ).toHaveValue(value);
  });

  test('a QA lead can set the active release', async ({ page, request }) => {
    // `activate_release` shipped complete with no route for the whole life of
    // the epic. A 404 here means it is unreachable again.
    const token = await bearer(page);
    const projectId = await firstProjectId(page, request);

    const list = await request.get(`/api/v1/releases?project_id=${projectId}`, {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });
    expect(list.ok(), `releases lookup failed with ${list.status()}`).toBeTruthy();
    const body = await list.json();
    const releases = Array.isArray(body) ? body : body.items ?? [];
    expect(releases.length, 'no releases on this project to activate').toBeGreaterThan(0);

    // A finished release is refused by design, so choose one that is not.
    // Picking releases[0] blindly would turn a legitimate 409 into a red test
    // and, worse, let a broken endpoint pass by looking like that refusal.
    const TERMINAL = ['released', 'cancelled', 'archived'];
    const target = releases.find((r: { status: string }) => !TERMINAL.includes(r.status));
    expect(
      target,
      `every release on this project is terminal (${releases
        .map((r: { status: string }) => r.status)
        .join(', ')}) — nothing here can be activated`,
    ).toBeTruthy();

    const res = await request.post(`/api/v1/releases/${target.id}/activate`, {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });

    // The e2e user is ADMIN, which outranks QA_LEAD and bypasses project
    // access — so 403 is not a legitimate outcome here, and accepting one
    // would let the guard be wrong in the direction that locks users out.
    expect(
      res.status(),
      `activate returned ${res.status()}: ${await res.text()}`,
    ).toBe(200);

    const out = await res.json();
    expect(out.is_active).toBe(true);
    expect(out).toHaveProperty('changed');

    // Idempotent: the same call again must not error. A double-click, or two
    // people on the same stale page, is not a conflict.
    const again = await request.post(`/api/v1/releases/${target.id}/activate`, {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });
    expect(again.status()).toBe(200);
    expect((await again.json()).changed).toBe(false);
  });

  test('attribution rules can be listed, previewed and created', async ({ page, request }) => {
    // Ladder rung 3 could never fire because nothing could create a rule. The
    // preview is exercised too: it is the safeguard against the documented
    // failure mode, a rule that matches everything.
    const token = await bearer(page);
    const projectId = await firstProjectId(page, request);
    const base = `/api/v1/projects/${projectId}/attribution-rules`;
    const auth = { Authorization: `Bearer ${token}` };

    const listed = await request.get(base, { headers: auth, failOnStatusCode: false });
    expect(
      listed.status(),
      `listing attribution rules returned ${listed.status()}: ${await listed.text()}`,
    ).toBe(200);

    const preview = await request.post(`${base}/preview`, {
      headers: auth,
      data: {
        name: 'e2e probe',
        match_field: 'branch',
        match_pattern: 'no-branch-matches-this-*',
        target_release_name: 'e2e-probe-release',
        priority: 9999,
        is_enabled: false,
      },
      failOnStatusCode: false,
    });
    expect(
      preview.status(),
      `the rule preview returned ${preview.status()}: ${await preview.text()}`,
    ).toBe(200);

    const out = await preview.json();
    expect(out).toHaveProperty('considered');
    expect(out).toHaveProperty('matched');
    expect(out).toHaveProperty('field_present');
    expect(out.matched).toBeLessThanOrEqual(out.considered);
    expect(
      out.matched,
      'a pattern no branch can match reported matches — the preview is not evaluating',
    ).toBe(0);

    // A preview must not persist the rule it was asked to try.
    const after = await request.get(base, { headers: auth });
    const body = await after.json();
    const rules = Array.isArray(body) ? body : body.items ?? [];
    expect(
      rules.some((r: { name: string }) => r.name === 'e2e probe'),
      'the preview saved the rule it was only asked to evaluate',
    ).toBe(false);
  });

  test('a match_field the evaluator cannot read is refused', async ({ page, request }) => {
    // The column is String(30), so the database accepts anything. A typo would
    // store cleanly and never match — a rule sitting in the list looking
    // configured while the ladder falls straight past it.
    const token = await bearer(page);
    const projectId = await firstProjectId(page, request);

    const res = await request.post(`/api/v1/projects/${projectId}/attribution-rules`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        name: 'bad field',
        match_field: 'Branch',
        match_pattern: '*',
        target_release_name: 'x',
      },
      failOnStatusCode: false,
    });

    expect(res.status(), 'a bad match_field was accepted').not.toBe(201);
    expect(
      res.status(),
      `expected 422 for an unreadable match_field, got ${res.status()}`,
    ).toBe(422);
    expect(await res.text()).toContain('match_field');
  });
});
