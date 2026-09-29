/**
 * The BEFORE/AFTER visual baselines of the PRODUCTION pages (Wave 2.5,
 * VIZ-104): what every `tests/visual/prod-*.visual.spec.ts` shares.
 *
 * It mirrors `openReportRoute` in `tests/ci-e2e/report-context.spec.ts`
 * (seeded session, seeded project, every API call answered at the network
 * edge by `page.route`) with three deliberate differences:
 *
 * 1. It FAILS CLOSED. That helper answers an unknown endpoint with `{}`,
 *    which turns a missing fixture into an empty page that still
 *    screenshots green. Here an `/api` (or `/ws`, `/webhooks`) request with
 *    no handler is aborted and recorded, and `assertHermetic()` fails the spec
 *    naming it. Only a short whitelist has a default answer: `auth/me`,
 *    `feature-flags/*` (every flag off), the observability beacon (204)
 *    and `*\/count` badges. A request for any host other than the dev
 *    server's `127.0.0.1` is aborted and fails the spec too (air-gapped).
 * 2. The CLOCK is fixed. `Date` returns `NOW` for the whole test, so a
 *    "refreshed HH:MM:SS", a relative day label or a "today" outline is the
 *    same on every run; the fixtures are built from the same `NOW`.
 * 3. It captures REGIONS, not pages: one soft `toHaveScreenshot` per
 *    region, so an unrelated edit to a page's table never fails the gate,
 *    and the AFTER diff of a region shows only what moved inside it.
 * 4. Nothing scrolls, and partial raster is off (`VIEWPORT`, `RASTER_ARGS`):
 *    both were measured to make a Linux render differ from the next one.
 *
 * The locators here are the single place to adapt after the migration
 * (`cardByHeading`, `frameByHeading`, `waitForCharts`); a region's NAME
 * never changes, so the AFTER PNG replaces the BEFORE one under the same
 * path.
 */
import { expect, type Locator, type Page, type Route } from '@playwright/test'

/** The dev server of `playwright.ci.config.ts` (reused by the visual config). */
const DEV_HOST = '127.0.0.1'

export type Theme = 'signal' | 'lab'
/** `signal` is the default dark theme; `lab` the only light one (store/themeStore.ts). */
export const THEMES: readonly Theme[] = ['signal', 'lab']

/**
 * The viewport: the gallery's 1280 px width (the layout breakpoint every
 * region was designed at), but tall enough that no region needs a scroll,
 * so a scroll offset is never one of the inputs of a baseline.
 * `visualRegion` fails a region that would need one.
 */
export const VIEWPORT = { width: 1280, height: 2400 } as const

/**
 * `--disable-partial-raster`: measured in the Linux container (the CI
 * image), two renders of the same regions differed in two to four PNGs of
 * 54 per pair, always by one unit of one channel on the anti-aliased
 * rounded corners of a card, at identical layout positions, and never the
 * same regions twice. Chromium re-rasterises only the invalidated part of a
 * tile when something repaints late, and a corner cut by that partial
 * rect is anti-aliased slightly differently from one rasterised whole.
 * With the flag, 6 renders of all 54 were byte-identical. It changes how
 * tiles are scheduled, not what is drawn.
 */
const RASTER_ARGS = ['--disable-partial-raster']

/** Options every production visual spec pins (`test.use(PINNED)`). */
export const PINNED = {
  viewport: { ...VIEWPORT },
  timezoneId: 'UTC',
  locale: 'en-US',
  reducedMotion: 'reduce' as const,
  colorScheme: 'dark' as const,
  launchOptions: { args: RASTER_ARGS },
}

export interface SessionSeed {
  theme: Theme
  user: Record<string, unknown>
  projectId: string
  /** The stored global window (`testlookup-time-window`); pages may reset it. */
  days?: number
}

/**
 * Local storage as a signed-in user with one active project would have it,
 * written before any app script runs. The theme key is the zustand `persist`
 * record of `store/themeStore.ts` (`name: 'testlookup-theme'`); `?theme=`
 * works on the dev gallery only.
 */
export async function seedSession(page: Page, seed: SessionSeed) {
  await page.addInitScript(
    ({ user, projectId, days, theme }) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({ state: { token: 'access', refreshToken: 'refresh', user, isAuthenticated: true }, version: 0 }),
      )
      localStorage.setItem('testlookup-active-project', JSON.stringify({ state: { activeProjectId: projectId }, version: 0 }))
      localStorage.setItem('testlookup-time-window', JSON.stringify({ state: { days }, version: 3 }))
      localStorage.setItem('testlookup-theme', JSON.stringify({ state: { theme }, version: 0 }))
    },
    { user: seed.user, projectId: seed.projectId, days: seed.days ?? 30, theme: seed.theme },
  )
}

/**
 * Pin `Date` to `now` for the whole test. `install` fakes the timers from
 * before the first script; `setFixedTime` then stops `Date` from flowing
 * while every timer (React Query, debounces) keeps running in real time.
 */
export async function freezeClock(page: Page, now: Date) {
  await page.clock.install({ time: now })
  await page.clock.setFixedTime(now)
}

/**
 * Motion off: `prefers-reduced-motion: reduce` (the kit's `useChartAnimation`
 * honours it) on top of the config's `animations: 'disabled'`. The context
 * option in `PINNED` already sets it; this re-asserts it for a page opened
 * some other way.
 */
export async function stillMotion(page: Page) {
  await page.emulateMedia({ reducedMotion: 'reduce' })
}

// ── The fail-closed router ─────────────────────────────────────────────────

export interface ApiRequest {
  url: URL
  path: string
  method: string
  route: Route
}

/** An explicit response: a status other than 200, or no body (`respond`). */
export class ApiResponse {
  constructor(
    readonly status: number,
    readonly json?: unknown,
  ) {}
}

/** Mark an explicit response (status other than 200, or no body). */
export const respond = (status: number, json?: unknown) => new ApiResponse(status, json)

/** A handler's answer: a JSON body (status 200), or an `ApiResponse`. */
export type ApiHandler = (request: ApiRequest) => unknown

/**
 * `[matcher, handler, method?]`, first match wins. A string matches the
 * path exactly; the method defaults to GET, so a write the page was never
 * meant to make is unmocked (and fails the spec) rather than answered with
 * the read's payload.
 */
export type ApiHandlers = ReadonlyArray<readonly [string | RegExp, ApiHandler, string?]>

/** The whitelist of 3.1.1: the only paths with a default answer. */
const WHITELIST: ApiHandlers = [
  [/^\/api\/v1\/feature-flags\/[^/]+\/status$/, ({ path }) => ({ key: path.split('/')[4], enabled: false })],
  [/^\/api\/v1\/feature-flags(\/.*)?$/, () => []],
  [/^\/api\/v1\/observability(\/.*)?$/, () => respond(204), 'POST'],
  [/\/count$/, () => ({ count: 0, unread: 0 })],
]

const API_PATH = /^\/(api|ws|webhooks)(\/|$)/

export interface MockedApi {
  /** Every `/api` request with no handler, as `METHOD path?query`. */
  unhandled: string[]
  /** Every request for a host other than the dev server. */
  offHost: string[]
  /** Every handled request, as `METHOD path?query` (for debugging a fixture). */
  seen: string[]
}

/**
 * Answer the page's API calls from `handlers` (then `me`, then the
 * whitelist); abort and record everything else. Call before `goto`.
 */
export async function mockApi(page: Page, me: unknown, handlers: ApiHandlers): Promise<MockedApi> {
  const state: MockedApi = { unhandled: [], offHost: [], seen: [] }
  const all: ApiHandlers = [...handlers, ['/api/v1/auth/me', () => me], ...WHITELIST]
  // Only what could leave the machine or reach the API is intercepted; the
  // dev server's own module requests (and Vite's HMR socket on `/`, which
  // reloads the page if it is cut) go straight through.
  const intercepted = (url: URL) => url.hostname !== DEV_HOST || API_PATH.test(url.pathname)
  // An app socket (`/ws/live/...`) never reaches the dev server's proxy
  // either: one with nothing behind it retries on a timer, which is a race.
  await page.routeWebSocket(intercepted, (ws) => {
    const url = new URL(ws.url())
    if (url.hostname !== DEV_HOST) state.offHost.push(ws.url())
    else state.unhandled.push(`WS ${url.pathname}`)
    void ws.close()
  })
  await page.route((url) => url.protocol.startsWith('http') && intercepted(url), async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.hostname !== DEV_HOST) {
      state.offHost.push(request.url())
      return route.abort('blockedbyclient')
    }
    const label = `${request.method()} ${url.pathname}${url.search}`
    const method = request.method()
    const hit = all.find(
      ([matcher, , verb = 'GET']) =>
        verb === method && (typeof matcher === 'string' ? matcher === url.pathname : matcher.test(url.pathname)),
    )
    if (!hit) {
      state.unhandled.push(label)
      return route.abort('blockedbyclient')
    }
    state.seen.push(label)
    const answer = await hit[1]({ url, path: url.pathname, method, route })
    if (answer instanceof ApiResponse) {
      const { status, json } = answer
      return route.fulfill({
        status,
        contentType: 'application/json',
        body: json === undefined ? '' : JSON.stringify(json),
      })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer ?? null) })
  })
  return state
}

/** Uncaught page errors (a crash renders an error boundary, not the region). */
export function watchPageErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  return errors
}

/** The fail-closed assertions, run at the end of every test. */
export function assertHermetic(api: MockedApi, errors: string[]) {
  expect(api.unhandled, 'API requests with no fixture (fail closed)').toEqual([])
  expect(api.offHost, `requests that left ${DEV_HOST}`).toEqual([])
  expect(errors, 'uncaught page errors').toEqual([])
}

// ── Opening a page ─────────────────────────────────────────────────────────

export interface OpenOptions extends SessionSeed {
  now: Date
  me: unknown
  handlers: ApiHandlers
  /** A heading every drawn state of the page shows, awaited after `goto`. */
  ready: Locator | ((page: Page) => Locator)
}

export interface OpenedPage {
  api: MockedApi
  errors: string[]
}

/**
 * Seed, freeze, mock, navigate, and prove the route really rendered (a 404
 * or an auth bounce lands elsewhere: the spec fails, it never skips).
 */
export async function openProductionPage(page: Page, path: string, options: OpenOptions): Promise<OpenedPage> {
  const errors = watchPageErrors(page)
  await seedSession(page, options)
  await freezeClock(page, options.now)
  await stillMotion(page)
  const api = await mockApi(page, options.me, options.handlers)
  await page.goto(path)
  const expected = new URL(path, 'http://x').pathname
  expect(new URL(page.url()).pathname, 'the route redirected').toBe(expected)
  await expect(page.locator('html')).toHaveAttribute('data-theme', options.theme)
  const ready = typeof options.ready === 'function' ? options.ready(page) : options.ready
  await expect(ready).toBeVisible({ timeout: 20_000 })
  return { api, errors }
}

// ── Locating regions ───────────────────────────────────────────────────────

/** A `<section aria-label>` landmark the page already has. */
export const landmark = (page: Page | Locator, name: string): Locator =>
  page.locator(`section[aria-label="${name}"]`)

/**
 * The card around a heading: the nearest ancestor that is a card
 * (`.card`, a `CardShell`'s `rounded-xl` box, or a `<section>`). A chart
 * whose heading moved into a kit frame is found with `frameByHeading`.
 */
export function cardByHeading(scope: Page | Locator, name: string | RegExp, level?: number): Locator {
  return cardAround(scope.getByRole('heading', { name, exact: typeof name === 'string' ? true : undefined, level }))
}

/**
 * A kit chart frame by its title. Since Wave 2.5 a migrated chart is no
 * longer drawn inside a page card: the `ChartFrame` root
 * (`[data-chart-frame]`, `rounded-lg`) IS the card, and its title is the
 * heading, so `cardByHeading` would climb past it to the page's column.
 * The region is the whole frame (title, toolbar, plot, legend, notes), the
 * same extent the page card had before.
 */
export function frameByHeading(scope: Page, name: string | RegExp): Locator {
  return scope
    .locator('[data-chart-frame]')
    .filter({ has: scope.getByRole('heading', { name, exact: typeof name === 'string' ? true : undefined }) })
}

const hasClass = (name: string) => `contains(concat(" ", normalize-space(@class), " "), " ${name} ")`

/** The nearest card (`.card`, a `rounded-xl` box, or a `<section>`) around `inner`. */
export const cardAround = (inner: Locator): Locator =>
  inner.locator(`xpath=ancestor::*[self::section or ${hasClass('card')} or ${hasClass('rounded-xl')}][1]`)

/** The nearest ancestor of `inner` carrying the class `name` (e.g. a KPI `grid`). */
export const ancestorWithClass = (inner: Locator, name: string): Locator =>
  inner.locator(`xpath=ancestor::*[${hasClass(name)}][1]`)

/**
 * Wait until every Recharts chart inside `region` has laid out and drawn
 * (a zero-size container never draws: that fails here, it is not a timeout
 * on a blank screenshot). `count` is how many charts the region must have.
 */
export async function waitForCharts(region: Locator, count = 1) {
  await expect(region.locator('.recharts-wrapper')).toHaveCount(count)
  for (let i = 0; i < count; i++) {
    await expect(region.locator('.recharts-wrapper').nth(i).locator('svg.recharts-surface path').first()).toBeVisible()
  }
}

/** How long the page must go without a DOM change to count as settled. */
const QUIET_MS = 500
/** Give up (and fail) if the page never goes quiet that long. */
const QUIET_TIMEOUT_MS = 15_000

/**
 * Wait until the page has made no DOM change for `QUIET_MS`. A page renders
 * in several passes as its requests land, and Recharts lays a chart out
 * more than once (it measures its tick labels, then thins them): in the
 * Linux container a "Run history" chart was once captured between two of
 * those passes, with a grid line for every tick, because the intermediate
 * frame held still long enough for `toHaveScreenshot`'s two matching
 * captures. The page's own timers still run (the clock only pins `Date`).
 */
export async function settle(page: Page) {
  const quiet = await page.evaluate(
    ({ quietMs, timeoutMs }) =>
      new Promise<boolean>((resolve) => {
        let timer = setTimeout(() => finish(true), quietMs)
        const deadline = setTimeout(() => finish(false), timeoutMs)
        const observer = new MutationObserver(() => {
          clearTimeout(timer)
          timer = setTimeout(() => finish(true), quietMs)
        })
        function finish(ok: boolean) {
          clearTimeout(timer)
          clearTimeout(deadline)
          observer.disconnect()
          resolve(ok)
        }
        observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true })
      }),
    { quietMs: QUIET_MS, timeoutMs: QUIET_TIMEOUT_MS },
  )
  expect(quiet, `the page never went ${QUIET_MS} ms without a DOM change`).toBe(true)
}

/**
 * One soft screenshot of one region: `production/<name>--<theme>.png`
 * (`snapshotPathTemplate` puts it under `__screenshots__/<platform>/`).
 * The region must exist exactly once, be visible, and lie wholly inside the
 * tall `VIEWPORT` with nothing scrolled (see there for why a scrolled
 * capture is not reproducible).
 */
export async function visualRegion(page: Page, name: string, theme: Theme, region: Locator) {
  await expect(region, `region ${name}`).toHaveCount(1)
  await expect(region).toBeVisible()
  const box = await region.boundingBox()
  expect(box, `region ${name} has a box`).not.toBeNull()
  if (box) {
    expect(box.y, `region ${name} starts inside the viewport`).toBeGreaterThanOrEqual(0)
    expect(box.y + box.height, `region ${name} ends inside the ${VIEWPORT.height} px viewport`).toBeLessThanOrEqual(
      VIEWPORT.height,
    )
  }
  // Nothing hovered: the pointer rests off every region.
  await page.mouse.move(0, 0)
  await settle(page)
  await expect.soft(region, `region ${name} (${theme})`).toHaveScreenshot(['production', `${name}--${theme}.png`])
}
