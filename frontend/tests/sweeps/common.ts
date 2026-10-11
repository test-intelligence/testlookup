/**
 * Shared plumbing for the route and action sweeps (owner request 2026-10-10:
 * every page navigation tested for 500; every UI action tested for UI and
 * backend errors).
 *
 * The sweeps run against a LIVE deployment. They sign in with tokens minted
 * inside the backend container (`backend/scripts/sweep_fixtures.py`), never a
 * typed password, and read real ids for parameterised routes from the same
 * fixtures file:
 *
 *   SWEEP_BASE_URL=http://testlookup.local SWEEP_FIXTURES=/tmp/sweep-fixtures.json \
 *     npx playwright test -c playwright.sweep.config.ts
 *
 * Without SWEEP_FIXTURES every sweep test is skipped.
 */
import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { BrowserContext, ConsoleMessage, Page, Response } from '@playwright/test'

import { concreteRoutes } from './routes'

interface Fixtures {
  ids: Record<string, string | null>
  tokens: Record<string, string>
}

const FIXTURES_PATH = process.env.SWEEP_FIXTURES
export const HAVE_FIXTURES = !!FIXTURES_PATH && existsSync(FIXTURES_PATH)
const FIXTURES: Fixtures = HAVE_FIXTURES
  ? JSON.parse(readFileSync(FIXTURES_PATH as string, 'utf8'))
  : { ids: {}, tokens: {} }

export const BASE = process.env.SWEEP_BASE_URL ?? 'http://localhost:5173'
export const ROLES = (process.env.SWEEP_ROLES ?? Object.keys(FIXTURES.tokens).join(',')).split(',').filter(Boolean)
export const IDS = FIXTURES.ids
export const ROUTES = concreteRoutes(IDS)

// Not under test-results/: Playwright empties that folder at the start of
// every run, so a route sweep's findings vanished when the action sweep ran.
const OUT_DIR = resolve(process.env.SWEEP_OUT ?? 'sweep-results')

const users: Record<string, Record<string, unknown>> = {}

async function userFor(role: string): Promise<Record<string, unknown>> {
  if (!users[role]) {
    const r = await fetch(`${BASE}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${FIXTURES.tokens[role]}` } })
    if (!r.ok) throw new Error(`the ${role} token was refused (HTTP ${r.status}): mint fresh fixtures`)
    users[role] = (await r.json()) as Record<string, unknown>
  }
  return users[role]
}

/** Signed in as `role` on the fixtures' project before any app code runs. */
export async function signIn(ctx: BrowserContext, role: string): Promise<void> {
  const user = await userFor(role)
  await ctx.addInitScript(
    ([token, u, project]) => {
      localStorage.setItem('auth-storage', JSON.stringify({
        state: {
          token, refreshToken: null, user: u, isAuthenticated: true, refreshRequiresReauth: false,
          refreshError: null, refreshRetryAt: null, refreshFailureCount: 0, refreshRetryExhausted: false,
        },
        version: 0,
      }))
      localStorage.setItem('testlookup-active-project', JSON.stringify({
        state: { activeProjectId: project, activeProject: null }, version: 0,
      }))
    },
    [FIXTURES.tokens[role], user, IDS.project] as const,
  )
}

export interface Errors {
  /** Hard failures: a sweep test fails on any of these. */
  server5xx: string[]
  pageErrors: string[]
  errorText: string[]
  /** Recorded for triage: a 4xx can be a designed answer ("not configured"). */
  api4xx: string[]
  consoleErrors: string[]
  toasts: string[]
}

const ERROR_TEXT = /Something went wrong|Unexpected Application Error|Request failed with status code 5\d\d|Internal Server Error/i
const BAD_TEXT = /\bNaN\b|\[object Object\]|Invalid Date/
const TOAST_ERROR = /fail|error|could not|couldn.t|unable|denied|status code|not allowed|forbidden/i

/** Collects every error signal while attached. */
export function watch(page: Page): { errors: Errors; stop: () => void } {
  const errors: Errors = { server5xx: [], pageErrors: [], errorText: [], api4xx: [], consoleErrors: [], toasts: [] }
  const onResponse = (r: Response) => {
    const u = new URL(r.url())
    if (!u.pathname.startsWith('/api/')) return
    const line = `${r.status()} ${r.request().method()} ${u.pathname}`
    if (r.status() >= 500) errors.server5xx.push(line)
    else if (r.status() >= 400 && r.status() !== 401) errors.api4xx.push(line)
  }
  const onConsole = (m: ConsoleMessage) => {
    if (m.type() === 'error') errors.consoleErrors.push(m.text().slice(0, 300))
  }
  const onPageError = (e: Error) => errors.pageErrors.push(`${e.name}: ${e.message}`.slice(0, 300))
  page.on('response', onResponse)
  page.on('console', onConsole)
  page.on('pageerror', onPageError)
  return {
    errors,
    stop: () => {
      page.off('response', onResponse)
      page.off('console', onConsole)
      page.off('pageerror', onPageError)
    },
  }
}

/** Error toasts and error text visible on the page right now. */
export async function scrape(page: Page, errors: Errors): Promise<void> {
  // The AI review banner is a role=status notice, not a toast.
  const toasts = await page
    .locator('[role="status"]:not([data-testid="review-banner"]), [role="alert"]')
    .allInnerTexts()
    .catch(() => [] as string[])
  for (const t of toasts) {
    const s = t.trim().replace(/\s+/g, ' ')
    if (s && TOAST_ERROR.test(s) && !errors.toasts.includes(s)) errors.toasts.push(s.slice(0, 200))
  }
  const body = await page.locator('body').innerText().catch(() => '')
  const hard = body.match(ERROR_TEXT)
  if (hard && !errors.errorText.includes(hard[0])) errors.errorText.push(hard[0])
  const bad = body.match(BAD_TEXT)
  if (bad && !errors.errorText.includes(`bad text: ${bad[0]}`)) errors.errorText.push(`bad text: ${bad[0]}`)
}

export async function settle(page: Page, ms = 12_000): Promise<void> {
  await page.waitForLoadState('networkidle', { timeout: ms }).catch(() => undefined)
  await page.waitForTimeout(700)
}

export function hardFailures(e: Errors): string[] {
  return [...e.server5xx, ...e.pageErrors, ...e.errorText]
}

export function record(file: string, row: Record<string, unknown>): void {
  mkdirSync(OUT_DIR, { recursive: true })
  appendFileSync(resolve(OUT_DIR, file), JSON.stringify(row) + '\n')
}
