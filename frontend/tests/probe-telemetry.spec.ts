/**
 * F-007 settle probe — does the frontend telemetry beacon actually fail?
 *
 * The exploratory walk recorded `requestfailed` on
 * `POST /api/v1/observability/frontend` for 17 of 18 pages. That walk called
 * `page.goto()` per route with no dwell, so the only flush that could have run
 * was the `pagehide` one — a beacon issued while the document was tearing down.
 * That is a plausible measurement artifact, so the finding was parked as
 * NEEDS CONFIRMATION rather than filed.
 *
 * This probe separates the two explanations by measuring three things on the
 * SAME page load:
 *
 *   A. DWELL   — sit on one page past the 5 s batch window, so the timer flush
 *                fires while the document is alive and fully attached.
 *   B. EXPLICIT— call navigator.sendBeacon() from page context ourselves and
 *                record its boolean return (false = the UA refused to queue it).
 *   C. UNLOAD  — then navigate away, and see whether the failure only appears
 *                at that point.
 *
 * If A and B succeed and only C fails, F-007 is an artifact of the probe and
 * gets closed. If A or B fails, telemetry is genuinely broken on this
 * deployment and it becomes a real finding.
 *
 * Reports only — not a pass/fail gate. Run:
 *   npx playwright test --config=probe-live.config.ts tests/probe-telemetry.spec.ts
 */
import { test, expect } from '@playwright/test'

const BASE = 'http://testlookup.local'
const PROJECT_ID = '2aefa4fa-8917-448b-9895-9cd41911ef95'
const PROJECT_NAME = 'Checkout Service'
const TELEMETRY = '/api/v1/observability/frontend'

test('telemetry beacon: dwell, explicit send, then unload', async ({ page, request }) => {
  const login = await request.post(`${BASE}/api/v1/auth/login`, {
    form: { username: 'admin', password: 'Admin@2026!' },
  })
  expect(login.ok(), 'login must succeed').toBeTruthy()
  const auth = await login.json()

  await page.addInitScript(
    ({ token, projectId, projectName }) => {
      localStorage.setItem(
        'auth-storage',
        JSON.stringify({
          state: { token, user: { username: 'admin', role: 'ADMIN' }, isAuthenticated: true },
          version: 0,
        }),
      )
      localStorage.setItem(
        'testlookup-active-project',
        JSON.stringify({ state: { activeProjectId: projectId, activeProjectName: projectName }, version: 0 }),
      )
    },
    { token: auth.access_token, projectId: PROJECT_ID, projectName: PROJECT_NAME },
  )

  // ── record every telemetry request and how it ended ──────────────────────
  type Event = { phase: string; outcome: string; detail: string }
  const events: Event[] = []
  let phase = 'A-dwell'

  page.on('requestfailed', (req) => {
    if (req.url().includes(TELEMETRY)) {
      events.push({ phase, outcome: 'FAILED', detail: req.failure()?.errorText ?? 'unknown' })
    }
  })
  page.on('response', (res) => {
    if (res.url().includes(TELEMETRY)) {
      events.push({ phase, outcome: 'RESPONSE', detail: String(res.status()) })
    }
  })

  // ── A. dwell past the 5 s batch window ──────────────────────────────────
  await page.goto(`${BASE}/overview`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(12_000)

  // Did the SPA have anything to flush? A page with no errors still reports
  // Web Vitals, so an empty buffer here is itself worth knowing.
  const spontaneous = events.length

  // ── B. explicit beacon from page context ────────────────────────────────
  phase = 'B-explicit'
  const beaconQueued = await page.evaluate(async (path) => {
    const body = JSON.stringify({
      errors: [],
      vitals: [{ name: 'LCP', value: 1234.5, rating: 'good', url: location.href, delta: 1234.5 }],
    })
    const blob = new Blob([body], { type: 'application/json' })
    return navigator.sendBeacon(path, blob)
  }, TELEMETRY)
  await page.waitForTimeout(3_000)

  // ── C. navigate away — the unload flush the original walk measured ──────
  phase = 'C-unload'
  await page.goto(`${BASE}/runs`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3_000)

  const byPhase = (p: string) => events.filter((e) => e.phase === p)
  const summarise = (p: string) =>
    byPhase(p)
      .map((e) => `${e.outcome}(${e.detail})`)
      .join(', ') || '(nothing)'

  console.log('\n──────── F-007 telemetry beacon ────────')
  console.log(`A dwell 12s on /overview : ${summarise('A-dwell')}`)
  console.log(`  spontaneous flushes    : ${spontaneous}`)
  console.log(`B explicit sendBeacon    : queued=${beaconQueued} -> ${summarise('B-explicit')}`)
  console.log(`C navigate away (unload) : ${summarise('C-unload')}`)
  console.log('────────────────────────────────────────\n')
})

/**
 * The quota measurement that settling F-007 turned up.
 *
 * Reading the code while the beacon was known-working raised a different
 * question: `flush()` empties the buffer before sending and ignores what
 * `sendBeacon` returns. Does that return value ever go false in practice?
 *
 * It does, well below any payload the server minds — which is why the fix
 * chunks batches to fit. Kept as a live check: if a future UA moves the quota,
 * this reports the new number instead of the fix quietly becoming wrong.
 */
test('sendBeacon quota: where does the UA start refusing?', async ({ page }) => {
  const network: string[] = []
  page.on('response', (r) => {
    if (r.url().includes(TELEMETRY)) network.push(`RESP ${r.status()}`)
  })
  page.on('requestfailed', (r) => {
    if (r.url().includes(TELEMETRY)) network.push(`FAIL ${r.failure()?.errorText}`)
  })

  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })

  const measured = await page.evaluate(async (path) => {
    // An error-boundary report of realistic size: 4 KB stack + 2 KB component
    // stack, matching what the reporter actually captures.
    const batchOf = (n: number) =>
      JSON.stringify({
        errors: Array.from({ length: n }, (_, i) => ({
          type: 'boundary',
          message: `boom ${i}`,
          stack: 'x'.repeat(4000),
          component_stack: 'y'.repeat(2000),
          url: location.href,
          user_agent: navigator.userAgent,
          timestamp: new Date().toISOString(),
        })),
        vitals: [],
      })

    const rows: Array<{ errors: number; bytes: number; queued: boolean }> = []
    for (const n of [1, 5, 10, 20]) {
      const body = batchOf(n)
      const queued = navigator.sendBeacon(path, new Blob([body], { type: 'application/json' }))
      rows.push({ errors: n, bytes: body.length, queued })
    }

    // Prove the server is not the constraint: the largest refused body over
    // an ordinary fetch.
    const largest = batchOf(20)
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: largest,
    })
    return { rows, fetchStatus: res.status, fetchBytes: largest.length }
  }, TELEMETRY)

  await page.waitForTimeout(2_000)

  console.log('\n──────── sendBeacon quota ────────')
  for (const r of measured.rows) {
    console.log(
      `  ${String(r.errors).padStart(2)} errors  ${String(r.bytes).padStart(7)} bytes  ` +
        `queued=${r.queued ? 'true' : 'FALSE'}`,
    )
  }
  console.log(`  same ${measured.fetchBytes}-byte body over fetch -> ${measured.fetchStatus}`)
  console.log(`  network: ${network.join(' | ')}`)
  console.log('──────────────────────────────────\n')

  // The server must accept what the beacon refuses — that asymmetry is the
  // whole point. If this ever fails, the problem moved to the backend.
  expect(measured.fetchStatus).toBe(202)
})
