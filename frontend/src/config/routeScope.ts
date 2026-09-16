/**
 * Route → scope registry.
 *
 * Some pages cannot render their content unless exactly one project is pinned.
 * That is a legitimate design choice — API keys, integrations and retention
 * policies are per-project settings, and Flaky Coach analyses one project's
 * test history — but it collides with the app's DEFAULT state.
 *
 * `projectStore` defaults `activeProjectId` to `ALL_PROJECTS_ID`, and its own
 * comment explains why: before 2026-05-15 the default was `null`, and every
 * page gating on `!project && !isAllProjects` (Overview, Runs, Failures, Deep
 * Investigation) was unusable for a first-time user. That fix was right for
 * those pages. But six OTHER pages gate on the opposite condition —
 * `if (isAllProjects)` — so the default state is precisely the one they refuse
 * to render in, and every one of them tells the reader to go and use the top
 * bar.
 *
 * Reported as: from `/overview` with no project chosen, the "Flaky tests" KPI
 * card offers "Open flaky coach", and the destination shows nothing.
 *
 * Why a registry rather than fixing the one card
 * ----------------------------------------------
 * The knowledge "this destination needs a single project" lived only inside the
 * destination, so every LINK to it had to remember independently — and
 * `OverviewPage` did not. A registry moves that knowledge somewhere a link can
 * ask, and `ScopedLink` is the thing that asks. `routeScope.ratchet.test.ts`
 * then holds the registry to the code: a new project-gated page fails the suite
 * until it is declared here.
 *
 * Adding a route here does NOT gate it. It declares what is already true of the
 * page, so links and tests can reason about it.
 */
import { ALL_PROJECTS_ID } from '@/store/projectStore'

export type RouteScope =
  /** Needs exactly one project pinned; renders a picker prompt otherwise. */
  | 'single-project'
  /** Works in All Projects mode (and in every other scope). */
  | 'any'

/**
 * Routes whose page renders a "select a project" prompt INSTEAD of its content
 * while All Projects is active.
 *
 * Membership is not a judgement about what these pages ought to do — it is a
 * statement of what they currently do, verified by the ratchet test. Pages that
 * merely degrade (hiding one panel, disabling one action) are deliberately NOT
 * here: they still render, so a link to them is not a broken promise.
 */
const SINGLE_PROJECT_ROUTES: ReadonlySet<string> = new Set([
  '/activity',
  '/agents/workflows',
  '/flaky-coach',
  '/reviews',
  '/settings/api-keys',
  '/settings/github',
  '/settings/gitlab',
  '/settings/webhooks',
  '/settings/retention',
])

/** The declared routes, for tests and for anyone auditing the list. */
export const singleProjectRoutes = (): string[] =>
  [...SINGLE_PROJECT_ROUTES].sort()

/**
 * Reduce a `to` value to the path the registry is keyed on.
 *
 * Call sites pass real link targets, which carry query strings and hashes
 * (`/test-management?tab=Test+Suites`) and are sometimes written without a
 * leading slash. Comparing those raw against the registry would silently miss,
 * and a scope check that silently misses is worse than none — it reads as
 * "reachable".
 */
export function normalizeRoutePath(to: string): string {
  const withoutFragment = to.split(/[?#]/)[0]
  const withLeadingSlash = withoutFragment.startsWith('/')
    ? withoutFragment
    : `/${withoutFragment}`
  // Collapse a trailing slash, but never reduce the root to an empty string.
  return withLeadingSlash.length > 1
    ? withLeadingSlash.replace(/\/+$/, '')
    : withLeadingSlash
}

/** What this destination requires. Unknown routes are `'any'` — the registry
 *  declares restrictions, so absence means unrestricted. */
export function routeScope(to: string): RouteScope {
  return SINGLE_PROJECT_ROUTES.has(normalizeRoutePath(to))
    ? 'single-project'
    : 'any'
}

/**
 * Can this destination show its content under the current project selection?
 *
 * `null` counts as unreachable alongside `ALL_PROJECTS_ID`: the store promotes
 * null to the sentinel on read, but a component can observe the pre-hydration
 * value, and answering "reachable" there would produce exactly the dead end
 * this registry exists to prevent.
 */
export function isRouteReachable(
  to: string,
  activeProjectId: string | null,
): boolean {
  if (routeScope(to) === 'any') return true
  return activeProjectId !== null && activeProjectId !== ALL_PROJECTS_ID
}
