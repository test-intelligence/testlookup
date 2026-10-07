/**
 * UX redesign P4 item 7 (`03-implementation-plan.md`; `02-design-spec.md` §5
 * "Test cases"), hermetic: /test-management has five tabs — Cases · Suites ·
 * Plans · Approvals · More ▾ (Strategy, Duplicates, Audit log, Knowledge) —
 * and on Cases, at 1440 x 900, the cases table (`[data-primary]`) starts at
 * most 300 px below the top of `#main-content`. The former right rail is the
 * "Insights" drawer; "Generate test cases" is the AI Generate modal's
 * "From documents" option.
 *
 * The scroller's `scrollHeight` is printed (FOLD line) for the before/after
 * comparison in `docs/viz-work/p4-agent-D.md` (measured on the unchanged page
 * first).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock). The page has
 * no visual-spec fixtures, so its answers are here.
 */
import { expect, test, type Page } from '@playwright/test'
import type { ApiHandlers } from '../lib/production-pages'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { isoAgo, LAYOUT, PROJECT_ID } from '../visual/production/fixtures'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const STATUSES = ['active', 'draft', 'review_requested', 'approved', 'active', 'under_review', 'active', 'draft'] as const
const CASES = STATUSES.map((status, i) => ({
  id: `0000000${i}-aaaa-4000-8000-00000000000${i}`,
  project_id: PROJECT_ID,
  title: `Checkout case ${i + 1}: the cart survives a refresh`,
  test_type: 'functional',
  priority: ['critical', 'high', 'medium', 'low'][i % 4],
  severity: 'major',
  suite_name: i % 2 ? 'Auth' : 'Checkout',
  test_suite_id: null,
  status,
  version: 1,
  is_automated: i % 3 === 0,
  automation_status: i % 3 === 0 ? 'automated' : 'manual',
  ai_generated: false,
  source: 'managed',
  allowed_actions: ['deprecate'],
  created_at: isoAgo(40 + i),
  updated_at: isoAgo(2 + i),
}))

const page = <T>(items: T[], size = 25) => ({ items, total: items.length, page: 1, size, pages: items.length ? 1 : 0 })

/** The suites list (by name) and the first-class suites (with ids) the Suites tab links to. */
const SUITE_IDS: Record<string, string> = {
  Checkout: '5a000000-0000-4000-8000-000000000001',
  Auth: '5a000000-0000-4000-8000-000000000002',
}
const SUITES = Object.keys(SUITE_IDS).map((name, i) => ({
  suite_name: name,
  test_count: 4,
  passed_count: 3,
  failed_count: 1,
  last_run_at: isoAgo(1),
  last_run_id: null,
  pass_rate: 75,
  run_count: 3 + i,
  total_executions: 12,
  total_passed: 9,
  total_failed: 3,
  total_skipped: 0,
  total_broken: 0,
}))

const TEST_CASES: ApiHandlers = [
  ...LAYOUT,
  ['/api/v1/test-management/cases', ({ url }) => {
    const status = url.searchParams.get('status')
    const size = Number(url.searchParams.get('size') ?? 25)
    return page(status ? CASES.filter((c) => c.status === status) : CASES, size)
  }],
  ['/api/v1/test-management/audit', () => page([], 5)],
  ['/api/v1/test-management/cases/evidence-gaps', () => ({ items: [], total: 0 })],
  ['/api/v1/canonical-test-cases/orphaned', () => page([])],
  ['/api/v1/test-management/suites', () => SUITES],
  ['/api/v1/suites', () => ({
    items: Object.entries(SUITE_IDS).map(([name, id]) => ({
      id,
      project_id: PROJECT_ID,
      name,
      description: null,
      is_default: false,
      tags: null,
      test_case_count: 4,
      created_at: isoAgo(60),
      updated_at: null,
    })),
    total: 2,
  })],
  ['/api/v1/auth/users', () => []],
  [`/api/v1/projects/${PROJECT_ID}/members`, () => []],
  ['/api/v1/test-management/strategies', () => []],
  ['/api/v1/test-management/plans', () => page([])],
]

const ready = (p: Page) => p.getByRole('heading', { name: 'Test Case Management', level: 1 })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface FoldGeometry {
  scrollHeight: number
  primaryTop: number | null
  primaries: number
  /** The cases table card (`Cases N of M`), px below the scroller's top, for the before/after report. */
  tableTop: number | null
  blocks: Record<string, [number, number] | null>
}

async function foldGeometry(page: Page): Promise<FoldGeometry> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    main.scrollTop = 0
    const top = main.getBoundingClientRect().top
    const span = (css: string): [number, number] | null => {
      const box = document.querySelector(css)?.getBoundingClientRect()
      return box ? [Math.round(box.top - top), Math.round(box.bottom - top)] : null
    }
    const primaries = document.querySelectorAll('[data-primary]')
    const primary = primaries[0] as HTMLElement | undefined
    const table = document.querySelector('table[role="table"]')?.closest('.rounded-xl')
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
      tableTop: table ? Math.round(table.getBoundingClientRect().top - top) : null,
      blocks: {
        sectionTabs: span('[data-section-tabs]'),
        header: span('[data-page-header]'),
        tabs: span('[data-tabs]'),
        banner: span('[data-status-banner]'),
        primary: span('[data-primary]'),
      },
    }
  }, MAIN)
}

const tabBar = (p: Page) => p.getByRole('tablist', { name: 'Test case sections' })

test('Cases: the table starts within the fold budget at 1440 x 900', async ({ page }, testInfo) => {
  const { api, errors } = await openRollout(page, '/test-management', { handlers: TEST_CASES, ready })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD /test-management ${JSON.stringify(geometry)}`)
  // For reading the layout by eye (never compared): FOLD_SHOT=1.
  if (process.env.FOLD_SHOT) await page.screenshot({ path: testInfo.outputPath('test-cases-1440.png') })
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)
  const table = page.locator('[data-primary]').getByRole('table')
  await expect(table).toBeVisible()
  await expect(table.getByRole('row').nth(1)).toBeInViewport()

  // Five tabs, Cases first and selected.
  await expect(tabBar(page).getByRole('tab')).toHaveText(['Cases', 'Suites', 'Plans', 'Approvals', /^More/])
  await expect(tabBar(page).getByRole('tab', { name: 'Cases' })).toHaveAttribute('aria-selected', 'true')
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Cases: the Insights drawer holds the former right rail', async ({ page }, testInfo) => {
  const { api, errors } = await openRollout(page, '/test-management', { handlers: TEST_CASES, ready })
  await expect(page.getByRole('complementary', { name: 'Insights' })).toHaveCount(0)
  await page.getByRole('button', { name: /^Insights/ }).click()
  const drawer = page.getByRole('complementary', { name: 'Insights' })
  await expect(drawer).toBeVisible()
  if (process.env.FOLD_SHOT) await page.screenshot({ path: testInfo.outputPath('test-cases-insights.png') })
  await expect(drawer.getByRole('heading', { name: /^Review queue/ })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Strategy gaps' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Recent activity' })).toBeVisible()
  await expect(drawer.getByRole('heading', { name: 'Automation coverage' })).toBeVisible()
  await drawer.getByRole('button', { name: 'Close panel' }).click()
  await expect(drawer).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('AI Generate offers "From documents" (the knowledge generation flow) beside a description', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/test-management', {
    handlers: [
      // The knowledge flow asks its status and the project's sources; RAG off here.
      ['/api/v1/test-management/rag/status', () => ({ enabled: false })],
      ['/api/v1/knowledge-sources', () => ({ items: [], total: 0 })],
      ...TEST_CASES,
    ],
    ready,
  })
  await page.getByRole('button', { name: 'AI Generate' }).first().click()
  const dialog = page.getByRole('dialog', { name: 'AI Generate Test Cases' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('tab', { name: 'From a description' })).toHaveAttribute('aria-selected', 'true')
  await dialog.getByRole('tab', { name: 'From documents' }).click()
  await expect(dialog.getByText('Knowledge RAG is not enabled')).toBeVisible()
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Suites: rows link to the suite page, and the old ?tab=Test+Suites link still opens it', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/test-management?tab=Test+Suites', { handlers: TEST_CASES, ready })
  await expect(tabBar(page).getByRole('tab', { name: 'Suites' })).toHaveAttribute('aria-selected', 'true')
  const checkout = page.getByRole('link', { name: 'Checkout', exact: true })
  await expect(checkout).toHaveAttribute('href', `/suites/${SUITE_IDS.Checkout}`)
  await expect(page.getByRole('link', { name: 'Trend →' }).first()).toHaveAttribute('href', `/suites/${SUITE_IDS.Checkout}?tab=charts`)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('Approvals: ?tab=reviews is an alias, and the tab is named Approvals', async ({ page }) => {
  const { api, errors } = await openRollout(page, '/test-management?tab=reviews', { handlers: TEST_CASES, ready })
  await expect(tabBar(page).getByRole('tab', { name: 'Approvals' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText(/test cases awaiting review/)).toBeVisible()
  await networkQuiet(page, api)
  expect(requestsTo(api, '/api/v1/test-management/cases').length).toBeGreaterThan(0)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('More ▾ is a menu that selects Strategy, Duplicates, Audit log or Knowledge', async ({ page }, testInfo) => {
  const { api, errors } = await openRollout(page, '/test-management', { handlers: TEST_CASES, ready })
  await tabBar(page).getByRole('tab', { name: /^More/ }).click()
  const menu = page.getByRole('menu', { name: 'More sections' })
  await expect(menu.getByRole('menuitem')).toHaveText(['Strategy', 'Duplicates', 'Audit log', 'Knowledge'])
  if (process.env.FOLD_SHOT) await page.screenshot({ path: testInfo.outputPath('test-cases-more.png') })
  await menu.getByRole('menuitem', { name: 'Strategy' }).click()
  await expect(page).toHaveURL(/[?&]tab=strategy\b/)
  await expect(tabBar(page).getByRole('tab', { name: /^More/ })).toHaveAttribute('aria-selected', 'true')
  await expect(tabBar(page).getByRole('tab', { name: /^More/ })).toContainText('Strategy')
  await expect(page.getByText('No test strategy')).toBeVisible()
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
