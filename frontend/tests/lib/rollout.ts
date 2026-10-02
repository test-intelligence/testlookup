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
 * What the app shell asks for on every report route with the catalogue flag
 * ON: the flag-off shell of `rollout-flag-off.spec.ts` (session and project
 * list twice, three shell flags, badges, notifications, the release picker,
 * the AI settings) plus the seam's own lookup, `viz_chart_data_api`, once.
 */
export const SHELL_ON = [
  'GET /api/v1/auth/me',
  'GET /api/v1/auth/me',
  `GET /api/v1/feature-flags/ask_ai_chat/status?project_id=${P}`,
  `GET /api/v1/feature-flags/manual_upload/status?project_id=${P}`,
  `GET /api/v1/feature-flags/viz_multi_filters/status?project_id=${P}`,
  `GET /api/v1/feature-flags/viz_chart_data_api/status?project_id=${P}`,
  'GET /api/v1/me/assigned-failures/count',
  'GET /api/v1/notifications/history/unread-count',
  'GET /api/v1/notifications/history?unread_only=false&limit=50',
  'GET /api/v1/projects',
  'GET /api/v1/projects',
  `GET /api/v1/releases?project_id=${P}`,
  'GET /api/v1/settings/ai',
]

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
