/**
 * Hermetic page-load measurement of the five VIZ-408 report pages (Wave 2.6,
 * plan 4.1 A): Largest Contentful Paint, plus First Contentful Paint, the
 * JavaScript bytes and the request count of each load. Config and how to
 * run: `playwright.perf.config.ts`. NOT a CI test (plan 4.1: "CI asserts
 * structure, never milliseconds").
 *
 * What is held fixed, so that two trees can be compared:
 * - a production build served by `vite preview` (not the dev server);
 * - every API answer from the visual baselines' fail-closed fixtures
 *   (`tests/visual/production/fixtures.ts`), each after a fixed 40 ms
 *   (`LATENCY_MS`), with the fixtures' pinned clock; an unmocked request or
 *   an off-host one fails the run, as in the visual specs;
 * - Chromium throttled 4x (CDP `Emulation.setCPUThrottlingRate`), 1280 x 800,
 *   HTTP cache disabled, a fresh browser context per load;
 * - the order: one unrecorded warm-up load of every page x cell, then
 *   `LCP_RUNS` (default 9) samples of each, the page order reversed on every
 *   other sample and the cell order rotated, so a machine that drifts
 *   (thermal, background work) drifts every cell alike.
 *
 * What is NOT fixed, deliberately: motion. A user without "reduce motion"
 * sees the charts animate, so the harness does not ask for it.
 *
 * Cells (`LCP_CELLS`): comma-separated `label:flags:url`, `flags` one of
 * `off` (every flag off, the committed baselines' state) or `on`
 * (`viz_chart_data_api` and `viz_advanced_charts` on). Default: one cell,
 * `A0:off:<the config's server>`. To compare the untouched base with the
 * branch in one interleaved run, serve the base's `dist/` yourself on
 * another port and pass e.g.
 *   LCP_CELLS=A0:off:http://127.0.0.1:4181,B:off:http://127.0.0.1:4180,C:on:http://127.0.0.1:4180
 *
 * Output: one JSON line per load in `test-results/lcp/lcp-<run id>.jsonl`
 * (or `LCP_OUT`), and at the end a median table per page x cell on stdout.
 * Nothing is asserted about the numbers.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { assertHermetic, freezeClock, landmark, mockApi, seedSession, watchPageErrors, type ApiHandlers, type FlagMap } from '../lib/production-pages'
import {
  NOW,
  OVERVIEW,
  OVERVIEW_ON,
  PROJECT_ID,
  RELEASE_GATE,
  releaseGateOn,
  RUN_ID,
  SUITE,
  SUITE_DETAIL,
  SUITE_DETAIL_ON,
  SUMMARY_REPORT,
  SUMMARY_REPORT_ON,
  TRENDS,
  TRENDS_ON,
  USER,
} from '../visual/production/fixtures'

/** The fixed answer delay of every API request. */
const LATENCY_MS = 40
/** CPU slowdown (4 = a mid-range laptop's budget on a fast desktop). */
const CPU_THROTTLE = 4
/** How long after `load` (and the page's heading) LCP is read. */
const SETTLE_AFTER_LOAD_MS = 1_000
const RUNS = Number(process.env.LCP_RUNS ?? 9)
const OUT = process.env.LCP_OUT ?? `test-results/lcp/lcp-${process.env.LCP_RUN_ID ?? 'run'}.jsonl`

const FLAGS: Record<string, FlagMap> = {
  off: {},
  on: { viz_chart_data_api: true, viz_advanced_charts: true },
}

interface Cell {
  label: string
  flags: keyof typeof FLAGS
  /** Absolute origin, or '' for the config's own server. */
  origin: string
}

function parseCells(raw: string | undefined): Cell[] {
  if (!raw) return [{ label: 'A0', flags: 'off', origin: '' }]
  return raw.split(',').map((spec) => {
    const [label, flags, ...rest] = spec.trim().split(':')
    if (!label || !(flags in FLAGS)) throw new Error(`LCP_CELLS: bad cell "${spec}" (label:off|on:url)`)
    return { label, flags: flags as Cell['flags'], origin: rest.join(':') }
  })
}

const CELLS = parseCells(process.env.LCP_CELLS)

interface MeasuredPage {
  name: string
  path: string
  /** Every flag off: the committed baselines' answers. */
  handlers: ApiHandlers
  /**
   * The flags-on cells' answers: the same page plus what its catalogue
   * sections ask (the rollout specs' fixtures). With the flag-off set an `on`
   * load would fail closed on its first section request.
   */
  handlersOn: ApiHandlers
  /** Visible in every drawn state of the page: the load is not over before it. */
  ready: (page: Page) => Locator
}

const PAGES: MeasuredPage[] = [
  {
    name: 'Overview',
    path: '/overview',
    handlers: OVERVIEW,
    handlersOn: OVERVIEW_ON,
    ready: (p) => p.getByRole('heading', { level: 1, name: 'Dashboard' }),
  },
  { name: 'Trends', path: '/trends', handlers: TRENDS, handlersOn: TRENDS_ON, ready: (p) => landmark(p, 'Trend metrics') },
  {
    name: 'Summary',
    path: '/reports/summary',
    handlers: SUMMARY_REPORT,
    handlersOn: SUMMARY_REPORT_ON,
    ready: (p) => p.getByText('Total tests', { exact: true }),
  },
  {
    name: 'Suite detail',
    path: `/coverage/suite?name=${SUITE}&days=30`,
    handlers: SUITE_DETAIL,
    handlersOn: SUITE_DETAIL_ON,
    ready: (p) => p.getByRole('heading', { name: /^Run history/ }),
  },
  {
    name: 'Release gate',
    path: `/release-gate/${RUN_ID}`,
    handlers: RELEASE_GATE,
    // The same stored decision (no clusters), plus the gate's live reads.
    handlersOn: releaseGateOn(),
    ready: (p) => p.getByRole('meter', { name: 'Risk Score' }),
  },
]

/** What the page reports about its own load (read from the browser). */
interface PaintReading {
  lcpMs: number | null
  lcpElement: string | null
  lcpSize: number | null
  fcpMs: number | null
  jsEncodedBytes: number
  jsDecodedBytes: number
  jsFiles: number
  resources: number
}

/**
 * Every timing is read through `PerformanceObserver`, never through
 * `performance.getEntries*`: the pinned clock (`page.clock`, a CONTEXT init
 * script, so it runs before this page one) replaces `performance` with a fake
 * whose entry lists may be empty. The observers see every entry from the
 * first moment of the document, and entry times are the browser's own.
 */
function recordPaints() {
  const store = {
    lcp: [] as PerformanceEntry[],
    fcp: null as number | null,
    resources: [] as PerformanceEntry[],
  }
  ;(window as unknown as { __lcpHarness: unknown }).__lcpHarness = store
  new PerformanceObserver((list) => store.lcp.push(...list.getEntries())).observe({
    type: 'largest-contentful-paint',
    buffered: true,
  })
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) if (entry.name === 'first-contentful-paint') store.fcp = entry.startTime
  }).observe({ type: 'paint', buffered: true })
  new PerformanceObserver((list) => store.resources.push(...list.getEntries())).observe({
    type: 'resource',
    buffered: true,
  })
}

/** Read the recorded paints and the script resources (in the page). */
function readPaints(): PaintReading {
  type LcpEntry = PerformanceEntry & { element?: Element | null; size?: number }
  const store = (
    window as unknown as {
      __lcpHarness: { lcp: LcpEntry[]; fcp: number | null; resources: PerformanceResourceTiming[] }
    }
  ).__lcpHarness
  const last = store.lcp[store.lcp.length - 1]
  const describe = (el: Element | null | undefined): string | null => {
    if (!el) return null
    const id = el.id ? `#${el.id}` : ''
    const cls = typeof el.className === 'string' && el.className.trim() ? `.${el.className.trim().split(/\s+/).slice(0, 3).join('.')}` : ''
    const text = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40)
    return `${el.tagName.toLowerCase()}${id}${cls}${text ? ` "${text}"` : ''}`
  }
  const resources = store.resources
  const scripts = resources.filter((r) => r.initiatorType === 'script' || /\.m?js(\?|$)/.test(r.name))
  return {
    lcpMs: last ? last.startTime : null,
    lcpElement: describe(last?.element),
    lcpSize: last?.size ?? null,
    fcpMs: store.fcp,
    jsEncodedBytes: scripts.reduce((n, r) => n + r.encodedBodySize, 0),
    jsDecodedBytes: scripts.reduce((n, r) => n + r.decodedBodySize, 0),
    jsFiles: scripts.length,
    resources: resources.length,
  }
}

interface Sample extends PaintReading {
  run: number
  cell: string
  flags: string
  page: string
  requests: number
  apiRequests: number
  at: string
}

function writeSample(sample: Sample) {
  mkdirSync(dirname(OUT), { recursive: true })
  appendFileSync(OUT, `${JSON.stringify(sample)}\n`)
}

const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/** Median of every metric per page x cell, from the results file. */
function summarise(): string {
  if (!existsSync(OUT)) return 'no samples'
  const samples = readFileSync(OUT, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Sample)
  const rows = ['| Page | Cell | n | LCP p50 ms | LCP min-max | FCP p50 ms | JS KB (wire) | requests | LCP element (last run) |', '|---|---|---:|---:|---|---:|---:|---:|---|']
  for (const pg of PAGES) {
    for (const cell of CELLS) {
      const mine = samples.filter((s) => s.page === pg.name && s.cell === cell.label)
      const lcps = mine.map((s) => s.lcpMs).filter((v): v is number => v != null)
      if (mine.length === 0) continue
      const fcps = mine.map((s) => s.fcpMs).filter((v): v is number => v != null)
      rows.push(
        `| ${pg.name} | ${cell.label} | ${mine.length} | ${lcps.length ? median(lcps).toFixed(0) : '-'} | ` +
          `${lcps.length ? `${Math.min(...lcps).toFixed(0)}-${Math.max(...lcps).toFixed(0)}` : '-'} | ` +
          `${fcps.length ? median(fcps).toFixed(0) : '-'} | ${(median(mine.map((s) => s.jsEncodedBytes)) / 1024).toFixed(1)} | ` +
          `${median(mine.map((s) => s.requests))} | ${mine[mine.length - 1].lcpElement ?? '-'} |`,
      )
    }
  }
  return `${rows.join('\n')}\n(${samples.length} samples in ${OUT})`
}

test.afterAll(() => {
  console.log(`\nLCP harness, ${LATENCY_MS} ms API latency, CPU x${CPU_THROTTLE}, 1280 x 800, cache off\n${summarise()}`)
})

// Run 0 is a warm-up and is not recorded: the first load of each page after
// the server starts paid for a cold server and disk (measured: Overview's
// first A0 load was 1,904 ms against a median of 1,364 for the next eight).
for (let run = 0; run <= RUNS; run++) {
  const pages = run % 2 === 1 ? PAGES : [...PAGES].reverse()
  for (const pg of pages) {
    const shift = Math.max(run - 1, 0) % CELLS.length
    const cells = [...CELLS.slice(shift), ...CELLS.slice(0, shift)]
    for (const cell of cells) {
      test(`${run === 0 ? 'warm-up' : `run ${run}`} · ${pg.name} · ${cell.label}`, async ({ page, baseURL }) => {
        const errors = watchPageErrors(page)
        const cdp = await page.context().newCDPSession(page)
        await cdp.send('Network.enable')
        await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
        await cdp.send('Emulation.setCPUThrottlingRate', { rate: CPU_THROTTLE })

        await page.addInitScript(recordPaints)
        await seedSession(page, { theme: 'signal', user: USER, projectId: PROJECT_ID })
        await freezeClock(page, NOW)
        const handlers = cell.flags === 'on' ? pg.handlersOn : pg.handlers
        const api = await mockApi(page, USER, handlers, { flags: FLAGS[cell.flags], latencyMs: LATENCY_MS })
        let requests = 0
        page.on('request', () => {
          requests += 1
        })

        const origin = cell.origin || baseURL
        await page.goto(`${origin}${pg.path}`, { waitUntil: 'load' })
        expect(new URL(page.url()).pathname, 'the route redirected').toBe(new URL(pg.path, 'http://x').pathname)
        await expect(pg.ready(page)).toBeVisible({ timeout: 30_000 })
        await page.waitForTimeout(SETTLE_AFTER_LOAD_MS)

        const reading = await page.evaluate(readPaints)
        await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
        assertHermetic(api, errors)
        // The warm-up only warms: the very first load of a session once
        // reported no LCP entry at all (seen once in three sessions).
        if (run === 0) return
        expect(reading.lcpMs, 'the browser reported no LCP').not.toBeNull()
        writeSample({
          run,
          cell: cell.label,
          flags: cell.flags,
          page: pg.name,
          ...reading,
          requests,
          apiRequests: api.seen.length,
          at: new Date().toISOString(),
        })
      })
    }
  }
}
