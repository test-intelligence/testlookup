import { test, expect } from '@playwright/test';

/**
 * POST /ws/events/{run_id} must refuse an unauthenticated or untenanted write.
 *
 * Re-audit finding H1. This endpoint was gated by one deployment-wide webhook
 * secret that authenticates a caller but names no project, and the handler took
 * `project_id` straight from the request body. Anyone holding that secret could
 * open a run against any project and stream fabricated test results into that
 * tenant's live dashboard and release-risk signals — and the secret was rated
 * only WARNING, so production booted with the literal default committed to this
 * repository.
 *
 * This runs against a LIVE deployment on purpose, and it is the only level at
 * which the property is real. The unit tests call the handler directly, so they
 * cannot see whether the route is mounted where the ingress sends traffic, nor
 * whether the deployment's own configuration re-opens the hole. Two of the
 * batch's fixes were wrong in exactly that gap: correct handler code, wrong
 * shipped configuration.
 *
 * Status codes are the assertion, and the distinction that matters is:
 *
 *    404  the route is not mounted at all — a deploy that did not land, or an
 *         ingress path that never reaches the backend. NOT a pass.
 *    401  mounted, resolved, and the credential check ran and refused.
 *    403  a credential was supplied and rejected on tenancy grounds.
 *
 * A 404 passing for "rejected" is the failure mode this file exists to rule
 * out: it reads as security working while the endpoint may be missing entirely.
 *
 * Playwright's `request` fixture carries no authentication, which is normally a
 * trap here — for these cases it is exactly what is wanted.
 */

const RUN_ID = `e2e-authz-probe-${Date.now()}`;

/** A project this caller has no claim to. Nothing should ever be written to it. */
const VICTIM_PROJECT = '00000000-0000-4000-8000-00000000dead';

const RUN_START = {
  type: 'run_start',
  project_id: VICTIM_PROJECT,
  build_number: 'e2e-probe',
  total_tests: 1,
};

const API_BASE = process.env.VITE_API_BASE_URL || process.env.PLAYWRIGHT_BASE_URL || '';

function eventUrl(runId: string): string {
  return `${API_BASE}/ws/events/${runId}`;
}

test.describe('live event ingestion — tenant authorization', () => {
  test('the route is mounted, so a refusal means the guard ran', async ({ request }) => {
    const res = await request.post(eventUrl(RUN_ID), { data: RUN_START });

    expect(
      res.status(),
      'POST /ws/events/{run_id} returned 404. The route is not reachable at ' +
        'this path, so every "rejected" assertion below would pass for the ' +
        'wrong reason. Check the deploy landed and the ingress routes /ws/.',
    ).not.toBe(404);
  });

  test('an unauthenticated caller cannot open a run', async ({ request }) => {
    const res = await request.post(eventUrl(RUN_ID), { data: RUN_START });

    expect(
      res.status(),
      'the endpoint accepted a run_start with no credential at all',
    ).toBe(401);
  });

  test('an unauthenticated caller cannot stream results into a run', async ({ request }) => {
    const res = await request.post(eventUrl(RUN_ID), {
      data: { type: 'test_result', test_name: 'probe', status: 'FAILED' },
    });

    expect(res.status(), 'fabricated results were accepted unauthenticated').toBe(401);
  });

  test('an unauthenticated caller cannot finalise someone else’s run', async ({ request }) => {
    // run_complete is the one that writes through to Postgres and closes the
    // run, so it is the most damaging of the three event types to leave open.
    const res = await request.post(eventUrl(RUN_ID), { data: { type: 'run_complete' } });

    expect(res.status(), 'a run could be finalised unauthenticated').toBe(401);
  });

  test('a wrong shared secret is refused', async ({ request }) => {
    const res = await request.post(eventUrl(RUN_ID), {
      data: RUN_START,
      headers: { 'X-Webhook-Secret': 'not-the-secret-either-way' },
    });

    expect(
      [401, 403],
      `a bad webhook secret returned ${res.status()}`,
    ).toContain(res.status());
  });

  test('a wrong API key is refused', async ({ request }) => {
    const res = await request.post(eventUrl(RUN_ID), {
      data: RUN_START,
      headers: { 'X-API-Key': 'qai_not_a_real_key_at_all' },
    });

    expect([401, 403], `a bad API key returned ${res.status()}`).toContain(res.status());
  });

  test('the default configuration does not accept the shared secret alone', async ({ request }) => {
    // LIVE_EVENTS_REQUIRE_PROJECT_KEY defaults on: the shared secret names no
    // tenant, so it cannot be the sole credential for a write that does.
    //
    // A deployment may deliberately re-enable the legacy path, and this test
    // reports that rather than failing it — the fixed property is that a
    // SUCCESSFUL write can never be one the body's project_id chose. A 202 here
    // would mean the victim project named above was written to.
    const res = await request.post(eventUrl(RUN_ID), {
      data: RUN_START,
      headers: { 'X-Webhook-Secret': process.env.E2E_WEBHOOK_SECRET || 'unknown-secret' },
    });

    expect(
      res.status(),
      'a caller holding only the shared webhook secret opened a run against a ' +
        'project it named itself — the cross-tenant write H1 reported',
    ).not.toBe(202);
  });
});
