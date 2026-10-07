/**
 * /release-gate/<run> with the catalogue (Wave 2.6, VIZ-408; plan 2.2
 * "Release gate", 2.5, 5.3). A "Context" group AFTER the recommendation card:
 * pass rate by release (live: `GET /runs/{id}` for the run's release, then one
 * `chart-data` by release, lazy) and the failure-cluster share (the STORED
 * decision's clusters: a donut at five or fewer, a ranked bar past it).
 *
 * The charts explain a stored verdict and must never imply another one
 * (plan 2.5): the recommendation card and the risk ring are the same DOM
 * before the group arrives and after it draws, beside a 100% release for a
 * NO_GO and a 0% release for a GO; and the group is the same markup whatever
 * the verdict.
 *
 * Phase D S2: the page no longer asks `viz_chart_data_api` (migration 0195
 * turned it on everywhere and the flag-off path is deleted), so every load
 * here runs with every flag OFF in the harness and the inventory has no seam
 * lookup (`SHELL_BASE`). The flag-off test ("no Context group") is gone.
 *
 * Fail-closed harness: `tests/lib/production-pages.ts`; helpers: `rollout.ts`.
 */
import { expect, test, type Page } from '@playwright/test'
import { cardAround, type ApiHandlers } from '../lib/production-pages'
import {
  expectDrawn,
  expectInventory,
  expectNoErrorFrame,
  expectNoTextEscapes,
  networkQuiet,
  normalisedMarkup,
  openRollout,
  proveLazyMount,
  requestsTo,
  section,
  sectionFrame,
  SHELL_BASE,
} from '../lib/rollout'
import {
  CHART_DATA_PATH,
  GATE_CLUSTERS,
  GATE_CLUSTERS_7,
  HOSTILE_NAME,
  PROJECT_ID,
  RELEASE_ID,
  RELEASES,
  releaseGateOn,
  RUN_ID,
} from '../visual/production/fixtures'
import { expectNoBlockingViolations } from '../lib/axe-gate'

const P = PROJECT_ID
const PATH = `/release-gate/${RUN_ID}`
const ready = (p: Page) => p.getByRole('meter', { name: 'Risk Score' })

/** The five releases compared for the run in 2026.09: its own first, then the newest four with runs. */
const COMPARED = [RELEASE_ID.current, RELEASE_ID.planned, RELEASE_ID.august, RELEASE_ID.hostile, RELEASE_ID.july]
const releaseQuery = (ids: readonly string[]) =>
  `metric=pass_rate&group_by=day&group_by=release&project_id=${P}&days=90&${[...ids]
    .sort()
    .map((id) => `release_id=${id}`)
    .join('&')}`

/** The shell and the gate's own reads, plus the run read once and one chart-data; the release list is the cached one. */
const inventoryOn = (ids: readonly string[] | null) => [
  ...SHELL_BASE,
  `GET /api/v1/release-readiness/${RUN_ID}`,
  'GET /api/v1/scoring-model',
  `GET /api/v1/runs?project_id=${P}&page=1&size=1`,
  'GET /api/v1/settings/ai',
  `GET /api/v1/runs/${RUN_ID}`,
  ...(ids ? [`GET ${CHART_DATA_PATH}?${releaseQuery(ids)}`] : []),
]

async function openGate(page: Page, handlers: ApiHandlers) {
  return openRollout(page, PATH, { handlers, ready })
}

/** The Context group's chunk (the dev server's `.tsx`, a build's hashed `.js`); NOT `GateContextHeader`. */
const CONTEXT_CHUNK = /\/GateCatalogue(\.tsx|-[\w-]+\.js)(\?.*)?$/

/** Hold the Context group's chunk until `release()`; counts how often it was asked. */
async function holdContextChunk(page: Page) {
  let release: () => void = () => {}
  const held = new Promise<void>((resolve) => {
    release = resolve
  })
  const state = { asked: 0, release: () => release() }
  await page.route(CONTEXT_CHUNK, async (route) => {
    state.asked += 1
    await held
    await route.continue()
  })
  return state
}

/** The recommendation card (ring included), its text and the ring's reading. */
async function verdict(page: Page) {
  const card = cardAround(page.getByText('Risk Score', { exact: true }))
  await expect(card).toContainText('Recommendation')
  return {
    markup: await normalisedMarkup(card),
    text: (await card.innerText()).trim(),
    ring: await card.getByRole('meter', { name: 'Risk Score' }).getAttribute('aria-valuenow'),
  }
}

test.describe('Release gate, everything on screen (1280 x 2400)', () => {
  test.use({ viewport: { width: 1280, height: 2400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('the Context group: both charts with their headings and captions, after the recommendation card', async ({ page }) => {
    const { api, errors } = await openGate(page, releaseGateOn({ clusters: GATE_CLUSTERS }))
    const context = page.getByRole('region', { name: 'Context' })
    await expect(context).toHaveAttribute('data-catalogue-section', 'gate-context')
    // An h3 like every other gate section (R2-8): no h2 puts the sections after it under "Context".
    await expect(context.getByRole('heading', { level: 3, name: 'Context', exact: true })).toBeVisible()

    const releases = sectionFrame(page, 'gate-releases', 'Pass rate by release')
    await expectDrawn(releases, 'gate-releases')
    // Both dated captions are in their frame's own footer, inside the border (R2-21).
    await expect(section(page, 'gate-releases').locator('[data-chart-frame] [data-chart-footer] [data-gate-caption="live"]')).toHaveText(
      'Live data as of 2026-09-18 12:00 UTC. The verdict above is the stored decision for build 240; it is not computed from this chart.',
    )
    await expect(section(page, 'gate-releases').locator('[data-gate-note="not-attributed"]')).toHaveCount(0)

    const clusters = sectionFrame(page, 'gate-clusters', 'Failure cluster share')
    await expectDrawn(clusters, 'gate-clusters')
    await expect(section(page, 'gate-clusters').locator('[data-chart-choice="donut"]')).toHaveCount(1)
    await expect(
      section(page, 'gate-clusters').locator('[data-chart-frame] [data-chart-footer] [data-gate-caption="stored"]'),
    ).toContainText(
      'recorded with the decision for build 240',
    )

    // Placement (rule 2.5.1): after the recommendation card, outside it, above the cluster list.
    const card = cardAround(page.getByText('Risk Score', { exact: true }))
    const order = await page.evaluate(() => {
      const ring = document.querySelector('[role="meter"][aria-label="Risk Score"]') as Element
      const cardEl = ring.closest('.card, section, .rounded-xl') as Element
      const ctx = document.querySelector('[data-catalogue-section="gate-context"]') as Element
      const list = Array.from(document.querySelectorAll('h1,h2,h3')).find((h) => h.textContent?.trim() === 'Linked Failure Clusters') as Element
      const follows = (a: Element, b: Element) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
      return { afterCard: follows(cardEl, ctx), insideCard: cardEl.contains(ctx), beforeList: follows(ctx, list) }
    })
    expect(order).toEqual({ afterCard: true, insideCard: false, beforeList: true })
    await expect(card.locator('[data-catalogue-section]')).toHaveCount(0)

    // Hostile release and cluster names are text.
    await expect(context.locator('img')).toHaveCount(0)
    expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
    await expect(releases).toContainText(HOSTILE_NAME)
    await expectNoErrorFrame(page)
    await expectNoTextEscapes(page, 'Release gate at 1280')
    await networkQuiet(page, api)
    expectInventory(api, errors, inventoryOn(COMPARED), 'Release gate, every flag off')
  })

  // Plan 5.3 item 8 / 5.5 (R2-11): axe at EVERY impact, full tag set, on the
  // Context group's subtree, in both themes the harness renders. No
  // allowlist: R2 measured 0 violations here.
  for (const theme of ['signal', 'lab'] as const) {
    test(`axe: the Context group, every impact, no violation (${theme})`, async ({ page }) => {
      const { api } = await openRollout(page, PATH, {
        handlers: releaseGateOn({ clusters: GATE_CLUSTERS }),
        ready,
        theme,
      })
      await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), 'gate-releases')
      await expectDrawn(sectionFrame(page, 'gate-clusters', 'Failure cluster share'), 'gate-clusters')
      await networkQuiet(page, api)
      await expectNoBlockingViolations(page, theme, [], ['[data-catalogue-section]'])
    })
  }

  test('seven stored clusters: a ranked bar, and no pie offered', async ({ page }) => {
    const { api, errors } = await openGate(page, releaseGateOn({ clusters: GATE_CLUSTERS_7 }))
    const clusters = sectionFrame(page, 'gate-clusters', 'Failure cluster share')
    await expectDrawn(clusters, 'gate-clusters')
    await expect(section(page, 'gate-clusters').locator('[data-chart-choice="ranked-bar"]')).toHaveCount(1)
    await expect(section(page, 'gate-clusters').locator('[data-chart-offers-pie="false"]')).toHaveCount(1)
    await expect(section(page, 'gate-clusters').locator('[data-chart-choice="donut"]')).toHaveCount(0)
    await expectNoTextEscapes(page, 'Release gate with seven clusters at 1280')
    await networkQuiet(page, api)
    expectInventory(api, errors, inventoryOn(COMPARED), 'Release gate, seven clusters')
  })

  test('a run with no release: the project’s five newest releases, and one sentence saying so', async ({ page }) => {
    const { api, errors } = await openGate(page, releaseGateOn({ releaseId: null }))
    await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), 'gate-releases')
    await expect(section(page, 'gate-releases').locator('[data-gate-note="not-attributed"]')).toBeVisible()
    // No stored cluster: no share chart, and no empty frame for it.
    await expect(section(page, 'gate-clusters')).toHaveCount(0)
    await networkQuiet(page, api)
    // 2026.06 has no runs (`test_run_count: 0`) and "next" is undated: the five newest others.
    const others = [RELEASE_ID.current, ...COMPARED.slice(1)]
    expectInventory(api, errors, inventoryOn(others), 'Release gate, run without a release')
  })

  test('only one release: one sentence, no frame, and no chart-data request at all', async ({ page }) => {
    const only = RELEASES.filter((r) => r.id === RELEASE_ID.current)
    const { api, errors } = await openGate(page, releaseGateOn({ releases: only }))
    const releases = section(page, 'gate-releases')
    await expect(releases).toHaveAttribute('data-gate-releases', 'too-few', { timeout: 20_000 })
    await expect(releases.locator('[data-gate-note="too-few"]')).toContainText('needs at least two releases')
    await expect(releases.locator('[data-chart-frame]')).toHaveCount(0)
    await networkQuiet(page, api)
    expect(requestsTo(api, CHART_DATA_PATH)).toEqual([])
    expectInventory(api, errors, inventoryOn(null), 'Release gate, one release')
  })

  for (const [name, options, expected] of [
    ['a NO_GO beside a release at 100%', { go: false, releaseRate: 100 }, { recommendation: /NO.GO/i, ring: '60' }],
    ['a GO beside releases at 0%', { go: true, releaseRate: 0 }, { recommendation: /\bGO\b/, ring: '12' }],
  ] as const) {
    test(`verdict integrity: ${name}: the card and the ring are the same DOM before the Context group arrives and after it draws`, async ({
      page,
    }) => {
      const handlers = releaseGateOn({ clusters: GATE_CLUSTERS, ...options })
      // Held: the group's chunk is asked for and not answered, so the page draws only its stand-in.
      const chunk = await holdContextChunk(page)
      const { api, errors } = await openGate(page, handlers)
      await expect.poll(() => chunk.asked).toBeGreaterThan(0)
      await expect(page.locator('[data-gate-context-pending]')).toBeVisible()
      await expect(page.locator('[data-catalogue-section]')).toHaveCount(0)
      const before = await verdict(page)
      expect(before.ring).toBe(expected.ring)
      expect(before.text).toMatch(expected.recommendation)

      chunk.release()
      await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), 'gate-releases')
      await expectDrawn(sectionFrame(page, 'gate-clusters', 'Failure cluster share'), 'gate-clusters')
      // The chart really shows the contrasting release: the run's own at 100% (or 0%) every day.
      const rate = options.releaseRate === 100 ? '100.0' : '0.0'
      await expect(sectionFrame(page, 'gate-releases', 'Pass rate by release').locator('[data-chart-summary]')).toContainText(
        `2026.09: min ${rate}%`,
      )
      const after = await verdict(page)
      expect(after.ring, 'the ring reads the stored score').toBe(before.ring)
      expect(after.text, 'the card says the same thing').toBe(before.text)
      expect(after.markup, 'the recommendation card is the same DOM').toBe(before.markup)
      await networkQuiet(page, api)
      assertClean(api.unhandled, errors)
    })
  }

  test('the Context group is the same markup for a NO_GO and a GO: nothing in it follows the verdict', async ({ page }) => {
    const markupFor = async (go: boolean) => {
      const { api, errors } = await openGate(page, releaseGateOn({ clusters: GATE_CLUSTERS, go }))
      await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), `releases (go: ${go})`)
      await expectDrawn(sectionFrame(page, 'gate-clusters', 'Failure cluster share'), `clusters (go: ${go})`)
      await networkQuiet(page, api)
      assertClean(api.unhandled, errors)
      return normalisedMarkup(section(page, 'gate-context'))
    }
    const noGo = await markupFor(false)
    const go = await markupFor(true)
    expect(go, 'the Context group changed with the verdict').toBe(noGo)
  })
})

function assertClean(unhandled: string[], errors: string[]) {
  expect(unhandled, 'API requests with no fixture (fail closed)').toEqual([])
  expect(errors, 'uncaught page errors').toEqual([])
}

// 400 px tall, not SHORT_VIEWPORT's 600: the UX redesign P2 moved the
// "Release decision flow" timeline from above the comparison to a collapsed
// section at the bottom, so at 600 px the comparison is already near at load
// (measured: mounted at 600 and 500; 292 px below the fold at 400). The proof
// needs it beyond the near margin, which a 400 px screen still gives.
test.describe('Release gate, a short screen (1280 x 400)', () => {
  test.use({ viewport: { width: 1280, height: 400 }, timezoneId: 'UTC', locale: 'en-US', reducedMotion: 'reduce' })

  test('lazy: neither the run nor chart-data is asked until the comparison is near, and both before it is visible', async ({
    page,
  }) => {
    const { api, errors } = await openGate(page, releaseGateOn({ clusters: GATE_CLUSTERS }))
    const asked = () => requestsTo(api, `/api/v1/runs/${RUN_ID}`).length + requestsTo(api, CHART_DATA_PATH).length
    await proveLazyMount(page, api, { label: 'gate-releases', section: 'gate-releases', asked, before: 0 })
    await expect.poll(asked).toBe(2)
    await expectDrawn(sectionFrame(page, 'gate-releases', 'Pass rate by release'), 'gate-releases')
    await networkQuiet(page, api)
    expectInventory(api, errors, inventoryOn(COMPARED), 'Release gate, scrolled to the comparison')
  })
})
