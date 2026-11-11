import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { apiRequest, authenticatePage, createDataProject } from './data-project'
import { checkStatus } from './http'

const fixtures = fileURLToPath(new URL('../../../e2e-runner/fixtures/', import.meta.url))
const pythonLeaves = { test_report_pass: 'PASSED', test_report_failure: 'FAILED', test_report_skipped: 'SKIPPED' }
const lowerLeaves = { passes: 'PASSED', fails: 'FAILED', skips: 'SKIPPED' }
const upperLeaves = { Passes: 'PASSED', Fails: 'FAILED', Skips: 'SKIPPED' }
const robotLeaves = { 'Producer observed pass': 'PASSED', 'Producer observed failure': 'FAILED', 'Producer observed skip': 'SKIPPED' }
const allureDir = resolve(fixtures, 'native/allure-results')
const broken = readdirSync(allureDir).find(f => JSON.parse(readFileSync(resolve(allureDir, f), 'utf8')).name === 'test_report_broken')
if (!broken) throw Error('Missing native Allure result for test_report_broken')
const contracts = {
  junit: ['pytest-junit.xml', pythonLeaves],
  pytest: ['pytest-report.json', pythonLeaves],
  robot: ['robot.xml', robotLeaves],
  testng: ['native/testng.xml', lowerLeaves],
  nunit: ['native/nunit.xml', upperLeaves],
  trx: ['native/report.trx', upperLeaves],
  xunit: ['native/xunit.xml', upperLeaves],
  allure: ['native/allure-results/' + broken, { test_report_broken: 'BROKEN' }],
  cypress: ['native/cypress.json', lowerLeaves],
  playwright: ['native/playwright.json', lowerLeaves],
  cucumber: ['native/cucumber.json', upperLeaves],
} as const
type Leaf = Record<string, string>
type Input = string | string[] | {name: string; mimeType: string; buffer: Buffer}
type Project = Awaited<ReturnType<typeof createDataProject>>

async function uploadAndVerify(page: Page, request: APIRequestContext, data: Project, format: string,
                               input: Input, expected: Leaf, build: string, storedBuild = build) {
  await page.goto('/runs?upload=1')
  await expect(page.getByRole('heading', { name: 'Upload test report' })).toBeVisible()
  const projectSelector = page.getByRole('combobox', { name: /^Project/ })
  if (await projectSelector.count()) await projectSelector.selectOption(data.project.id)
  await page.locator('input[type=file]').setInputFiles(input)
  const formatSelector = page.getByLabel('Format', { exact: true })
  if (format === 'junit') {
    const offered = await formatSelector.locator('option').evaluateAll(options =>
      options.map(option => (option as HTMLOptionElement).value).sort())
    expect(offered, 'each format in the deployed form needs a native producer case')
      .toEqual(['auto', ...Object.keys(contracts)].sort())
  }
  await formatSelector.selectOption(format)
  await page.getByRole('checkbox', { name: /Skip AI analysis/ }).check()
  await page.getByRole('button', { name: 'Optional metadata' }).click()
  await page.getByLabel('Build label').fill(build)
  // Hold the browser's first receipt read. The upload, worker and receipt
  // remain real, while the processing UI is observable on fast deployments.
  let releaseRead: (() => void) | undefined
  if (format === 'junit') {
    const gate = new Promise<void>(resolve => { releaseRead = resolve })
    await page.route('**/api/v1/ingest/uploads/*', async route => {
      await gate
      await route.continue()
    }, { times: 1 })
  }
  const receipt = page.waitForResponse(r => r.request().method() === 'POST'
    && new URL(r.url()).pathname === '/api/v1/ingest/file')
  await page.getByRole('button', { name: 'Upload', exact: true }).click()
  const accepted = await receipt
  expect(accepted.status(), 'report submission').toBe(202)
  const { task_id, run_id } = await accepted.json()
  expect(task_id).toBeTruthy()
  expect(run_id).toBeTruthy()
  if (releaseRead) {
    try { await expect(page.getByText('Processing report…', { exact: true })).toBeVisible() }
    finally { releaseRead() }
  }
  await expect.poll(async () => {
    const response = await apiRequest(request, 'GET', data.api + '/ingest/uploads/' + task_id, { headers: data.headers })
    await checkStatus('GET', response, 200)
    return (await response.json()).state
  }, { timeout: 60_000, message: format + ' receipt must complete' }).toBe('succeeded')
  await expect(page.getByText('Processed ' + Object.keys(expected).length + (Object.keys(expected).length === 1 ? ' test' : ' tests'),
    { exact: true })).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: 'View run', exact: true }).click()
  await expect(page).toHaveURL(new RegExp('/runs/' + run_id + '(?:[?#]|$)'))
  await page.reload()
  const failedLeaf = Object.keys(expected).find(name => ['BROKEN', 'FAILED'].includes(expected[name]))
  if (!failedLeaf) throw Error('Fixture must include a failed or broken leaf to drill into')
  await expect(page.getByText(failedLeaf, { exact: true }).first()).toBeVisible({ timeout: 20_000 })
  const run = await apiRequest(request, 'GET', data.api + '/runs/' + run_id, { headers: data.headers })
  await checkStatus('GET', run, 200)
  const summary = await run.json()
  expect([summary.project_id, summary.build_number, summary.total_tests])
    .toEqual([data.project.id, storedBuild, Object.keys(expected).length])
  for (const state of ['PASSED', 'FAILED', 'BROKEN', 'SKIPPED']) {
    expect(summary[state.toLowerCase() + '_tests']).toBe(Object.values(expected).filter(value => value === state).length)
  }
  const list = await apiRequest(request, 'GET', data.api + '/runs/' + run_id + '/tests', { headers: data.headers })
  await checkStatus('GET', list, 200)
  const { items } = await list.json()
  expect(items.map((i: {test_name: string; status: string}) => [i.test_name, i.status]).sort())
    .toEqual(Object.entries(expected).sort())
  const leaf = items.find((i: {test_name: string}) => i.test_name === failedLeaf)
  const detail = await apiRequest(request, 'GET', data.api + '/runs/' + run_id + '/tests/' + leaf.id, { headers: data.headers })
  await checkStatus('GET', detail, 200)
  expect((await detail.json()).test_name).toBe(failedLeaf)
  return run_id
}

const tags = ['@P0', '@regression', '@full', '@ephemeral-only', '@area:Ingest real reports', '@journey:J02', '@role:QA_ENGINEER']
for (const [format, [file, expected]] of Object.entries(contracts)) {
  test('TL-INGEST-001 ' + format + ' native producer through the upload form', { tag: tags }, async ({ page, request }) => {
    const data = await createDataProject(request, 'qa_engineer')
    try {
      await authenticatePage(page, data)
      const build = data.project.name
      const explicit = await test.step('Select ' + format + ', upload and inspect persisted leaves', () =>
        uploadAndVerify(page, request, data, format, resolve(fixtures, file), expected, build))
      const detected = await test.step('Re-submit the same build and auto-detect the format', () =>
        uploadAndVerify(page, request, data, 'auto', resolve(fixtures, file), expected, build, build + '-2'))
      expect(detected, 'manual resubmissions are independent receipts').not.toBe(explicit)
      if (format === 'junit') {
        const response = await apiRequest(request, 'POST', data.api + '/ingest/file', { headers: data.headers, multipart: {
          file: { name: 'empty.xml', mimeType: 'application/xml', buffer: Buffer.alloc(0) },
          format: 'junit', project_id: data.project.id, build_number: build,
        } })
        await checkStatus('POST', response, 400)
      }
    } finally { await data.remove() }
  })
}

test('TL-INGEST-001 zip Allure archive and browser-generated mixed archive', { tag: tags }, async ({ page, request }) => {
  const data = await createDataProject(request, 'qa_engineer')
  try {
    await authenticatePage(page, data)
    const archive = execFileSync(process.env.TL_PYTHON || 'python', ['-c', [
      'import io,pathlib,sys,zipfile',
      'buf=io.BytesIO()',
      'with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:',
      ' for path in sorted(pathlib.Path(sys.argv[1]).glob("*-result.json")): z.write(path,"allure-results/"+path.name)',
      'sys.stdout.buffer.write(buf.getvalue())',
    ].join('\n'), allureDir])
    await uploadAndVerify(page, request, data, 'auto', { name:'allure-results.zip', mimeType:'application/zip', buffer:archive },
      { ...pythonLeaves, test_report_broken:'BROKEN' }, data.project.name)
    await uploadAndVerify(page, request, data, 'auto', [resolve(fixtures,'pytest-junit.xml'), resolve(fixtures,'robot.xml')],
      { ...pythonLeaves, ...robotLeaves }, data.project.name + '-mixed')
  } finally { await data.remove() }
})
