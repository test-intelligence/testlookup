import { describe, expect, it } from 'vitest'
import {
  ENTITY_SCOPES, MODE_SWITCH_LINE, SCOPE_SWITCH_LINE, SEMANTIC_SCOPE_HINT, SEMANTIC_UNAVAILABLE_HINT,
  distinctMatches, errorSentence, fromSemanticResponse, matchCountLabel, modeAvailability, modeForScope,
  normalizeMode, parseMode, parseScope, relativeTime, toResultRow,
} from './searchModel'
import type { GlobalSearchResult, IndexStatus, SearchResult } from '@/types/search'

const healthy: IndexStatus = { status: 'healthy', document_count: 10, last_indexed_at: null, semantic_available: true }

describe('searchModel — modes by scope (W12 SRCH-01)', () => {
  it('the scopes are the server\'s SEARCH_SCOPES, in order', () => {
    expect(ENTITY_SCOPES).toEqual(['all', 'tests', 'runs', 'suites', 'defects', 'flaky', 'releases'])
  })

  it('offers Hybrid and Semantic unless the index says semantic_available: false', () => {
    expect(modeAvailability('tests', healthy).map((m) => [m.id, m.available, m.why])).toEqual([
      ['keyword', true, undefined], ['hybrid', true, undefined], ['semantic', true, undefined],
    ])
    // Unknown while loading (null) and an older server (no field) both offer them.
    expect(modeAvailability('tests', null).every((m) => m.available)).toBe(true)
    expect(modeAvailability('tests', { status: 'healthy', document_count: 1, last_indexed_at: null }).every((m) => m.available)).toBe(true)
  })

  it('disables them with the server\'s reason sentence (or a fallback sentence)', () => {
    const off = modeAvailability('tests', { ...healthy, semantic_available: false, semantic_reason_text: 'Needs the semantic index: no ChromaDB server answers here.' })
    expect(off.filter((m) => !m.available).map((m) => [m.id, m.why])).toEqual([
      ['hybrid', 'Needs the semantic index: no ChromaDB server answers here.'],
      ['semantic', 'Needs the semantic index: no ChromaDB server answers here.'],
    ])
    expect(modeAvailability('all', { ...healthy, semantic_available: false })[2].why).toBe(SEMANTIC_UNAVAILABLE_HINT)
  })

  it('on another scope the semantic chips say they switch the scope', () => {
    expect(modeAvailability('runs', healthy)[1].why).toBe(SEMANTIC_SCOPE_HINT)
  })

  it('a semantic mode on another scope moves the scope to Tests, with the line', () => {
    expect(normalizeMode('all', 'semantic')).toEqual({ scope: 'tests', mode: 'semantic', note: SCOPE_SWITCH_LINE })
    expect(normalizeMode('defects', 'hybrid')).toEqual({ scope: 'tests', mode: 'hybrid', note: SCOPE_SWITCH_LINE })
    expect(normalizeMode('tests', 'hybrid')).toEqual({ scope: 'tests', mode: 'hybrid', note: null })
    expect(normalizeMode('runs', 'keyword')).toEqual({ scope: 'runs', mode: 'keyword', note: null })
  })

  it('another scope moves a semantic mode back to Keyword, with the line', () => {
    expect(modeForScope('runs', 'semantic')).toEqual({ mode: 'keyword', note: MODE_SWITCH_LINE })
    expect(modeForScope('tests', 'semantic')).toEqual({ mode: 'semantic', note: null })
    expect(modeForScope('all', 'keyword')).toEqual({ mode: 'keyword', note: null })
  })

  it('reads unknown modes as Keyword and unknown scopes as All', () => {
    expect(parseMode('vector')).toBe('keyword')
    expect(parseMode(null)).toBe('keyword')
    expect(parseMode('hybrid')).toBe('hybrid')
    expect(parseScope('everything')).toBe('all')
    expect(parseScope('flaky')).toBe('flaky')
  })
})

describe('searchModel — semantic rows', () => {
  const item: SearchResult = {
    test_case_id: 't', test_run_id: 'r', test_name: 'pay times out', suite_name: 'Payments', status: 'FAILED',
    failure_count: 2, last_run_date: '2026-10-01T00:00:00Z', relevance_score: 0.82,
  }

  it('maps a GET /search row onto the result row', () => {
    expect(toResultRow(item)).toEqual({
      entity_type: 'test_case', entity_id: 't', title: 'pay times out', subtitle: 'Payments · FAILED',
      project_id: null, project_name: null, navigation_url: '/runs/r/tests/t', relevance_score: 0.82,
      match_reasons: [], metadata: { status: 'FAILED', suite_name: 'Payments' },
    })
    expect(toResultRow({ ...item, suite_name: undefined, relevance_score: undefined }).subtitle).toBe('No suite · FAILED')
    expect(toResultRow({ ...item, relevance_score: undefined }).relevance_score).toBe(0)
  })

  it('keeps the fallback fields and the lower-bound flags of the answer', () => {
    const page = fromSemanticResponse({
      items: [item], total: 1, page: 1, size: 25, pages: 1, query: 'pay', search_type: 'keyword',
      requested_search_type: 'semantic', fallback_reason: 'semantic_unavailable',
      fallback_message: 'Semantic index unavailable — showing keyword results.',
      result_status: 'complete', counts_are_exact: true,
    })
    expect(page.items[0].navigation_url).toBe('/runs/r/tests/t')
    expect(page.entity_counts).toEqual({ test_case: 1 })
    expect(page.fallback_reason).toBe('semantic_unavailable')
    expect(page.fallback_message).toBe('Semantic index unavailable — showing keyword results.')
    expect(page.search_type).toBe('keyword')
    expect(fromSemanticResponse({ items: [], total: 0, page: 1, size: 25, pages: 0, query: '', search_type: 'semantic' }).fallback_reason).toBeNull()
  })
})

describe('searchModel — the typeahead\'s distinct rows', () => {
  const r = (n: number, type: GlobalSearchResult['entity_type'], title: string): GlobalSearchResult => ({
    entity_type: type, entity_id: `e${n}`, title, subtitle: `s${n}`, project_id: null, project_name: null,
    navigation_url: `/x/${n}`, relevance_score: 1, match_reasons: [], metadata: {},
  })

  it('five rows of one test + two others → three rows, the first of each in the server\'s order, counted', () => {
    const items = [r(1, 'test_case', 'a'), r(2, 'test_case', 'a'), r(3, 'test_run', 'b'), r(4, 'test_case', 'a'),
      r(5, 'test_case', 'a'), r(6, 'test_case', 'a'), r(7, 'defect', 'c')]
    const matches = distinctMatches(items)
    expect(matches.map((m) => [m.row.entity_id, m.count])).toEqual([['e1', 5], ['e3', 1], ['e7', 1]])
    expect(matchCountLabel(matches[0], false)).toBe('5 runs')
    expect(matchCountLabel(matches[0], true)).toBe('5+ runs')
    expect(matchCountLabel(matches[1], false)).toBeNull()
  })

  it('the same title as another kind is another entity; at most `max` rows', () => {
    expect(distinctMatches([r(1, 'test_case', 'a'), r(2, 'suite', 'a')])).toHaveLength(2)
    expect(distinctMatches([1, 2, 3, 4, 5, 6, 7].map((n) => r(n, 'test_case', `t${n}`)))).toHaveLength(5)
    expect(distinctMatches([1, 2, 3].map((n) => r(n, 'test_case', `t${n}`)), 2).map((m) => m.row.entity_id)).toEqual(['e1', 'e2'])
    expect(matchCountLabel({ row: r(1, 'defect', 'x'), count: 3 }, false)).toBe('3 matches')
  })
})

describe('searchModel — helpers', () => {
  it('errorSentence: a string detail, a 422\'s first msg, else the fallback', () => {
    expect(errorSentence({ response: { data: { detail: 'Triggering a reindex requires QA_LEAD or higher' } } }, 'x'))
      .toBe('Triggering a reindex requires QA_LEAD or higher')
    expect(errorSentence({ response: { data: { detail: [{ loc: ['body'], msg: 'A saved search needs its query text (1 to 500 characters).' }] } } }, 'x'))
      .toBe('A saved search needs its query text (1 to 500 characters).')
    expect(errorSentence(new Error('network'), 'Could not save.')).toBe('Could not save.')
    expect(errorSentence(null, 'fallback')).toBe('fallback')
  })

  it('relativeTime', () => {
    const now = Date.parse('2026-10-24T12:00:00Z')
    expect(relativeTime(now - 5_000, now)).toBe('5s ago')
    expect(relativeTime(now - 5 * 60_000, now)).toBe('5m ago')
    expect(relativeTime(now - 3 * 3_600_000, now)).toBe('3h ago')
    expect(relativeTime(now - 2 * 86_400_000, now)).toBe('2d ago')
    expect(relativeTime(now + 1000, now)).toBe('—')
  })
})
