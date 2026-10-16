/**
 * Every Wave 3 hermetic fixture is a body the client accepts (plan 5,
 * "Hermetic fixtures": validated by the contract, so a bad fixture fails HERE,
 * by name, and never as an error frame in a screenshot or a rollout spec).
 *
 * The bodies are the WIRE shapes of `tests/visual/production/fixtures.ts`
 * (`wave3Bodies()`): the C3 keys at the top level and `meta` beside them, as
 * `with_meta` writes them. Each is reshaped exactly as the client's
 * `chartResponseFromEnvelope` does, then held to the client's own validators:
 * `validateAnyChartSeries` + `validateEnvelopeMeta` (= `validateAnyChartResponse`)
 * for a chart, `validateRowsResponse` for a rows page, `validateClustersResponse`
 * for the clusters list. No browser: these run in the test runner's Node.
 *
 * Also pinned here, because a fixture that drifted from the server would
 * make every rollout spec test the wrong thing: the wire shape itself (no
 * hand-wrapped `{meta, series: {...}}`, FK0 finding 1), the envelope keys the
 * routes lift into `meta` absent from the body, null-never-zero cells and
 * stats, and the sentinel / prefix ids the readers strip.
 */
import { expect, test } from '@playwright/test'
import { validateAnyChartSeries, validateEnvelopeMeta } from '../../src/lib/viz/contracts'
import { validateRowsResponse } from '../../src/components/reports/catalogue/RowsPanel.model'
import { validateClustersResponse } from '../../src/components/charts/failureGroups/systemicClusters.model'
import { parseTrendSummary } from '../../src/types/trends'
import { parseTimelineNotes } from '../../src/types/timelineNotes'
import { parseCoverageHistory, parseUntaggedTests } from '../../src/types/analytics'
import { parseTeamValueMetrics } from '../../src/types/valueMetrics'
import type { ApiRequest } from '../lib/production-pages'
import {
  COVERAGE_KEY_SEPARATOR,
  coverageHistoryBody,
  coverageMapLevel,
  daysAgo,
  failureGroupsBody,
  FAILURE_GROUPS_TOTAL,
  HOSTILE_NAMES,
  NO_MESSAGE_ID,
  PROJECT_ID,
  rowsTotal,
  SCATTER_SUITE_TESTS,
  SINGLETONS_ID,
  suiteDayMatrix,
  teamValueMetricsBody,
  testRunMatrix,
  testScatterBody,
  timelineNotesList,
  trendSummaryBody,
  UNTAGGED_TESTS,
  untaggedTestsBody,
  wave3Bodies,
} from '../visual/production/fixtures'
import { NEEDS_REVIEW, VISUAL_NOTES, WIDE_SUITES } from '../visual/production/fixtures-trends-coverage'

/** The client's reshape (`chartResponseFromEnvelope`): the wire body's keys minus `meta` ARE the series. */
function reshape(body: Record<string, unknown>): { meta: unknown; series: Record<string, unknown> } {
  const { meta, ...series } = body
  return { meta: meta ?? null, series }
}

test.describe('Wave 3 hermetic fixtures: the client accepts every body', () => {
  for (const { name, body, kind } of wave3Bodies()) {
    test(`${kind}: ${name}`, () => {
      expect(body.meta, `${name}: meta beside the body`).toBeTruthy()
      const meta = validateEnvelopeMeta(body.meta)
      expect(meta.ok ? [] : meta.errors, `${name}: meta is a C2 envelope`).toEqual([])
      if (kind === 'rows') {
        const rows = validateRowsResponse(body)
        expect(rows.ok ? [] : rows.errors, `${name}: a rows page`).toEqual([])
        return
      }
      if (kind === 'clusters') {
        const clusters = validateClustersResponse(body)
        expect(clusters.ok ? [] : clusters.errors, `${name}: the clusters list`).toEqual([])
        return
      }
      // The wire shape: the series' keys at the top level, never wrapped.
      expect(typeof body.kind, `${name}: the series' kind at the TOP level (with_meta)`).toBe('string')
      expect(
        body.series === undefined || Array.isArray(body.series),
        `${name}: not hand-wrapped as {meta, series: {...}}`,
      ).toBe(true)
      // The envelope keys a route lifts into meta are not left in the body.
      for (const key of ['truncated_axes', 'outside_window', 'definitions']) {
        expect(body, `${name}: ${key} belongs in meta`).not.toHaveProperty(key)
      }
      const { series } = reshape(body)
      const checked = validateAnyChartSeries(series)
      expect(checked.ok ? [] : checked.errors, `${name}: a C3 series`).toEqual([])
    })
  }
})

test.describe('Wave 3 fixtures say what the server says', () => {
  test('a heatmap cell nobody ran is null with n 0; one with only skips is null with its real n (never 0%)', () => {
    const matrix = suiteDayMatrix(14)
    const cells = matrix.cells
    expect(cells.some((c) => c.value === null && c.n === 0)).toBe(true)
    const skippedOnly = cells.filter((c) => c.value === null && c.n > 0)
    expect(skippedOnly.length, 'Search, 3 days ago').toBe(1)
    expect(skippedOnly[0].counts.skipped).toBe(skippedOnly[0].n)
    expect(cells.every((c) => c.value === null || (c.value >= 0 && c.value <= 100))).toBe(true)
    expect(matrix.unit).toBe('percent')
    // Keys are LOWER-cased (chart-data's suite expression); labels keep the spelling.
    expect(matrix.y_keys).toContain('legacy import')
    expect(matrix.y_labels).toContain('Legacy import')
    expect(matrix.meta.truncated_axes).toEqual({ series: { dimension: 'suite', kept: 7, total: 11 } })
    expect(matrix.x_keys[matrix.x_keys.length - 1]).toBe(daysAgo(0))
  })

  test('the test x run matrix is a status matrix: no unit, no counts, no rate basis', () => {
    const matrix = testRunMatrix(30)
    expect(matrix.value_type).toBe('status')
    expect(matrix).not.toHaveProperty('unit')
    expect(matrix.cells.every((c) => !('counts' in c))).toBe(true)
    expect(matrix.meta.pass_rate_basis).toBeNull()
    for (const name of HOSTILE_NAMES) expect(matrix.y_labels).toContain(name)
  })

  test('coverage map: prefixed ids, (ungrouped), the recency pair, null never 0', () => {
    const level1 = coverageMapLevel(30, 1, null, null)
    const nodes = level1.nodes as { id: string; label: string; stats: Record<string, unknown> }[]
    expect(nodes[0].id).toBe('all')
    expect(nodes.slice(1).every((n) => n.id.startsWith('s:'))).toBe(true)
    const never = nodes.find((n) => n.label === 'Legacy import')
    expect(never?.stats).toMatchObject({ recency: 'never', executions: 0, pass_rate: null, last_executed_at: null, staleness_days: null })
    const unknown = nodes.find((n) => n.label === 'Archived')
    expect(unknown?.stats).toMatchObject({ recency: 'unknown', last_executed_at: null })
    const idle = nodes.find((n) => n.label === 'Search')
    expect(idle?.stats).toMatchObject({ recency: 'seen', executions: 0, pass_rate: null, staleness_days: 40 })
    const level2 = coverageMapLevel(30, 2, 'auth', null)
    const classes = level2.nodes as { id: string; label: string }[]
    expect(classes[0].id).toBe('s:auth')
    expect(classes.map((n) => n.label)).toContain('(ungrouped)')
    expect(classes.map((n) => n.id)).toContain(`c:auth${COVERAGE_KEY_SEPARATOR}__none__`)
    expect(coverageMapLevel(30, 2, 'no such suite', null).nodes).toEqual([])
  })

  test('failure groups: roll-ups are objects with UPPERCASE ids, never nodes; edges only when asked', () => {
    const body = failureGroupsBody(30)
    const ids = (body.nodes as { id: string }[]).map((n) => n.id)
    expect(ids).not.toContain(NO_MESSAGE_ID)
    expect(ids).not.toContain(SINGLETONS_ID)
    expect(body.no_message.id).toBe(NO_MESSAGE_ID)
    expect(body.singletons.id).toBe(SINGLETONS_ID)
    expect(body.edges).toEqual([])
    expect(failureGroupsBody(30, { edges: true }).edges.length).toBeGreaterThan(0)
    expect(body.total_failures).toBe(FAILURE_GROUPS_TOTAL)
    for (const name of ['constructor', '__proto__']) expect(ids).toContain(name)
    for (const group of body.groups) {
      expect(group.trend.reduce((sum: number, b: { y: number }) => sum + b.y, 0), group.label).toBe(group.failure_count)
      expect(group.label.length).toBeLessThanOrEqual(160)
    }
    const empty = failureGroupsBody(30, { empty: true })
    expect(empty.total_failures).toBe(0)
    expect(empty.no_message.share_of_failures, 'a share over no failure is null, never 0').toBeNull()
  })

  test('test scatter: medians absent with no point (never 0, 0); every x above 0 on the log axis', () => {
    const body = testScatterBody(30, SCATTER_SUITE_TESTS)
    expect(body.medians).toBeDefined()
    expect(body.points.every((p) => p.x > 0 && p.n >= 1)).toBe(true)
    expect(body.points.map((p) => p.label)).toEqual(expect.arrayContaining([...HOSTILE_NAMES]))
    expect(testScatterBody(30, SCATTER_SUITE_TESTS, { allExcluded: true })).not.toHaveProperty('medians')
  })

  test('a rows page reconciles with the mark it was opened from', () => {
    const matrix = suiteDayMatrix(14)
    const y = matrix.y_keys.indexOf('payments')
    const x = matrix.x_keys.indexOf(daysAgo(1))
    const cell = matrix.cells.find((c) => c.x === x && c.y === y)
    expect(rowsTotal('executions', { suite: 'payments', day: daysAgo(1) }, 14)).toBe(cell?.n)
    const point = SCATTER_SUITE_TESTS[3]
    expect(rowsTotal('failure_rate', { test: point.id }, 30)).toBe(point.n)
  })
})

/**
 * W08: the wave's new fixture bodies, held to the CLIENT's own guards (the
 * `parse*` guards return `null` for an answer they refuse, and the page then
 * says "Not available": a fixture the guard refused would turn every spec
 * of that section into a test of the fallback).
 */
test.describe('W08 hermetic fixtures: the client accepts every new body', () => {
  const request = (query: string): ApiRequest => {
    const url = new URL(`http://127.0.0.1/fixture?${query}&project_id=${PROJECT_ID}`)
    return { url, path: url.pathname, method: 'GET', route: undefined as never }
  }

  test('trend summary (14, 30, 90 days): parseTrendSummary accepts it; the 14-day one has the fixture\'s 7-day gap', () => {
    for (const days of [14, 30, 90]) {
      const body = trendSummaryBody(days)
      expect(parseTrendSummary(body), `${days} days`).not.toBeNull()
      const meta = validateEnvelopeMeta(body.meta)
      expect(meta.ok ? [] : meta.errors).toEqual([])
    }
    const fourteen = trendSummaryBody(14)
    expect(fourteen).toMatchObject({ days_with_runs: 7, empty_days: 7, gap: { silent_days: 7, start: daysAgo(11), end: daysAgo(5), ongoing: false } })
    expect(fourteen.confidence.dimensions.map((d) => d.id)).toEqual(['data_coverage', 'sample_size', 'variance_stability', 'tag_quality'])
    expect(fourteen.untagged_share).toBe(0)
  })

  test('timeline notes (empty, and the visual variant): parseTimelineNotes keeps every note', () => {
    expect(parseTimelineNotes(timelineNotesList(request('from=2026-09-05&to=2026-09-18')))).toMatchObject({ items: [], total: 0, truncated: false })
    expect(parseTimelineNotes(timelineNotesList(request('from=2026-09-05&to=2026-09-18'), VISUAL_NOTES))?.items).toHaveLength(VISUAL_NOTES.length)
  })

  test('coverage history and untagged tests: their guards accept them', () => {
    const history = coverageHistoryBody(90)
    expect(parseCoverageHistory(history)?.points).toHaveLength(history.points.length)
    expect(history.points.length).toBeGreaterThan(80)
    const meta = validateEnvelopeMeta(history.meta)
    expect(meta.ok ? [] : meta.errors).toEqual([])
    const untagged = untaggedTestsBody(request('days=30&page=1&size=50'))
    expect(parseUntaggedTests(untagged)?.items).toHaveLength(UNTAGGED_TESTS.length)
  })

  test('value by team: the guard accepts it, and the teams plus the run-level hours add up to the headline', () => {
    const body = teamValueMetricsBody(30)
    expect(parseTeamValueMetrics(body)?.teams).toHaveLength(body.teams.length)
    const sum = body.teams.reduce((s, t) => s + t.hours_saved, 0) + body.not_attributable_hours
    expect(Math.abs(sum - body.headline_hours)).toBeLessThan(0.01)
    expect(body.teams[body.teams.length - 1].team).toBe('Unassigned')
  })

  test('the coverage map variants: needs_review_count on every node, and an Other that pages', () => {
    for (const depth of [1, 2]) {
      const body = coverageMapLevel(30, depth, depth === 1 ? null : 'payments', null, 1, { needsReview: NEEDS_REVIEW })
      const { meta, series } = reshape(body)
      const chart = validateAnyChartSeries(series)
      expect(chart.ok ? [] : chart.errors, `needs review, depth ${depth}`).toEqual([])
      expect(validateEnvelopeMeta(meta).ok).toBe(true)
      const nodes = (body as { nodes: { label: string; stats: { needs_review_count?: number } }[] }).nodes
      expect(nodes.every((n) => typeof n.stats.needs_review_count === 'number')).toBe(true)
    }
    const level1 = coverageMapLevel(30, 1, null, null, 1, { needsReview: NEEDS_REVIEW }) as { nodes: { label: string; stats: { needs_review_count?: number } }[] }
    expect(level1.nodes.find((n) => n.label === 'Payments')?.stats.needs_review_count).toBe(3)
    expect(level1.nodes[0].stats.needs_review_count).toBe(NEEDS_REVIEW.size)
    // The default map carries no such key (its regions must not move).
    const plain = coverageMapLevel(30, 1, null, null) as { nodes: { stats: Record<string, unknown> }[] }
    expect(plain.nodes.some((n) => 'needs_review_count' in n.stats)).toBe(false)

    const wide = (page: number) => coverageMapLevel(30, 1, null, null, page, { suites: WIDE_SUITES }) as { nodes: { id: string; label: string; value: number }[] }
    const [one, two, three] = [wide(1), wide(2), wide(3)]
    for (const body of [one, two, three]) {
      const chart = validateAnyChartSeries(reshape(body as unknown as Record<string, unknown>).series)
      expect(chart.ok ? [] : chart.errors).toEqual([])
    }
    expect(one.nodes).toHaveLength(1 + 498 + 1)
    expect(one.nodes[one.nodes.length - 1]).toMatchObject({ id: 'other:all', label: 'Other (5)' })
    expect(two.nodes.map((n) => n.label).slice(1)).toEqual(WIDE_SUITES.slice(498).map((s) => s.label))
    expect(three.nodes).toHaveLength(1)
    // The root (the parent's totals) is the same on every page.
    expect(two.nodes[0].value).toBe(one.nodes[0].value)
    expect(three.nodes[0].value).toBe(one.nodes[0].value)
  })
})
