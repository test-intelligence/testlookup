/**
 * UX redesign P4 item 6 (`03-implementation-plan.md`; `02-design-spec.md` §5
 * "Release gate"), hermetic: /release-gate/<run> is verdict-first. At
 * 1440 x 900 the verdict card (GO / NO-GO, pass rate, the top blockers,
 * Override: `[data-primary]`) starts at most 300 px below the top of
 * `#main-content`; the explanation is in the tabs Why · Context · History
 * below it, and the decision pipeline in a collapsed disclosure at the bottom.
 *
 * The scroller's `scrollHeight` is printed (FOLD line) for a before/after
 * comparison (measured on the unchanged page first).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the `prod-release-gate` visual spec).
 */
import { expect, test, type Page } from '@playwright/test'
import { opened } from '../lib/inPage'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
import { respond } from '../lib/production-pages'
import { CHART_DATA_PATH, GATE_CLUSTERS, releaseGateOn, RUN_ID } from '../visual/production/fixtures'
// Four stored blockers (the card shows the first three), over the shared handlers' floored NO_GO.
import { withGateBlockers as withBlockers } from '../visual/production/fixtures-pages'

/** The fold budget of the page template (§2): primary content top, px below the scroller's top. */
const FOLD_BUDGET_PX = 300

const PATH = `/release-gate/${RUN_ID}`
const ready = (p: Page) => p.getByRole('meter', { name: 'Risk Score' })

test.use({ viewport: { width: 1440, height: 900 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

interface FoldGeometry {
  scrollHeight: number
  primaryTop: number | null
  primaries: number
  /** The recommendation card (the ring's card), px below the scroller's top, for the before/after report. */
  verdictTop: number | null
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
    const ring = document.querySelector('[role="meter"][aria-label="Risk Score"]')
    const card = ring?.closest('.card, [data-primary]')
    return {
      scrollHeight: main.scrollHeight,
      primaryTop: primary ? Math.round(primary.getBoundingClientRect().top - top) : null,
      primaries: primaries.length,
      verdictTop: card ? Math.round(card.getBoundingClientRect().top - top) : null,
      blocks: {
        sectionTabs: span('[data-section-tabs]'),
        header: span('[data-page-header]'),
        primary: span('[data-primary]'),
        tabs: span('[data-tabs]'),
      },
    }
  }, MAIN)
}

test('the verdict card starts within the fold budget at 1440 x 900, before the tabs', async ({ page }, testInfo) => {
  const { api, errors } = await openRollout(page, PATH, {
    handlers: withBlockers(releaseGateOn({ clusters: GATE_CLUSTERS })),
    ready,
  })
  await networkQuiet(page, api)
  const geometry = await foldGeometry(page)
  console.log(`FOLD ${PATH} ${JSON.stringify(geometry)}`)
  // For reading the layout by eye (never compared): FOLD_SHOT=1.
  if (process.env.FOLD_SHOT) await page.screenshot({ path: testInfo.outputPath('release-gate-1440.png') })
  expect(geometry.primaries, 'one primary content element').toBe(1)
  expect(geometry.primaryTop, 'the primary content top, px below #main-content').not.toBeNull()
  expect(geometry.primaryTop as number).toBeLessThanOrEqual(FOLD_BUDGET_PX)

  // It is the verdict: the recommendation, the ring, the pass rate, the top
  // blockers and the Override control, all on the first screen.
  const verdict = page.locator('[data-primary]')
  await expect(verdict).toContainText('NO GO')
  await expect(verdict).toContainText('Pass rate: 44.4%')
  await expect(verdict.getByRole('meter', { name: 'Risk Score' })).toBeInViewport()
  await expect(verdict.getByRole('list', { name: 'Top blockers' }).getByRole('listitem')).toHaveCount(3)
  await expect(verdict.getByRole('button', { name: 'Override decision' })).toBeInViewport()

  // The tabs come after the verdict; Why is the default.
  const tabs = page.getByRole('tablist', { name: 'Release gate sections' })
  // History carries its override count (none in this fixture).
  await expect(tabs.getByRole('tab')).toHaveText(['Why', 'Context', 'History0'])
  await expect(tabs.getByRole('tab', { name: 'Why' })).toHaveAttribute('aria-selected', 'true')
  const verdictFirst = await page.evaluate(() => {
    const p = document.querySelector('[data-primary]') as Element
    const t = document.querySelector('[role="tablist"][aria-label="Release gate sections"]') as Element
    return Boolean(p.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING)
  })
  expect(verdictFirst, 'the verdict card precedes the tab bar').toBe(true)

  // The Context charts are in their own tab: not asked for at load.
  expect(requestsTo(api, CHART_DATA_PATH), 'no chart-data before Context is opened').toEqual([])
  expect(requestsTo(api, `/api/v1/runs/${RUN_ID}`), 'no run read before Context is opened').toEqual([])

  // The pipeline is a collapsed disclosure at the bottom.
  const pipeline = page.getByRole('button', { name: 'How this was decided' })
  await expect(pipeline).toHaveAttribute('aria-expanded', 'false')
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

test('opening Context loads the charts (the run, then one chart-data) and ?tab=context selects it', async ({ page }) => {
  const { api, errors } = await openRollout(page, `${PATH}?tab=context`, {
    handlers: releaseGateOn({ clusters: GATE_CLUSTERS }),
    ready,
  })
  const tabs = page.getByRole('tablist', { name: 'Release gate sections' })
  await expect(tabs.getByRole('tab', { name: 'Context' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('[data-catalogue-section="gate-context"]')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('heading', { name: 'Linked Failure Clusters', exact: true })).toBeVisible()
  await expect.poll(() => requestsTo(api, CHART_DATA_PATH).length, { timeout: 20_000 }).toBe(1)
  await networkQuiet(page, api)
  expect(requestsTo(api, `/api/v1/runs/${RUN_ID}`)).toHaveLength(1)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

/**
 * HON-06: "How this was decided" dates the decision from the stored row's
 * `created_at` and each override from its audit entry, newest first. It used
 * to stamp every stage `now - n * 45 s`, which moved on every reload.
 */
test('How this was decided shows the decision and the override at their recorded times', async ({ page }) => {
  const decidedAt = '2026-09-10T08:30:00Z'
  const overriddenAt = '2026-09-12T16:45:00Z'
  const decision = {
    run_id: RUN_ID,
    recommendation: 'NO_GO',
    risk_score: 60,
    composite_risk: 60,
    dimension_scores: [],
    blocking_issues: ['Checkout suite failing'],
    conditions_for_go: [],
    reasoning: 'Stored decision',
    score_model_version: 1,
    input_snapshot: null,
    cluster_insights: [],
    baseline_diff: null,
    open_defects_by_component: [],
    human_override: 'perf soak pending',
    overridden_by: 'u-qa-lead',
    original_recommendation: 'GO',
    original_risk_score: 30,
    override_audit: [
      {
        timestamp: overriddenAt,
        actor_id: 'u-qa-lead',
        actor_name: 'priya',
        before_recommendation: 'GO',
        before_risk_score: 30,
        after_recommendation: 'NO_GO',
        reason: 'perf soak pending',
        policy_id: null,
        policy_version: null,
      },
    ],
    pass_rate: 44.4,
    build_number: '240',
    policy_id: null,
    policy_version: 1,
    policy_level: 'project',
    rule_evaluations: [],
    synthesized: false,
    created_at: decidedAt,
    updated_at: overriddenAt,
  }
  const { api, errors } = await openRollout(page, PATH, {
    handlers: [[/^\/api\/v1\/release-readiness\/[^/]+$/, () => decision], ...releaseGateOn({ clusters: GATE_CLUSTERS })],
    ready,
  })
  await page.getByRole('button', { name: 'How this was decided' }).click()
  await expect(page.getByText('Workflow Event Feed')).toBeVisible()

  const times = page.locator('[data-event-time]')
  await expect(times).toHaveCount(2)
  expect(await times.evaluateAll((els) => els.map((el) => el.getAttribute('data-event-time')))).toEqual([
    new Date(overriddenAt).toISOString(),
    new Date(decidedAt).toISOString(),
  ])
  // The text a reader sees (UTC, pinned above): the records' times, not the
  // pinned "now" the page loaded at.
  await expect(times.nth(0)).toHaveText('Sep 12, 16:45')
  await expect(times.nth(1)).toHaveText('Sep 10, 08:30')
  await expect(page.getByText('Decision overridden')).toBeVisible()
  await expect(page.getByText('priya', { exact: true })).toBeVisible()
  // No stage claims a confidence the gate never measured.
  await expect(page.getByText(/% confidence/)).toHaveCount(0)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})

/**
 * GATE-06: Share report chooses the layout and the expiry, lists the run's
 * links (created by, expiry, views) and revokes one. It used to mint an
 * executive link for 7 days on the click and could neither list nor revoke.
 * The links' wire shape is `ShareLinkResponse` (`routers/reports.py`): the
 * URL and token only on the create response.
 */
test('Share report: Engineering for 14 days, listed with its expiry and views, then revoked', async ({ page }) => {
  const DAY = 86_400_000
  const now = new Date('2026-09-18T12:00:00Z').getTime() // the pinned clock (`NOW`)
  const created: Record<string, unknown>[] = []
  const posted: unknown[] = []
  const deleted: string[] = []
  const shareUrl = 'http://127.0.0.1:4179/api/v1/shared/reports/raw-token-1'
  const { api, errors } = await openRollout(page, PATH, {
    handlers: [
      [
        `/api/v1/reports/runs/${RUN_ID}/share`,
        ({ route }) => {
          const body = route.request().postDataJSON() as { layout: string; expiry_days: number }
          posted.push(body)
          const link = {
            id: 'link-1',
            report_layout: body.layout,
            expires_at: new Date(now + body.expiry_days * DAY).toISOString(),
            created_by_name: 'qa_lead',
            access_count: 0,
            is_revoked: false,
            created_at: new Date(now).toISOString(),
          }
          created.push(link)
          return { ...link, token: 'raw-token-1', share_url: shareUrl, snapshot_ready: true, warning: null }
        },
        'POST',
      ],
      [`/api/v1/reports/runs/${RUN_ID}/share-links`, () => created.map((l) => ({ ...l, token: null, share_url: null }))],
      [
        /^\/api\/v1\/reports\/share-links\/[^/]+$/,
        ({ path }) => {
          const id = path.split('/').pop() as string
          deleted.push(id)
          for (const link of created) if (link.id === id) link.is_revoked = true
          return respond(204)
        },
        'DELETE',
      ],
      ...releaseGateOn({ clusters: GATE_CLUSTERS }),
    ],
    ready,
  })
  await page.getByRole('button', { name: 'More actions' }).click()
  await page.getByRole('menuitem', { name: /Share report/ }).click()
  const dialog = opened(page, 'Share report')
  await expect(dialog.getByText('No share links for this run yet.')).toBeVisible()

  await dialog.getByRole('radio', { name: 'Engineering' }).check()
  await dialog.getByRole('combobox', { name: 'Link expires after' }).selectOption('14')
  await dialog.getByRole('button', { name: 'Create link' }).click()
  await expect(dialog.getByRole('textbox', { name: 'Share URL' })).toHaveValue(shareUrl)
  expect(posted).toEqual([{ layout: 'engineering', expiry_days: 14 }])

  const row = dialog.locator('[data-share-link="link-1"]')
  await expect(row).toHaveAttribute('data-share-status', 'active')
  await expect(row.locator('[data-share-summary]')).toHaveText('expires in 14 days, 0 views')
  await expect(row).toContainText('Engineering')

  await row.getByRole('button', { name: 'Revoke' }).click()
  await expect(row).toHaveAttribute('data-share-status', 'revoked')
  await expect(row).toContainText('Revoked')
  await expect(row.getByRole('button', { name: 'Revoke' })).toHaveCount(0)
  expect(deleted).toEqual(['link-1'])
  expect(requestsTo(api, `/api/v1/reports/runs/${RUN_ID}/share-links`).length).toBeGreaterThanOrEqual(3)
  await networkQuiet(page, api)
  expect(api.unhandled).toEqual([])
  expect(errors).toEqual([])
})
