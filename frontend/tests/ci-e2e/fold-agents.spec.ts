/**
 * UX redesign P5 item 7 (`03-implementation-plan.md` § P5, owner decision
 * D3), on `/agents`, hermetic: "Pipeline runs" under Admin › AI, inside the
 * settings layout.
 *
 * Held here, in a browser at 1440 x 900:
 *  - the template: one compact header titled "Pipeline runs", ONE run picker
 *    (there were two selects over the same runs: BUG-011), the AI report as
 *    the one `[data-primary]`, its top within the 300 px fold budget below
 *    the top of `#main-content`, and full width (it was a 2/3 column beside
 *    the pipeline cards: 587 of 892 px measured on the unchanged page);
 *  - the agent stages collapsed: the 600 px compute canvas is not drawn until
 *    "Agent stages" is opened;
 *  - `/agents?run=<id>` (UX redesign P4) selects that run and opens its
 *    pipeline; a pipeline picked from the recent list opens ITS run (picker
 *    and URL follow);
 *  - the trigger queues a pipeline for the run in the picker (the POST body
 *    names it).
 *
 * The FOLD line prints the scroller's `scrollHeight` and the report's top
 * and width. Before (unchanged page, same fixtures): scrollHeight 1231 px,
 * the "AI Report" heading at 117 px, 587 px wide, two run selects
 * (`docs/viz-work/p5-agent-D.md`).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock); the fixtures
 * are the Run page's (`fixtures-run.ts`: the run's pipeline, its stages,
 * timeline and AI report), plus the live-runs list this page also asks for.
 */
import { expect, test, type Page } from '@playwright/test'
import type { ApiHandlers } from '../lib/production-pages'
import { MAIN, networkQuiet, openRollout } from '../lib/rollout'
import { RUN_ID } from '../visual/production/fixtures'
import { AI_REPORT_SUMMARY, RUN, RUN_PAGE } from '../visual/production/fixtures-run'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

/** Every POST to the trigger, by body. */
const triggered: unknown[] = []

const AGENTS: ApiHandlers = [
  ...RUN_PAGE,
  ['/api/v1/agents/active-runs', () => ({ active_runs: [] })],
  [
    '/api/v1/agents/pipelines/trigger',
    ({ route }) => {
      triggered.push(route.request().postDataJSON())
      return { message: 'queued', task_id: 'task-1', run_id: RUN_ID }
    },
    'POST',
  ],
]

const report = (p: Page) => p.getByRole('region', { name: 'AI report' })
const ready = (p: Page) => report(p).getByText(AI_REPORT_SUMMARY)

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

test.beforeEach(() => {
  triggered.length = 0
})

/** The scroller's height, the primary content's top (px below the scroller's top) and widths. */
async function foldGeometry(page: Page) {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    const pageRoot = document.querySelector('[data-page-header]')?.parentElement as HTMLElement
    return {
      scrollHeight: main.scrollHeight,
      primaries: primaries.length,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaryWidth: primary ? Math.round(primary.getBoundingClientRect().width) : null,
      pageWidth: Math.round(pageRoot.getBoundingClientRect().width),
      pageHeight: Math.round(pageRoot.getBoundingClientRect().height),
      pageSelects: pageRoot.querySelectorAll('select').length,
    }
  }, MAIN)
}

test('a run on screen: one picker, the AI report full width within the fold, the stages collapsed', async ({ page }) => {
  const { api, errors } = await openRollout(page, `/agents?run=${RUN_ID}`, { handlers: AGENTS, ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /agents?run= ${JSON.stringify(geometry)}`)

  await expect(page.locator('[data-page-header]')).toHaveCount(1)
  await expect(page.getByRole('heading', { level: 1, name: 'Pipeline runs' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Help: Pipeline runs' })).toHaveAttribute('data-help-topic', 'ai-agents')

  // The template: one primary content, inside the fold, as wide as the page.
  expect(geometry.primaries, 'one primary content element').toBe(1)
  await expect(page.locator('[data-primary]')).toHaveAttribute('aria-label', 'AI report')
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  expect(geometry.primaryWidth, 'the report spans the page, not a column of it').toBe(geometry.pageWidth)

  // ONE run picker, showing the run on screen, and the run's pipeline selected.
  expect(geometry.pageSelects).toBe(1)
  const picker = page.getByLabel('Test suite & build')
  await expect(picker).toHaveValue(RUN_ID)
  await expect(picker.locator('option:checked')).toHaveText(`Auth · Run #${RUN.run_seq}`)
  const row = page.getByRole('region', { name: 'Pipelines' }).getByRole('button', { name: /offline pipeline/i })
  await expect(row).toHaveAttribute('aria-pressed', 'true')
  await expect(row.getByTestId('pipeline-status-chip')).toHaveText('COMPLETED')

  // The stages: collapsed, so the compute canvas is not drawn; opening draws it.
  const stages = report(page).getByRole('button', { name: 'Agent stages' })
  await expect(stages).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByText('Raw stage cards')).toHaveCount(0)
  await stages.click()
  await expect(stages).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('Raw stage cards')).toBeVisible()
  // The report stays where it was: the stages open below it.
  await expect(report(page).getByText(AI_REPORT_SUMMARY)).toBeInViewport()

  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('a pipeline picked from the recent list opens its run: picker and URL follow', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/agents', {
    handlers: AGENTS,
    ready: (p) => p.getByRole('region', { name: 'Pipelines' }).getByRole('button', { name: /offline pipeline/i }),
  })
  const list = page.getByRole('region', { name: 'Pipelines' })
  await expect(list.getByRole('heading', { name: 'Recent pipelines' })).toBeVisible()
  await expect(page.getByLabel('Test suite & build')).toHaveValue('')
  await expect(report(page)).toContainText('Select a pipeline run to see agent stages')

  await list.getByRole('button', { name: /offline pipeline/i }).click()
  await expect(page).toHaveURL(new RegExp(`/agents\\?run=${RUN_ID}$`))
  await expect(page.getByLabel('Test suite & build')).toHaveValue(RUN_ID)
  await expect(list.getByRole('heading', { name: "This run's pipelines" })).toBeVisible()
  await expect(ready(page)).toBeVisible()

  // Back to every recent pipeline through the same picker.
  await page.getByLabel('Test suite & build').selectOption('')
  await expect(page).toHaveURL(/\/agents$/)
  await expect(list.getByRole('heading', { name: 'Recent pipelines' })).toBeVisible()

  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('the trigger queues a pipeline for the run in the picker', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/agents', {
    handlers: AGENTS,
    ready: (p) => p.getByRole('region', { name: 'Pipelines' }).getByRole('button', { name: /offline pipeline/i }),
  })
  const trigger = page.getByRole('button', { name: 'Trigger pipeline' })
  // No run picked: nothing to queue.
  await expect(trigger).toBeDisabled()

  await page.getByLabel('Test suite & build').selectOption(RUN_ID)
  await expect(page).toHaveURL(new RegExp(`/agents\\?run=${RUN_ID}$`))
  await expect(trigger).toBeEnabled()
  await trigger.click()
  await expect(page.getByText('Pipeline queued — it will appear in the list shortly.')).toBeVisible()
  expect(triggered).toEqual([{ test_run_id: RUN_ID }])

  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
