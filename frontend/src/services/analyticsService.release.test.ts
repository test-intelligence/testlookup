/**
 * NFR1 at the wire, for the release axis.
 *
 * The guarantee is that omitting a release produces the request this endpoint
 * produced before the release axis existed — not a request with a null or empty
 * `release_id` hanging off it. That matters beyond tidiness: the backend's
 * matching helper appends no SQL fragment at all rather than emitting an
 * `IS NULL OR` predicate, precisely so the planner keeps using
 * `ix_test_runs_project_release_created`. If this side started sending
 * `release_id=` on every call, the backend would begin taking the release
 * branch for every caller and the index would stop being used — a performance
 * regression with no visible symptom and no failing test.
 *
 * The hook-level tests mock this whole module, so nothing there exercises the
 * param builder. This file is the only place that does.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

// Typed with the arguments `getData` actually receives — an untyped
// `vi.fn(async () => ({}))` infers a zero-arg call signature, and indexing
// `calls[n][1]` for the config is then a tuple error rather than a read.
const getData = vi.hoisted(() =>
  vi.fn(async (_url: string, _config?: { params?: Record<string, unknown> }) => ({})),
)
vi.mock('./http', () => ({ getData, postData: vi.fn(), putData: vi.fn(), deleteData: vi.fn() }))

import { analyticsService } from './analyticsService'

const PROJECT = 'aaaaaaaa-0000-0000-0000-000000000001'
const RELEASE = 'rrrrrrrr-0000-0000-0000-000000000001'

/** The params the last call actually put on the wire. */
function lastParams(): Record<string, unknown> {
  const calls = getData.mock.calls
  return calls[calls.length - 1]?.[1]?.params ?? {}
}

/** Every method that carries the release axis, with a no-release invocation and
 *  a with-release one. Kept as a table so a newly wired method is one line. */
const METHODS: Array<{
  name: string
  without: () => unknown
  withExplicitNull: () => unknown
  with: () => unknown
}> = [
  {
    name: 'getFlakyTests',
    without: () => analyticsService.getFlakyTests(PROJECT, 30),
    withExplicitNull: () => analyticsService.getFlakyTests(PROJECT, 30, null, null),
    with: () => analyticsService.getFlakyTests(PROJECT, 30, null, RELEASE),
  },
  {
    name: 'getFailureCategories',
    without: () => analyticsService.getFailureCategories(PROJECT, 30),
    withExplicitNull: () => analyticsService.getFailureCategories(PROJECT, 30, null, null),
    with: () => analyticsService.getFailureCategories(PROJECT, 30, null, RELEASE),
  },
  {
    name: 'getTopFailing',
    without: () => analyticsService.getTopFailing(PROJECT, 30),
    withExplicitNull: () => analyticsService.getTopFailing(PROJECT, 30, null, null),
    with: () => analyticsService.getTopFailing(PROJECT, 30, null, RELEASE),
  },
  {
    name: 'getCoverage',
    without: () => analyticsService.getCoverage(PROJECT, 30),
    withExplicitNull: () => analyticsService.getCoverage(PROJECT, 30, null, null),
    with: () => analyticsService.getCoverage(PROJECT, 30, null, RELEASE),
  },
  {
    name: 'getSuiteDetail',
    without: () => analyticsService.getSuiteDetail(PROJECT, 'Checkout', 30),
    withExplicitNull: () => analyticsService.getSuiteDetail(PROJECT, 'Checkout', 30, null),
    with: () => analyticsService.getSuiteDetail(PROJECT, 'Checkout', 30, RELEASE),
  },
]

describe('analyticsService release scoping', () => {
  beforeEach(() => {
    getData.mockClear()
  })

  // A loop that iterates an empty or short table asserts nothing while
  // reporting success, so the length is pinned separately.
  it('covers every release-capable method', () => {
    expect(METHODS).toHaveLength(5)
  })

  for (const method of METHODS) {
    it(`${method.name} omits release_id when none is given`, async () => {
      await method.without()

      const params = lastParams()
      // `not.toHaveProperty` rather than a falsy check: `release_id: undefined`
      // would satisfy "falsy" while still being a key axios could serialise.
      expect(params).not.toHaveProperty('release_id')
    })

    it(`${method.name} omits release_id when passed an explicit null`, async () => {
      // The case that matters in production and that the `without` variant
      // above cannot reach: EVERY hook passes the release argument explicitly,
      // and `useReleaseScope` returns `null` rather than `undefined` when no
      // release applies. A builder keyed on `!== undefined` would therefore
      // pass the omitted-argument test and still send `release_id=null` on
      // every unfiltered request in the running app — which the backend
      // answers 422, and 422s toast, so every page would toast and render
      // empty.
      await method.withExplicitNull()

      expect(lastParams()).not.toHaveProperty('release_id')
    })

    it(`${method.name} sends release_id when given`, async () => {
      await method.with()

      expect(lastParams().release_id).toBe(RELEASE)
    })
  }

  it('keeps the rest of the params untouched when scoping by release', async () => {
    await analyticsService.getCoverage(PROJECT, 7, 'Checkout', RELEASE)

    const params = lastParams()
    // The release axis is additive. If adding it displaced the project or the
    // window, the response would be scoped by something the caller never asked
    // for — and every one of these endpoints is tenant-scoped by project_id.
    expect(params).toMatchObject({
      project_id: PROJECT,
      days: 7,
      suite_name: 'Checkout',
      release_id: RELEASE,
    })
  })
})
