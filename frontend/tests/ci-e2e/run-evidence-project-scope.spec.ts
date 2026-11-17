/**
 * D-0020 (GPT-6): a member's saved link to a run, opened with another of their projects selected, said "No agent
 * pipeline has analysed this run yet." on the Evidence tab. The tab read the run's pipelines with the selected project
 * as well, and the server intersects the two (`200 []`). It now reads them by run, like the header, the tests and the
 * timing, and a read that fails says so instead of "never analysed".
 *
 * Each case opens the page with a stored project (a real load), so the selected project is the reader's. The
 * `/agents/pipelines` handler answers like the server (`agents.py` `list_pipelines`): a project the user is not a
 * member of is a 403; a member project the run is not in is an empty list.
 * The non-member half of E2E-0022 is `run-actions-inaccessible.spec.ts` (D-0023: Run not found).
 *
 * Harness: `tests/lib/production-pages.ts` (fail closed, pinned clock).
 */
import { expect, test, type Page } from '@playwright/test'
import { openProductionPage, respond, type ApiHandler, type ApiHandlers, type ApiRequest } from '../lib/production-pages'
import { assertHermetic, networkQuiet, requestsTo } from '../lib/rollout'
import { NOW, PROJECT_ID, RUN_ID, USER } from '../visual/production/fixtures'
import { LIVE_PROJECTS, SECOND_PROJECT } from '../visual/production/fixtures-activity-live'
import { RUN, RUN_PAGE } from '../visual/production/fixtures-run'

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

const NEVER = 'No agent pipeline has analysed this run yet.'
const COULD_NOT = "Could not load this run's pipelines."
const EVIDENCE = `/runs/${RUN_ID}?tab=evidence`
const report = (p: Page) => p.getByRole('region', { name: 'AI report' })
const pipelinePicker = (p: Page) => report(p).getByLabel('Pipeline')

/** The run's two pipelines: the world's (newest, whose stages and report `RUN_PAGE` answers) and an older failed one. */
function runPipelines(): Record<string, unknown>[] {
  const hit = RUN_PAGE.find(([matcher]) => matcher === '/api/v1/agents/pipelines')
  if (!hit) throw new Error('RUN_PAGE has no /api/v1/agents/pipelines handler')
  const [newest] = hit[1]({} as ApiRequest) as Record<string, unknown>[]
  const older = new Date(Date.parse(String(newest.created_at)) - 86_400_000).toISOString()
  return [
    newest,
    {
      ...newest, id: '78787878-7878-4787-8787-787878787878', workflow_type: 'investigation', status: 'failed',
      public_status: 'failed', created_at: older, started_at: older, completed_at: older,
    },
  ]
}

/** `GET /agents/pipelines` as the server answers a member of Checkout and Payments (the run is Checkout's). */
function serverLike(rows: Record<string, unknown>[]): ApiHandler {
  return ({ url }) => {
    const asked = url.searchParams.get('project_id')
    if (asked !== null && asked !== PROJECT_ID && asked !== SECOND_PROJECT.id) {
      return respond(403, { detail: 'You do not have access to this project' })
    }
    if (asked !== null && asked !== PROJECT_ID) return []
    return url.searchParams.get('run_id') === RUN_ID ? rows : []
  }
}

function open(page: Page, projectId: string, pipelines: ApiHandler) {
  const handlers: ApiHandlers = [
    ['/api/v1/projects', () => LIVE_PROJECTS],
    ['/api/v1/agents/pipelines', pipelines],
    ...RUN_PAGE,
  ]
  return openProductionPage(page, EVIDENCE, { theme: 'signal', now: NOW, me: USER, user: USER, projectId, handlers, ready: report })
}

async function expectTheRunsPipelines(page: Page) {
  await expect(page.getByRole('heading', { level: 1, name: `Run #${RUN.build_number}` })).toBeVisible()
  await expect(pipelinePicker(page).locator('option')).toHaveCount(2)
  await expect(page.getByText(NEVER)).toHaveCount(0)
}

/** Every pipelines read named the run and no project. */
function expectReadByRun(lines: string[]) {
  expect(lines.length).toBeGreaterThan(0)
  for (const line of lines) {
    expect(line).toContain(`run_id=${RUN_ID}`)
    expect(line).not.toContain('project_id=')
  }
}

test("under the run's own project, Evidence lists its two pipelines", async ({ page }) => {
  const { api, errors } = await open(page, PROJECT_ID, serverLike(runPipelines()))
  await expectTheRunsPipelines(page)
  await networkQuiet(page, api)
  expectReadByRun(requestsTo(api, '/api/v1/agents/pipelines'))
  assertHermetic(api, errors)
})

test('D-0020: with another of the member\'s projects selected, Evidence still lists the run\'s pipelines, after a reload too', async ({ page }) => {
  const { api, errors } = await open(page, SECOND_PROJECT.id, serverLike(runPipelines()))
  await expectTheRunsPipelines(page)
  await networkQuiet(page, api)
  expectReadByRun(requestsTo(api, '/api/v1/agents/pipelines'))

  // A reload keeps Payments selected (the session seed runs on every load).
  await page.reload()
  await expect(report(page)).toBeVisible()
  await expectTheRunsPipelines(page)
  await networkQuiet(page, api)
  expectReadByRun(requestsTo(api, '/api/v1/agents/pipelines'))
  assertHermetic(api, errors)
})

test('a run no pipeline has analysed still says so', async ({ page }) => {
  const { api, errors } = await open(page, PROJECT_ID, () => [])
  await expect(page.getByText(NEVER)).toBeVisible()
  await expect(page.getByText(COULD_NOT)).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})

test('D-0020: a pipeline list that fails to load says so, not that the run was never analysed', async ({ page }) => {
  const { api, errors } = await open(page, PROJECT_ID, () => respond(500, { detail: 'planted failure' }))
  await expect(page.getByText(COULD_NOT)).toBeVisible()
  await expect(page.getByText(NEVER)).toHaveCount(0)
  await networkQuiet(page, api)
  assertHermetic(api, errors)
})
