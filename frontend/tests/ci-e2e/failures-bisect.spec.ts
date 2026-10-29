/**
 * W23b FAIL-12 on `/failures` › a row's Suspects, hermetic (`docs/deferred-scope-2026-10-13/work/W23b-contract.md`
 * §2.1 F-1, §4, §6.3), a QA engineer, at 1280 × 900 and 1440 × 900:
 *
 *  - the narrowing block renders INSIDE the row's `complementary` section `Suspects: <test>`, above `SuspectsPanel`:
 *    `Last passed in build #236 (goodsen), first failed in build #239 (badsent).`, both builds linked to their runs
 *    (whole value);
 *  - opening the section asks exactly `…/suspects?fingerprint=…` and `…/bisect?fingerprint=…`, each once (nothing on
 *    page load);
 *  - `2 run(s) in between did not run this test.` expands to the two builds' links; the offline line verbatim;
 *  - a fetched world: `Narrowed to 3 commits (the run-level range since the last green run has 17)`, three ranked
 *    rows, the monorepo and the merge caveats;
 *  - a `timed_out` world: `Retry` asks `/bisect` a second time and the list arrives;
 *  - a `<script>` branch renders as text; a scripted 404 says `Could not load the narrowed range.` while
 *    `SuspectsPanel` below still renders;
 *  - the section never widens the table (the `in-page-tables` width rule); `#main-content` never scrolls sideways.
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). World: `fixtures-bisect-drift.ts` over `FAILURES_ON`.
 */
import { expect, test, type Locator, type Page } from '@playwright/test'
import { landmark } from '../lib/production-pages'
import { boxOf, expectMainFits, inPageLine, minContentSlack, tableBox } from '../lib/inPageGeometry'
import { assertHermetic, expectHostileAsText, networkQuiet, observed, openRollout, requestsTo } from '../lib/rollout'
import { RUN_ID, runs } from '../visual/production/fixtures'
import {
  bisectDriftWorld,
  WORLD_MONOREPO_CAVEAT,
  worldFetchedCompare,
  worldNarrowing,
  worldTimedOutCompare,
  type BisectWorldAnswer,
} from '../visual/production/fixtures-bisect-drift'

test.use({ timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

/** The top-failing row whose Suspects opens (`fixtures.ts` `TOP_FAILING`: fingerprint `fp-top-1`, last failed in RUN_ID). */
const ROW = 'checkout total includes tax'
const FP = 'fp-top-1'
const OFFLINE_LINE = 'Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).'
const LOAD_ERROR = 'Could not load the narrowed range.'
const BISECT = /^\/api\/v1\/runs\/[^/]+\/bisect$/

const ready = (p: Page) => landmark(p, 'Failure verdict')
const table = (p: Page) => p.locator('[data-primary]').getByRole('table', { name: 'Top failing tests' })
const section = (p: Page) => p.getByRole('complementary', { name: `Suspects: ${ROW}` })
const block = (p: Page) => section(p).getByRole('region', { name: 'Narrowed range' })

async function openFailures(page: Page, answer: BisectWorldAnswer | null) {
  const world = bisectDriftWorld({ role: 'QA_ENGINEER', answers: answer ? { [FP]: answer } : {} })
  const opened = await openRollout(page, '/failures', { handlers: world.handlers, ready, user: world.user })
  await networkQuiet(page, opened.api)
  return { ...opened, world }
}

/** Open the row's Suspects; returns the requests that opening made. */
async function openSuspects(page: Page, api: Parameters<typeof observed>[0]): Promise<string[]> {
  const before = observed(api).length
  await table(page).getByRole('button', { name: `Suspects for ${ROW}` }).click()
  await expect(section(page)).toBeVisible()
  await expect(block(page).locator('[data-bisect-state], [data-bisect-error]')).toBeVisible()
  await networkQuiet(page, api)
  return [...observed(api).slice(before)].sort()
}

async function expectSectionFits(page: Page, width: number, closedTable: number, what: string) {
  const open = await tableBox(table(page))
  expect(Math.abs(open.table - closedTable), `${what} @${width}: the table keeps its width`).toBeLessThanOrEqual(1)
  await expectMainFits(page, `${what} @${width}`)
  const [formBox, tableEdge] = [await boxOf(section(page)), await boxOf(table(page))]
  expect(formBox.right, `${what} @${width}: the section stays inside the table`).toBeLessThanOrEqual(tableEdge.right + 1)
  inPageLine(`/failures suspects-bisect ${what}`, width, { table: open.table, section: formBox.width, slack: await minContentSlack(section(page)) })
}

const stateLine = (scope: Locator) => scope.locator('[data-bisect-state]')

for (const [width, height] of [[1280, 900], [1440, 900]] as const) {
  test.describe(`at ${width} x ${height}`, () => {
    test.use({ viewport: { width, height } })

    test(`offline: the two builds, the runs in between, the offline line; two reads on open — ${width}`, async ({ page }) => {
      const { api, errors, world } = await openFailures(page, worldNarrowing())
      expect(requestsTo(api, BISECT), 'nothing is narrowed on page load').toEqual([])
      const closed = await tableBox(table(page))
      expect(await openSuspects(page, api)).toEqual([
        `GET /api/v1/runs/${RUN_ID}/bisect?fingerprint=${FP}`,
        `GET /api/v1/runs/${RUN_ID}/suspects?fingerprint=${FP}`,
      ])
      expect(world.seen).toEqual([FP])
      const b = block(page)
      await expect(b.getByRole('heading', { level: 3 })).toHaveText("Narrowed to this test's last pass")
      await expect(stateLine(b)).toHaveText('Last passed in build #236 (goodsen), first failed in build #239 (badsent).')
      const all = runs()
      await expect(stateLine(b).getByRole('link', { name: '#236', exact: true })).toHaveAttribute('href', `/runs/${all[4].id}`)
      await expect(stateLine(b).getByRole('link', { name: '#239', exact: true })).toHaveAttribute('href', `/runs/${all[1].id}`)
      // The runs in between: a native <details>, two build links once opened.
      const untested = b.locator('details[data-bisect-untested]')
      await expect(untested.locator('summary')).toHaveText('2 run(s) in between did not run this test.')
      await untested.locator('summary').click()
      await expect(untested.getByRole('link', { name: '#238', exact: true })).toHaveAttribute('href', `/runs/${all[2].id}`)
      await expect(untested.getByRole('link', { name: '#237', exact: true })).toHaveAttribute('href', `/runs/${all[3].id}`)
      await expect(untested.getByRole('link')).toHaveCount(2)
      await expect(b.locator('[data-bisect-compare-reason]')).toHaveText(OFFLINE_LINE)
      await expect(b.locator('[data-bisect-commits]')).toHaveCount(0)
      // The block sits above the run-level suspects, which still render.
      const panel = section(page).getByTestId('suspects-panel')
      await expect(panel).toBeVisible()
      expect(await b.evaluate((el, other) => Boolean(el.compareDocumentPosition(other as Node) & Node.DOCUMENT_POSITION_FOLLOWING), await panel.elementHandle())).toBe(true)
      await expectSectionFits(page, width, closed.table, 'offline')
      assertHermetic(api, errors)
    })

    test(`fetched: the narrowed commits ranked, the run-level comparison, both caveats — ${width}`, async ({ page }) => {
      const { api, errors } = await openFailures(page, worldNarrowing({ compare: worldFetchedCompare() }))
      const closed = await tableBox(table(page))
      await openSuspects(page, api)
      const b = block(page)
      await expect(b.locator('[data-bisect-narrowed-heading]')).toHaveText(
        'Narrowed to 3 commits (the run-level range since the last green run has 17)',
      )
      const rows = b.locator('[data-bisect-commit]')
      await expect(rows).toHaveCount(3)
      const ranked = [['cafesent', 'cafesentinel01'], ['fadesent', 'fadesentinel02'], ['beadsent', 'beadsentinel03']]
      for (const [i, [short, full]] of ranked.entries()) {
        await expect(rows.nth(i)).toContainText(`#${i + 1}`)
        const link = rows.nth(i).getByRole('link', { name: short, exact: true })
        await expect(link).toHaveAttribute('href', `https://github.example.com/acme/payments/commit/${full}`)
        await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
      }
      await expect(b.locator('[data-bisect-caveat]')).toHaveText(WORLD_MONOREPO_CAVEAT)
      await expect(b.locator('[data-bisect-merge-caveat]')).toHaveText(
        "Compared as the git host's goodsen...badsent (commits reachable from the failure and not from the pass): with merges or a rebase this is not a straight line.",
      )
      await expect(b.locator('[data-bisect-compare-reason]')).toHaveCount(0)
      await expectSectionFits(page, width, closed.table, 'fetched')
      assertHermetic(api, errors)
    })

    test(`timed out: Retry asks /bisect a second time and the list arrives — ${width}`, async ({ page }) => {
      const answer: BisectWorldAnswer = (asked) =>
        worldNarrowing({ compare: asked === 1 ? worldTimedOutCompare() : worldFetchedCompare() })
      const { api, errors, world } = await openFailures(page, answer)
      await openSuspects(page, api)
      const b = block(page)
      const reason = b.locator('[data-bisect-compare-reason]')
      await expect(reason).toContainText('The commit list did not arrive within 8 seconds.')
      await reason.getByRole('button', { name: 'Retry' }).click()
      await expect(b.locator('[data-bisect-narrowed-heading]')).toHaveText(/^Narrowed to 3 commits/)
      await networkQuiet(page, api)
      expect(requestsTo(api, BISECT)).toEqual([
        `GET /api/v1/runs/${RUN_ID}/bisect?fingerprint=${FP}`,
        `GET /api/v1/runs/${RUN_ID}/bisect?fingerprint=${FP}`,
      ])
      expect(world.seen).toEqual([FP, FP])
      assertHermetic(api, errors)
    })

    test(`a <script> branch renders as text — ${width}`, async ({ page }) => {
      const branch = '<script>window.__xss=1</script><img src="x" onerror="window.__xss=1">'
      const { api, errors } = await openFailures(page, worldNarrowing({ series_branch: branch }))
      await openSuspects(page, api)
      await expect(block(page).locator('[data-bisect-meta]')).toContainText(`2 failing runs on ${branch}`)
      await expectHostileAsText(page, section(page), 'the narrowing block')
      assertHermetic(api, errors)
    })

    test(`a scripted 404: "${LOAD_ERROR}" while SuspectsPanel below still renders — ${width}`, async ({ page }) => {
      const { api, errors, world } = await openFailures(page, null)
      await openSuspects(page, api)
      await expect(block(page).locator('[data-bisect-error]')).toHaveText(LOAD_ERROR)
      await expect(block(page).locator('[data-bisect-state]')).toHaveCount(0)
      const panel = section(page).getByTestId('suspects-panel')
      await expect(panel).toBeVisible()
      await expect(panel).toContainText('No commit range available for this failure yet')
      expect(world.seen).toEqual([FP])
      assertHermetic(api, errors)
    })
  })
}
