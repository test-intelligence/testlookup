/**
 * UX redesign P4 item 6 (`03-implementation-plan.md`; `02-design-spec.md` §5
 * "Release gate"), hermetic: /release-gate/<run> is verdict-first. At
 * 1440 x 900 the verdict card (GO / NO-GO, pass rate, the top blockers,
 * Override: `[data-primary]`) starts at most 300 px below the top of
 * `#main-content`; the explanation is in the tabs Why · Context · History
 * below it, and the decision pipeline in a collapsed disclosure at the bottom.
 *
 * The scroller's `scrollHeight` is printed (FOLD line) for the before/after
 * comparison in `docs/viz-work/p4-agent-D.md` (measured on the unchanged page
 * first).
 *
 * Harness: `tests/lib/rollout.ts` (fail closed, pinned clock, the fixtures of
 * the `prod-release-gate` visual spec).
 */
import { expect, test, type Page } from '@playwright/test'
import { MAIN, networkQuiet, openRollout, requestsTo } from '../lib/rollout'
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
