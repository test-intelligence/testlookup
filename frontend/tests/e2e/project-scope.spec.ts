import { test, expect } from '@playwright/test';
import { performRealLogin } from './realLoginHelper';

/**
 * Project scope — links must not promise what their destination cannot show.
 *
 * Reported from the deployment: from `/overview` with no project chosen, the
 * "Flaky tests" KPI card offers **Open flaky coach**, and the destination shows
 * nothing.
 *
 * The literal report was wrong in a way that mattered. The page was never blank
 * — it rendered "Select a project", an icon and a sentence telling the reader
 * to go and use the top bar, on a screen that had just replaced everything they
 * came for. Two real defects sat underneath: a prompt with nothing on it to
 * press, and a link offered from a state where its destination categorically
 * could not work, on a card holding a CROSS-project count.
 *
 * The unit tests cover the registry, the picker and the link in isolation. This
 * file covers the journey, because every part of it was individually fine and
 * the bug lived in how they met: the default project selection is All Projects,
 * six pages refuse to render in exactly that state, and nothing connected the
 * two facts.
 *
 * These tests deliberately do NOT pin a project. All Projects is the app's
 * default for a fresh session, and it is the state the whole defect lives in —
 * a test that pinned a project first would be testing the state where nothing
 * was ever wrong.
 */

/** The prompt's title. Shared by all six project-gated pages. */
const PROMPT = 'Select a project';
/** The control the prompt exists to offer. */
const PICKER = 'Choose a project to continue';

test.describe('Project scope — the dead end that had no exit', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('the dashboard warns before the click that a project is needed', async ({
    page,
  }) => {
    // The link is NOT hidden — an admin can still follow it and resolve the
    // scope on the destination, and hiding it would remove the only path to
    // that data. What changed is that it no longer looks identical to the six
    // links beside it that work in this state.
    await page.goto('/overview');

    const link = page.getByRole('link', { name: /Open flaky coach/i });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', '/flaky-coach');

    await expect(
      page.getByText(/pick a project/i).first(),
      'the dashboard offers a link to a page that cannot render, with nothing to say so',
    ).toBeVisible();
  });

  test('following it lands on a prompt that can be acted on', async ({ page }) => {
    // The reported journey, end to end. Clicked, not typed into the URL bar —
    // the bug was reachable only by taking the link the dashboard offers.
    await page.goto('/overview');
    await page.getByRole('link', { name: /Open flaky coach/i }).click();

    await expect(page).toHaveURL(/\/flaky-coach/);
    await expect(page.getByText(PROMPT)).toBeVisible();

    await expect(
      page.getByLabel(PICKER),
      'the prompt names what is missing and offers nothing to fix it — the reader is sent to find the top bar',
    ).toBeVisible();
  });

  test('choosing a project from the prompt actually loads the page', async ({
    page,
  }) => {
    // The load-bearing one. A picker that renders but does not commit is the
    // same dead end with a decorative control on it.
    await page.goto('/flaky-coach');
    const picker = page.getByLabel(PICKER);
    await expect(picker).toBeVisible();

    // `:not([value=""])` — `[value!=""]` is not valid CSS and Playwright
    // rejects it outright rather than matching nothing.
    const value = await picker
      .locator('option:not([value=""])')
      .first()
      .getAttribute('value');
    if (!value) throw new Error('the prompt offers no project to choose');

    await picker.selectOption(value);

    // The prompt is gone and the page is rendering its own content.
    await expect(page.getByLabel(PICKER)).toBeHidden();
    await expect(page.getByText(PROMPT)).toBeHidden();
  });

  test('the same prompt serves a settings page, not just flaky coach', async ({
    page,
  }) => {
    // Six pages had six copies of this dead end. If the shared component only
    // reached one of them, the other five still tell the reader to go and find
    // the top bar — and the registry that links consult would be describing a
    // fix that mostly did not happen.
    await page.goto('/settings/retention');

    await expect(page.getByText(PROMPT)).toBeVisible();
    await expect(page.getByLabel(PICKER)).toBeVisible();
  });

  test('a page that works across projects is left completely alone', async ({
    page,
  }) => {
    // The control, and the reason the registry lists routes instead of
    // defaulting everything to restricted. Coverage supports All Projects; if
    // it started showing this prompt, the fix would have broken more than it
    // repaired — and every test above would still pass.
    await page.goto('/coverage');

    await expect(page.getByLabel(PICKER)).toBeHidden();
    await expect(
      page.getByText('Select a project', { exact: true }),
      'a page that works across projects is now demanding one',
    ).toBeHidden();
  });
});

/**
 * A filter must not change what the app BELIEVES about the project.
 *
 * Reported from the deployment on `/overview?release=unattributed`: a project
 * with runs, none of them in that release bucket, was greeted with "Welcome to
 * TestLookup — No test runs here yet" and the entire setup wizard, telling an
 * established team to run `make quickstart`.
 *
 * Same family as the tests above and the opposite direction: there, a link
 * promised a destination that could not answer; here, an empty answer was
 * mistaken for an empty project.
 */
test.describe('Project scope — an empty filter is not an empty project', () => {
  test.beforeEach(async ({ page }) => {
    await performRealLogin(page);
  });

  test('the setup wizard stays away from a project that has runs', async ({
    page,
    request,
  }) => {
    const token = await page.evaluate(() => {
      try {
        return JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.token ?? '';
      } catch {
        return '';
      }
    });
    expect(token, 'no bearer token after login — the harness is broken').toBeTruthy();

    // Find a project that genuinely HAS runs. Asserting rather than skipping:
    // "no project has any runs" on a seeded deployment means the lookup is
    // wrong, and a test that skipped there would report success for having
    // checked nothing.
    const res = await request.get('/api/v1/runs?page=1&size=1', {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });
    expect(res.ok(), `runs lookup failed with ${res.status()}`).toBeTruthy();
    const runs = (await res.json()).items ?? [];
    expect(runs.length, 'no runs anywhere on this deployment').toBeGreaterThan(0);

    const projectId: string = runs[0].project_id;
    await page.addInitScript((id) => {
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({ state: { activeProjectId: id, activeProject: null }, version: 0 }),
      );
    }, projectId);

    await page.goto('/overview?release=unattributed');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveURL(/release=unattributed/);
    await expect(
      page.getByText(/Welcome to TestLookup/i),
      'a project with runs was shown the first-run setup wizard because one release filter came back empty',
    ).toBeHidden();
  });
});

/**
 * A surface that IGNORES the filter has to say so.
 *
 * The mirror image of the tests above. There, a link promised a destination
 * that could not answer under the current scope. Here, a page answers happily
 * and the answer is not scoped at all — search spans every release by design,
 * because scoping it would return nothing for a test that exists but last ran
 * elsewhere, which reads as "that test does not exist".
 *
 * The backend has declared this in its payload for a while, and its own comment
 * claimed "the UI renders this as the all-releases badge". The UI did not.
 */
test.describe('Project scope — search says that it spans every release', () => {
  test('the all-releases badge appears once a release is selected', async ({
    page,
    request,
  }) => {
    await performRealLogin(page);

    const token = await page.evaluate(() => {
      try {
        return JSON.parse(localStorage.getItem('auth-storage') || '{}')?.state?.token ?? '';
      } catch {
        return '';
      }
    });
    expect(token, 'no bearer token after login — the harness is broken').toBeTruthy();

    // A project that actually has releases to choose from; the badge is
    // deliberately invisible until one is selected.
    const res = await request.get('/api/v1/runs?page=1&size=1', {
      headers: { Authorization: `Bearer ${token}` },
      failOnStatusCode: false,
    });
    expect(res.ok(), `runs lookup failed with ${res.status()}`).toBeTruthy();
    const runs = (await res.json()).items ?? [];
    expect(runs.length, 'no runs anywhere on this deployment').toBeGreaterThan(0);

    await page.addInitScript((id) => {
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({ state: { activeProjectId: id, activeProject: null }, version: 0 }),
      );
    }, runs[0].project_id as string);

    // An empty query browses the most recent items, so there are results to
    // label without depending on any particular test name existing.
    await page.goto('/search?q=');

    const picker = page.getByLabel('Filter by release');
    await expect(picker).toBeEnabled();
    const value = await picker.locator('option').nth(1).getAttribute('value');
    if (!value) throw new Error('the picker offers no release for a pinned project');

    // Located by the badge's own tooltip, NOT by its visible text: the release
    // picker's default option is also the string "All releases", so
    // getByText matches both and Playwright rejects the ambiguity. The title
    // is what only the badge has.
    const badge = page.locator('[title^="Not filtered by the selected release"]');

    // Absent before the selection: with no release chosen there is no
    // discrepancy to explain, and the page looks as it did before the release
    // axis existed.
    await expect(badge).toHaveCount(0);

    await picker.selectOption(value);

    await expect(
      badge,
      'search results are shown under a release filter that does not apply to them, with nothing saying so',
    ).toBeVisible();

    // And it carries the backend's own sentence, rather than a second copy
    // written in the UI that drifts from it.
    await expect(badge).toHaveAttribute(
      'title',
      /Search spans every release in the project by design/,
    );
  });
});
