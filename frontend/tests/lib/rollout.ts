/**
 * What the Wave 2.6 rollout specs share (`tests/ci-e2e/rollout-*.spec.ts`,
 * plan 5.3): opening a report page with the catalogue flags ON through the
 * fail-closed harness of the visual baselines (`production-pages.ts`), the
 * request log, the lazy-mount proof, and the geometry the responsive and
 * presentation specs measure.
 *
 * Everything fails closed, as the harness does: an unmocked request fails the
 * test by name, a request that leaves the dev server fails it, and nothing is
 * skipped. A flag is ON only when a spec lists it (`FlagMap`).
 */
import { expect, type Locator, type Page } from '@playwright/test'
import {
  assertHermetic,
  openProductionPage,
  type ApiHandlers,
  type FlagMap,
  type MockedApi,
  type OpenedPage,
  type Theme,
} from './production-pages'
import { textEscapes } from './chart-text-escapes'
import { validateAnyChartSeries, validateEnvelopeMeta } from '../../src/lib/viz/contracts'
import { NOW, PROJECT_ID, USER } from '../visual/production/fixtures'

export { assertHermetic }

/** The app shell's scroller (`AppLayout.tsx`): the page scrolls here, never the window. */
export const MAIN = '#main-content'

/** The lazy sections' margin ahead of the scroller's box (`NEAR_VIEWPORT_MARGIN_PX`). */
export const NEAR_MARGIN_PX = 200

export interface RolloutOpen {
  handlers: ApiHandlers
  flags?: FlagMap
  ready: (page: Page) => Locator
  theme?: Theme
  /** The stored global window (`testlookup-time-window`), default 30. */
  days?: number
}

/** Seed, pin the clock, mock (fail closed), open `path`, wait for `ready`. */
export function openRollout(page: Page, path: string, options: RolloutOpen): Promise<OpenedPage> {
  return openProductionPage(page, path, {
    theme: options.theme ?? 'signal',
    now: NOW,
    me: USER,
    user: USER,
    projectId: PROJECT_ID,
    days: options.days,
    handlers: options.handlers,
    ready: options.ready,
    mock: { flags: options.flags ?? {} },
  })
}

// ── The request log ────────────────────────────────────────────────────────

/**
 * Telemetry is not part of a page's load (the beacon flushes on a timer and
 * on page hide): answered, never inventoried.
 */
const NOT_INVENTORIED = /^POST \/api\/v1\/observability\//

/** Every request the page made, as `METHOD path?query`, telemetry excluded. */
export const observed = (api: MockedApi): string[] => api.seen.filter((line) => !NOT_INVENTORIED.test(line))

/** The lines of `observed` whose path is `path` (a string: exact; a RegExp: tested on the path). */
export function requestsTo(api: MockedApi, path: string | RegExp): string[] {
  return observed(api).filter((line) => {
    const target = line.slice(line.indexOf(' ') + 1).split('?')[0]
    return typeof path === 'string' ? target === path : path.test(target)
  })
}

const NETWORK_QUIET_MS = 1_500
const NETWORK_QUIET_TIMEOUT_MS = 15_000

/** Wait until no request has arrived for 1.5 s (fails if the page never stops asking). */
export async function networkQuiet(page: Page, api: MockedApi) {
  const deadline = Date.now() + NETWORK_QUIET_TIMEOUT_MS
  let count = api.seen.length + api.unhandled.length
  let since = Date.now()
  while (Date.now() - since < NETWORK_QUIET_MS) {
    expect(Date.now(), `the page kept requesting for ${NETWORK_QUIET_TIMEOUT_MS} ms`).toBeLessThan(deadline)
    await page.waitForTimeout(100)
    const now = api.seen.length + api.unhandled.length
    if (now !== count) {
      count = now
      since = Date.now()
    }
  }
}

const P = PROJECT_ID

/**
 * What the app shell asks for on every report route, whatever the flags:
 * the flag-off shell of `rollout-flag-off.spec.ts` (session and project list
 * twice, three shell flags, badges, notifications, the release picker, the
 * AI settings). A page whose flag-off path Phase D deleted (Overview, S1)
 * no longer asks the seam, so its inventory starts here.
 */
export const SHELL_BASE = [
  'GET /api/v1/auth/me',
  'GET /api/v1/auth/me',
  `GET /api/v1/feature-flags/ask_ai_chat/status?project_id=${P}`,
  `GET /api/v1/feature-flags/manual_upload/status?project_id=${P}`,
  `GET /api/v1/feature-flags/viz_multi_filters/status?project_id=${P}`,
  'GET /api/v1/me/assigned-failures/count',
  'GET /api/v1/notifications/history/unread-count',
  'GET /api/v1/notifications/history?unread_only=false&limit=50',
  'GET /api/v1/projects',
  'GET /api/v1/projects',
  `GET /api/v1/releases?project_id=${P}`,
  'GET /api/v1/settings/ai',
]

/**
 * The shell on a report route that still asks the seam (catalogue flag ON):
 * `SHELL_BASE` plus the seam's own lookup, `viz_chart_data_api`, once.
 */
export const SHELL_ON = [...SHELL_BASE, `GET /api/v1/feature-flags/viz_chart_data_api/status?project_id=${P}`]

/**
 * The page's requests (telemetry excluded) must equal `expected` as a
 * multiset: the same lines, the same number of times, in any order. Fails
 * closed first (an unmocked or off-host request fails by name).
 */
export function expectInventory(api: MockedApi, errors: string[], expected: readonly string[], what: string) {
  const seen = observed(api)
  if (process.env.ROLLOUT_INVENTORY_PRINT) console.log(`INVENTORY ${what}: ${JSON.stringify([...seen].sort(), null, 2)}`)
  assertHermetic(api, errors)
  expect([...seen].sort(), `${what}: the requests of one load`).toEqual([...expected].sort())
}

/** Every `days=` value on the wire, per request line (lines without one are left out). */
export function daysOnTheWire(api: MockedApi): { line: string; days: number }[] {
  return observed(api).flatMap((line) => {
    const query = line.split('?')[1]
    if (!query) return []
    return new URLSearchParams(query).getAll('days').map((value) => ({ line, days: Number(value) }))
  })
}

// ── Frames ─────────────────────────────────────────────────────────────────

/** The catalogue section with this id (`data-catalogue-section`). */
export const section = (page: Page, id: string): Locator => page.locator(`[data-catalogue-section="${id}"]`)

/** The kit frame inside a section, by its title (the frame's heading). */
export function sectionFrame(page: Page, id: string, title: string | RegExp): Locator {
  // `has` is resolved INSIDE each frame, so the inner locator starts at the page.
  return section(page, id)
    .locator('[data-chart-frame]')
    .filter({ has: page.getByRole('heading', { name: title, exact: typeof title === 'string' ? true : undefined }) })
}

/** The states a frame is DRAWN in (anything else is a skeleton, an empty or an error state). */
export const DRAWN = /^(ready|truncated)$/

/** A frame reached a drawn state (`data-chart-state`), with its plot on screen. */
export async function expectDrawn(frame: Locator, what: string) {
  await expect(frame, `${what}: one frame`).toHaveCount(1)
  await expect(frame, `${what}: drawn, not an error or empty state`).toHaveAttribute('data-chart-state', DRAWN, {
    timeout: 20_000,
  })
  await expect(frame.locator('[data-chart-body]')).toBeVisible()
}

/** Every chart frame on the page, and the state each is in (for a failure message). */
export async function frameStates(page: Page): Promise<string[]> {
  return page.locator('[data-chart-frame]').evaluateAll((frames) =>
    frames.map((frame) => {
      const title = frame.querySelector('h1,h2,h3,h4')?.textContent?.trim() ?? '(untitled)'
      return `${title}: ${frame.getAttribute('data-chart-state')}`
    }),
  )
}

/**
 * No chart text drawn outside its frame, cut off by its svg, or wider than
 * its box, in any frame on the page (`textEscapes`: painted geometry, not
 * `textContent`). Plan 5.7: on Linux fonts (DejaVu, wider than Windows') at
 * every width the specs visit.
 */
export async function expectNoTextEscapes(page: Page, where: string) {
  const escapes: string[] = []
  for (const frame of await page.locator('[data-chart-frame]').all()) {
    await frame.scrollIntoViewIfNeeded()
    const title = (await frame.locator('h2, h3').first().textContent())?.trim()
    escapes.push(...(await frame.evaluate(textEscapes)).map((problem) => `${title}: ${problem}`))
  }
  expect(escapes, `${where}: chart text escaping its frame`).toEqual([])
}

/** No frame on the page is in an error state (a bad fixture or a broken section fails here by name). */
export async function expectNoErrorFrame(page: Page) {
  const states = await frameStates(page)
  expect(states.filter((state) => /: (error|forbidden)$/.test(state)), 'frames in an error state').toEqual([])
}

/**
 * An element's markup with the per-render ids taken out (React `useId`
 * values, Recharts' clip-path counters), so two loads of the same state
 * compare equal and anything else that differs is a real difference.
 */
export async function normalisedMarkup(target: Locator): Promise<string> {
  const html = await target.evaluate((element) => element.outerHTML)
  return html
    .replace(/«[^»]*»/g, '«id»')
    .replace(/:r[0-9a-z]+:/g, ':id:')
    .replace(/recharts\d+-/g, 'recharts-')
}

// ── The lazy-mount proof (plan 2.6) ────────────────────────────────────────

/** Where an element is relative to the scroller's visible box, px (top edge, from the scroller's bottom). */
export async function belowMainFold(page: Page, target: Locator): Promise<number> {
  const box = await target.boundingBox()
  const main = await page.locator(MAIN).boundingBox()
  expect(box, 'the target has a box').not.toBeNull()
  expect(main, 'the scroller has a box').not.toBeNull()
  return (box as { y: number }).y - ((main as { y: number; height: number }).y + (main as { height: number }).height)
}

/**
 * Scroll `#main-content` (never the window: the shell does not scroll it) so
 * that `target`'s top edge sits `below` px under the scroller's visible
 * bottom edge: out of sight, but inside the lazy sections' 200 px margin.
 */
export async function scrollMainUntilBelowFold(page: Page, target: Locator, below: number): Promise<number> {
  const offset = await belowMainFold(page, target)
  // The target may be replaced the moment it is near, so the scroll is
  // checked on the scroller (it moved by exactly what was asked, not clamped
  // at the end of the page), never by measuring the target again.
  const moved = await page.locator(MAIN).evaluate((main, by) => {
    const before = main.scrollTop
    main.scrollTop = before + by
    return main.scrollTop - before
  }, offset - below)
  expect(Math.abs(moved - (offset - below)), `scrolled ${MAIN} by ${offset - below} px`).toBeLessThan(2)
  return moved
}

/** A viewport short enough that every lazy section starts well below the scroller's fold. */
export const SHORT_VIEWPORT = { width: 1280, height: 600 } as const

export interface LazyProof {
  /** The placeholder's `data-lazy-section`. */
  label: string
  /** The `data-catalogue-section` that replaces it. */
  section: string
  /** How many of the section's requests have been made so far. */
  asked: () => number
  /** How many before the section is near (a page may make the same request for itself). */
  before: number
}

/**
 * The lazy-mount proof of plan 2.6, in a real browser, on the real shell:
 *
 *   1. at load the section is a placeholder more than the 200 px margin below
 *      the scroller's fold, and its requests have NOT been made;
 *   2. `#main-content` is scrolled until the placeholder's top is 100 px
 *      below the fold: out of sight, inside the margin;
 *   3. the requests go out, and when they do the section is STILL below the
 *      fold (it mounted before the reader could see a skeleton).
 *
 * With the observer rooted at the viewport instead of the scroller (`root:
 * null`), the target is clipped by the scroller before the margin applies,
 * so step 3 never happens until the section scrolls into view: the proof
 * fails (mutation M-L1).
 */
export async function proveLazyMount(page: Page, api: MockedApi, proof: LazyProof) {
  await networkQuiet(page, api)
  const placeholder = page.locator(`[data-lazy-section="${proof.label}"]`)
  await expect(placeholder, `${proof.label}: a placeholder at load`).toHaveCount(1)
  await expect(placeholder).toHaveAttribute('aria-hidden', 'true')
  await expect(placeholder.getByRole('heading')).toHaveCount(0)
  await expect(section(page, proof.section), `${proof.section}: not mounted at load`).toHaveCount(0)
  const distance = await belowMainFold(page, placeholder)
  expect(distance, `${proof.label} must start beyond the margin for the proof to mean anything`).toBeGreaterThan(
    NEAR_MARGIN_PX + 50,
  )
  expect(proof.asked(), `${proof.label}: requests made before it was near`).toBe(proof.before)

  await scrollMainUntilBelowFold(page, placeholder, 100)
  await expect.poll(proof.asked, { message: `${proof.label}: no request once it was 100 px below the fold`, timeout: 5_000 }).toBeGreaterThan(
    proof.before,
  )
  await expect(section(page, proof.section).first()).toBeAttached()
  expect(await belowMainFold(page, section(page, proof.section).first()), `${proof.section} was already visible`).toBeGreaterThan(0)
}

// ── Geometry (plan 3.4: measured on the scroller, never on `document`) ─────

export interface Overflow {
  main: { scrollWidth: number; clientWidth: number }
  document: { scrollWidth: number; innerWidth: number }
  /** Frames whose box pokes outside the scroller's box, px. */
  framesOutside: string[]
}

/**
 * Horizontal overflow of the scroller, of the document, and of every chart
 * frame against the scroller's box. The shell is `h-screen overflow-hidden`,
 * so a page wider than the screen widens `#main-content`'s scroll box and
 * never the document: measuring only the document would pass a broken page.
 */
export async function measureOverflow(page: Page): Promise<Overflow> {
  return page.evaluate((selector) => {
    const main = document.querySelector(selector) as HTMLElement
    const mainBox = main.getBoundingClientRect()
    const framesOutside: string[] = []
    for (const frame of Array.from(document.querySelectorAll('[data-chart-frame]'))) {
      const box = frame.getBoundingClientRect()
      if (box.width === 0) continue
      if (box.left < mainBox.left - 0.5 || box.right > mainBox.right + 0.5) {
        const title = frame.querySelector('h1,h2,h3,h4')?.textContent?.trim() ?? '(untitled)'
        framesOutside.push(`${title}: ${Math.round(box.left)}..${Math.round(box.right)} vs ${Math.round(mainBox.left)}..${Math.round(mainBox.right)}`)
      }
    }
    return {
      main: { scrollWidth: main.scrollWidth, clientWidth: main.clientWidth },
      document: { scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth },
      framesOutside,
    }
  }, MAIN)
}

/** The three overflow assertions of plan 3.4. */
export async function expectNoHorizontalOverflow(page: Page, where: string) {
  const o = await measureOverflow(page)
  expect(o.main.scrollWidth, `${where}: ${MAIN} scrolls sideways`).toBeLessThanOrEqual(o.main.clientWidth)
  expect(o.document.scrollWidth, `${where}: the document scrolls sideways`).toBeLessThanOrEqual(o.document.innerWidth)
  expect(o.framesOutside, `${where}: chart frames outside ${MAIN}`).toEqual([])
}

// ── Wave 3: the advanced sections (both flags) ─────────────────────────────

/**
 * The shell with BOTH flags on: `SHELL_ON` plus the second seam lookup,
 * `viz_advanced_charts`, asked once the catalogue flag is on (plan 2.4).
 */
export const SHELL_ADVANCED = [...SHELL_ON, `GET /api/v1/feature-flags/viz_advanced_charts/status?project_id=${P}`]

/** The sections' unfiltered "ever had a run?" probe (`useEverHadRun`): no `days`, no filter. */
export const RUN_PROBE = `GET /api/v1/runs?project_id=${P}&page=1&size=1`

/**
 * The console errors a DEV server may print for a Wave 3 page: React's
 * development warning about mixed shorthand / longhand style properties,
 * logged at error level by the dev build only. (ECharts' dev-only "Component
 * toolbox is used but not imported" is no longer tolerated: the scatter engine
 * drops the brush's synthetic toolbox, I-NOTE-4.) Anything else fails the spec.
 */
const DEV_ONLY_CONSOLE_ERRORS = [/^%s a style property during rerender/]

/** The browser's own line for a response the harness answered with an error ON PURPOSE (a planted 4xx / 5xx). */
const PLANTED_ERROR_STATUS = /^Failed to load resource: the server responded with a status of [45]\d\d/

/** Console errors the page printed, except the dev-only ones above and planted error statuses. */
export function watchConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    if (DEV_ONLY_CONSOLE_ERRORS.some((re) => re.test(text)) || PLANTED_ERROR_STATUS.test(text)) return
    errors.push(text.slice(0, 500))
  })
  return errors
}

/**
 * Bring every lazy section near (scrolling each placeholder into view until
 * none is left), then wait for the network to go quiet: for a load whose
 * inventory must include every section's requests.
 */
export async function mountEverySection(page: Page, api: MockedApi, { rounds = 16, stepTimeoutMs = 2_000 } = {}) {
  await networkQuiet(page, api)
  for (let round = 0; round < rounds; round++) {
    // Re-read by ATTRIBUTE every round, and every scroll bounded (R2-B F-16). A
    // section mounting replaces its placeholder (a composite's placeholder
    // becomes the section's own, `failures-scatter` -> `scatter-project`), so
    // a locator held from `.all()` (by position) can point at nothing, and
    // Playwright then waits for it with no limit: at an 800 px viewport, where
    // sections mount one at a time, the helper hung on Failures.
    const labels = await page
      .locator('[data-lazy-section]')
      .evaluateAll((elements) => elements.map((element) => element.getAttribute('data-lazy-section') ?? ''))
    if (labels.length === 0) break
    for (const label of labels) {
      const placeholder = page.locator(`[data-lazy-section="${label}"]`).first()
      if ((await placeholder.count()) === 0) continue
      await placeholder.scrollIntoViewIfNeeded({ timeout: stepTimeoutMs }).catch(() => undefined)
    }
    await networkQuiet(page, api)
  }
  await expect(page.locator('[data-lazy-section]'), 'a lazy section never mounted').toHaveCount(0)
}

/** Scroll a lazy section near: its placeholder if it is still one, then the section itself. */
export async function bringNear(page: Page, id: string) {
  const placeholder = page.locator(`[data-lazy-section="${id}"]`)
  if ((await placeholder.count()) > 0) await placeholder.first().scrollIntoViewIfNeeded()
  await section(page, id).first().scrollIntoViewIfNeeded()
}

/** The frame inside a Wave 3 section, drawn (scrolled near first). */
export async function expectSectionDrawn(page: Page, id: string, title: string | RegExp) {
  await section(page, id).first().scrollIntoViewIfNeeded()
  await expectDrawn(sectionFrame(page, id, title), id)
}

/** The open rows panels (`RowsPanel` in a `SidePanel`). */
export const rowsPanels = (page: Page): Locator =>
  page.locator('[data-side-panel]').filter({ has: page.getByRole('heading', { name: /^Executions in / }) })

/**
 * Exactly one rows panel is open (several hosts share the page's one `rows`
 * key: only the opener may answer it), titled for `title`, and its first
 * page is drawn. Returns it.
 */
export async function expectOneRowsPanel(page: Page, title: string | RegExp): Promise<Locator> {
  const panels = rowsPanels(page)
  await expect(panels, 'one rows panel: never two hosts answering one rows= selection').toHaveCount(1)
  const heading = typeof title === 'string' ? `Executions in ${title}` : title
  await expect(panels.getByRole('heading', { name: heading })).toBeVisible()
  await expect(panels.locator('[data-rows-state]')).toHaveAttribute('data-rows-state', /^(ready|empty)$/, { timeout: 15_000 })
  return panels
}

/** The parsed queries of the requests to `path` (optionally only those `filter` keeps). */
export function queryOf(api: MockedApi, path: string, filter: (q: URLSearchParams) => boolean = () => true): URLSearchParams[] {
  return requestsTo(api, path)
    .map((line) => new URLSearchParams(line.split('?')[1] ?? ''))
    .filter(filter)
}

/**
 * The hostile names reached the page as TEXT: no element was made from the
 * markup one (`<img src=x onerror=...>`) and its handler never ran.
 */
export async function expectHostileAsText(page: Page, scope: Locator, where: string) {
  await expect(scope.locator('img[src="x"]'), `${where}: an element made from a hostile name`).toHaveCount(0)
  expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss), `${where}: a hostile name ran`).toBeUndefined()
}

// ── Wave 3 live probes (`tests/probe-w3-*.spec.ts`) ────────────────────────

/**
 * The live probes' environment. They run by hand against a deployment after
 * it is rolled out (`npx playwright test --config probe-live.config.ts
 * probe-w3`), never in CI: without `PROBE_W3=1` every probe SKIPS (and says
 * how to run it). Credentials come ONLY from the environment; a probe with
 * `PROBE_W3=1` and no credentials FAILS with the instructions.
 */
export interface ProbeEnv {
  base: string
  user: string
  pass: string
  /** The project to probe (`PROBE_PROJECT_ID`); default: the session's active project after sign-in. */
  projectId: string | null
}

export const PROBE_HOWTO =
  'Set PROBE_W3=1, PROBE_BASE_URL (default http://testlookup.local), PROBE_USER and PROBE_PASS (a read-only ' +
  'account), optionally PROBE_PROJECT_ID; turn viz_chart_data_api AND viz_advanced_charts on for that project; then ' +
  '`npx playwright test --config probe-live.config.ts probe-w3`.'

/** `null` when the probes are not asked for (the caller skips). */
export function probeEnv(): ProbeEnv | null {
  if (process.env.PROBE_W3 !== '1') return null
  const user = process.env.PROBE_USER ?? ''
  const pass = process.env.PROBE_PASS ?? ''
  expect(user && pass, `PROBE_USER / PROBE_PASS are not set. ${PROBE_HOWTO}`).toBeTruthy()
  return { base: process.env.PROBE_BASE_URL ?? 'http://testlookup.local', user, pass, projectId: process.env.PROBE_PROJECT_ID ?? null }
}

/** Sign in through the login form; returns the project the probes read (env, else the active one). */
export async function probeSignIn(page: Page, env: ProbeEnv): Promise<string> {
  await page.goto(`${env.base}/login`, { waitUntil: 'domcontentloaded' })
  await page.locator('input').first().fill(env.user)
  await page.locator('input[type="password"]').fill(env.pass)
  await page.getByRole('button', { name: /sign in|log in|login/i }).first().click()
  await page.waitForURL((u) => !u.pathname.includes('/login'), { timeout: 30_000 })
  if (env.projectId) {
    await page.evaluate((id) => {
      localStorage.setItem('testlookup-active-project', JSON.stringify({ state: { activeProjectId: id }, version: 0 }))
    }, env.projectId)
    return env.projectId
  }
  const stored = await page.evaluate(() => localStorage.getItem('testlookup-active-project'))
  const id = stored ? (JSON.parse(stored) as { state?: { activeProjectId?: string } }).state?.activeProjectId : undefined
  expect(id && id !== 'all', `pick one project (PROBE_PROJECT_ID). ${PROBE_HOWTO}`).toBeTruthy()
  return id as string
}

/** A read-only GET through the signed-in session (the app's bearer token), as `{status, body}`. */
export async function probeGet(page: Page, path: string): Promise<{ status: number; body: unknown }> {
  return page.evaluate(async (url) => {
    const raw = localStorage.getItem('auth-storage')
    const token = raw ? (JSON.parse(raw) as { state?: { token?: string } }).state?.token : undefined
    const response = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    const text = await response.text()
    let body: unknown = text
    try {
      body = JSON.parse(text)
    } catch {
      // not JSON: kept as text for the message
    }
    return { status: response.status, body }
  }, path)
}

/** Both Wave 3 flags are ON for the project, or the probe FAILS saying how to turn them on (never skips). */
export async function expectProbeFlagsOn(page: Page, projectId: string) {
  for (const key of ['viz_chart_data_api', 'viz_advanced_charts']) {
    const { status, body } = await probeGet(page, `/api/v1/feature-flags/${key}/status?project_id=${projectId}`)
    expect(status, `${key} status`).toBe(200)
    expect((body as { enabled?: boolean }).enabled, `${key} is OFF for project ${projectId}: the probe tests nothing. ${PROBE_HOWTO}`).toBe(
      true,
    )
  }
}

/**
 * A live analytics body is the WIRE shape (`with_meta`: the C3 keys at the
 * top level, `meta` beside them) of the one `kind` its route answers, and the
 * client's own validators accept it (`validateAnyChartSeries` +
 * `validateEnvelopeMeta`). FK0 finding 1 was exactly a hermetic fixture that
 * did not look like this.
 */
export function expectWireChart(body: unknown, kind: string, where: string) {
  expect(body && typeof body === 'object' && !Array.isArray(body), `${where}: a JSON object`).toBe(true)
  const { meta, ...series } = body as Record<string, unknown>
  expect(series.kind, `${where}: the series' kind at the top level`).toBe(kind)
  const checkedMeta = validateEnvelopeMeta(meta)
  expect(checkedMeta.ok ? [] : checkedMeta.errors, `${where}: meta is a C2 envelope`).toEqual([])
  const checked = validateAnyChartSeries(series)
  expect(checked.ok ? [] : checked.errors, `${where}: a C3 ${kind}`).toEqual([])
}

/**
 * From a focused chart, press ArrowRight until the highlighted mark offers an
 * action (live data has empty cells, which are not marks), at most `limit` times.
 */
export async function walkToMark(page: Page, sectionId: string, limit = 60) {
  const actions = section(page, sectionId).locator('[data-mark-intent]')
  for (let i = 0; i < limit; i++) {
    await page.keyboard.press('ArrowRight')
    if (await actions.first().isVisible()) return
  }
  throw new Error(`${sectionId}: no mark with an action in the first ${limit} steps`)
}

/** Every response of the page to an `/api` path matching `path`, captured with its JSON body. */
export function captureResponses(page: Page, path: RegExp): { url: string; status: number; body: unknown }[] {
  const seen: { url: string; status: number; body: unknown }[] = []
  page.on('response', async (response) => {
    const url = new URL(response.url())
    if (!path.test(url.pathname)) return
    const body: unknown = await response.json().catch(() => null)
    seen.push({ url: `${url.pathname}${url.search}`, status: response.status(), body })
  })
  return seen
}

/** `Object.prototype`'s own properties in a clean page (Chromium). */
const OBJECT_PROTOTYPE_KEYS = [
  '__defineGetter__',
  '__defineSetter__',
  '__lookupGetter__',
  '__lookupSetter__',
  '__proto__',
  'constructor',
  'hasOwnProperty',
  'isPrototypeOf',
  'propertyIsEnumerable',
  'toLocaleString',
  'toString',
  'valueOf',
]

/**
 * No name reached a lookup that wrote through `__proto__`: `Object.prototype`
 * has exactly its own keys. Found in Wave 3: a test NAMED `__proto__` drawn as
 * canvas text goes through zrender's text-width LRU (`core/LRU.js`, a plain-
 * object map), whose `get('__proto__')` relinks `Object.prototype` into its
 * list and gives EVERY object `prev` / `next` (axe then throws
 * "Getter must be a function").
 */
export async function expectNoPrototypePollution(page: Page, where: string) {
  const extra = await page.evaluate(
    (known) => Object.getOwnPropertyNames(Object.prototype).filter((key) => !known.includes(key)),
    OBJECT_PROTOTYPE_KEYS,
  )
  expect(extra, `${where}: Object.prototype gained properties (prototype pollution from a hostile name)`).toEqual([])
}
