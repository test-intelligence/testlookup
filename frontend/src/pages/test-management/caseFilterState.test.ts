/**
 * W13 TCL-01: the Cases tab's filter state — its URL form (CSV lists, the
 * Cases-tab keys, every other key kept), the list read's params, the saved-view
 * form and the four presets. Unknown values are dropped on read, so they are
 * never sent.
 */
import { describe, expect, it } from 'vitest'

import {
  activePreset,
  CASE_FILTER_URL_KEYS,
  CASE_PRESET_IDS,
  CASE_PRESET_LABELS,
  CASE_PRESETS,
  CASE_TYPE_OPTIONS,
  caseFiltersKey,
  EMPTY_CASE_FILTERS,
  filtersFromView,
  hasCaseFilters,
  presetFilters,
  readCaseFilters,
  sameFilters,
  toListParams,
  viewFilters,
  writeCaseFilters,
  type CaseFilterState,
} from './caseFilterState'

const USER = '0f2a6c1e-1111-4111-8111-aaaaaaaaaaaa'
const FULL: CaseFilterState = {
  statuses: ['draft', 'review_requested'],
  priorities: ['critical', 'high'],
  types: ['e2e'],
  owner: 'me',
  automation: 'not_automated',
  suite: 'Checkout',
  q: 'login',
  aiGenerated: true,
  requirement: 'REQ-PAY-07',
}
const read = (search: string) => readCaseFilters(new URLSearchParams(search))

describe('URL ↔ state', () => {
  it('writes CSV lists under the Cases-tab keys and reads them back exactly', () => {
    const params = writeCaseFilters(new URLSearchParams(), FULL)
    expect(params.toString()).toBe(
      'status=draft%2Creview_requested&priority=critical%2Chigh&type=e2e&owner=me&automation=not_automated'
      + '&cases_suite=Checkout&q=login&ai=1&requirement=REQ-PAY-07',
    )
    expect(readCaseFilters(params)).toEqual(FULL)
  })

  it('keeps every non-filter key (tab, case, the Suites tab suite) and rewrites only its own', () => {
    const before = new URLSearchParams('tab=cases&case=c-1&suite=Auth&status=draft&owner=me')
    const next = writeCaseFilters(before, { ...EMPTY_CASE_FILTERS, priorities: ['low'] })
    expect(next.get('tab')).toBe('cases')
    expect(next.get('case')).toBe('c-1')
    expect(next.get('suite')).toBe('Auth')
    expect(next.has('status')).toBe(false)
    expect(next.has('owner')).toBe(false)
    expect(next.get('priority')).toBe('low')
    expect(before.get('status'), 'a NEW object: the input is untouched').toBe('draft')
  })

  it('an empty state writes no filter key', () => {
    const next = writeCaseFilters(new URLSearchParams('tab=cases'), EMPTY_CASE_FILTERS)
    expect(next.toString()).toBe('tab=cases')
    for (const key of Object.values(CASE_FILTER_URL_KEYS)) expect(next.has(key)).toBe(false)
  })

  it('drops unknown values on read, de-duplicates, and caps a list at 20', () => {
    const state = read(
      'status=draft,bogus,draft,%20review_requested&priority=p0,high&type=e2e,contract&owner=bob'
      + '&automation=yes&ai=yes&cases_suite=%20%20&requirement=',
    )
    expect(state).toEqual({ ...EMPTY_CASE_FILTERS, statuses: ['draft', 'review_requested'], priorities: ['high'], types: ['e2e'] })
    const many = read(`type=${Array.from({ length: 30 }, (_, i) => CASE_TYPE_OPTIONS[i % 9].value).join(',')}`)
    expect(many.types).toHaveLength(9)
  })

  it('reads repeated keys too, an owner id, ai=1 or ai=true', () => {
    expect(read('status=draft&status=active').statuses).toEqual(['draft', 'active'])
    expect(read(`owner=${USER.toUpperCase()}`).owner).toBe(USER)
    expect(read('ai=1').aiGenerated).toBe(true)
    expect(read('ai=true').aiGenerated).toBe(true)
    expect(read('ai=0').aiGenerated).toBe(false)
  })

  it('keeps the search text as typed (the box shows it), capped at 200', () => {
    expect(read('q=%20login%20').q).toBe(' login ')
    expect(read(`q=${'x'.repeat(250)}`).q).toHaveLength(200)
  })
})

describe('toListParams', () => {
  it('sends the server names, lists as arrays (one value included), nothing unset', () => {
    expect(toListParams(FULL, { page: 2, size: 25, includeAutomation: true })).toEqual({
      page: 2, size: 25, status: ['draft', 'review_requested'], test_type: ['e2e'], priority: ['critical', 'high'],
      search: 'login', owner: 'me', automation: 'not_automated', suite_name: 'Checkout', requirement: 'REQ-PAY-07',
      include_automation: true, ai_generated: true,
    })
    expect(toListParams(EMPTY_CASE_FILTERS, { page: 1, size: 25, includeAutomation: false })).toEqual({ page: 1, size: 25 })
    expect(toListParams({ ...EMPTY_CASE_FILTERS, statuses: ['draft'] }, { page: 1, size: 25, includeAutomation: false }))
      .toEqual({ page: 1, size: 25, status: ['draft'] })
  })

  it('a blank search is not sent; Archived among the statuses asks for archived rows', () => {
    expect(toListParams({ ...EMPTY_CASE_FILTERS, q: '   ' }, { page: 1, size: 25, includeAutomation: false })).toEqual({ page: 1, size: 25 })
    expect(toListParams({ ...EMPTY_CASE_FILTERS, statuses: ['archived', 'draft'] }, { page: 1, size: 25, includeAutomation: false }))
      .toEqual({ page: 1, size: 25, status: ['archived', 'draft'], include_archived: true })
  })

  it('never sends assignee_id (a parameter the API ignores)', () => {
    expect(Object.keys(toListParams(FULL, { page: 1, size: 25, includeAutomation: false }))).not.toContain('assignee_id')
  })
})

describe('saved view ↔ state', () => {
  it('a view holds only the set values, under the server keys', () => {
    expect(viewFilters(FULL)).toEqual({
      kind: 'test_case_view', statuses: ['draft', 'review_requested'], priorities: ['critical', 'high'], types: ['e2e'],
      owner: 'me', automation: 'not_automated', suite: 'Checkout', q: 'login', ai_generated: true, requirement: 'REQ-PAY-07',
    })
    expect(viewFilters(EMPTY_CASE_FILTERS)).toEqual({ kind: 'test_case_view' })
    expect(viewFilters({ ...EMPTY_CASE_FILTERS, q: '  login  ' }).q).toBe('login')
  })

  it('round-trips state -> view -> state', () => {
    expect(filtersFromView(viewFilters(FULL))).toEqual(FULL)
    expect(filtersFromView(viewFilters(EMPTY_CASE_FILTERS))).toEqual(EMPTY_CASE_FILTERS)
  })

  it('a stored view written elsewhere is read through the same rules (unknown values dropped)', () => {
    expect(filtersFromView({
      kind: 'test_case_view', statuses: ['draft', 'gone'], priorities: 'high', owner: 'everyone', automation: 'maybe',
      ai_generated: 'true', suite: '', q: 42, requirement: 'r'.repeat(201),
    })).toEqual({ ...EMPTY_CASE_FILTERS, statuses: ['draft'] })
    expect(filtersFromView(null)).toEqual(EMPTY_CASE_FILTERS)
    expect(filtersFromView({ owner: USER }).owner).toBe(USER)
  })
})

describe('presets', () => {
  it('four chips in order with the server labels; My P0/P1 is owner = me', () => {
    expect(CASE_PRESET_IDS).toEqual(['my_drafts', 'my_p0_p1', 'unautomated', 'ai_drafts'])
    expect(CASE_PRESET_IDS.map((id) => CASE_PRESET_LABELS[id])).toEqual(['My drafts', 'My P0/P1', 'Unautomated', 'AI drafts'])
    expect(Object.keys(CASE_PRESETS).sort()).toEqual([...CASE_PRESET_IDS].sort())
  })

  it('each preset applies exactly its filters (it REPLACES every key) and sends its params', () => {
    const params = (id: (typeof CASE_PRESET_IDS)[number]) =>
      toListParams(presetFilters(id), { page: 1, size: 25, includeAutomation: false })
    expect(params('my_drafts')).toEqual({ page: 1, size: 25, owner: 'me', status: ['draft'] })
    expect(params('my_p0_p1')).toEqual({ page: 1, size: 25, owner: 'me', priority: ['critical', 'high'] })
    expect(params('unautomated')).toEqual({ page: 1, size: 25, automation: 'not_automated' })
    expect(params('ai_drafts')).toEqual({ page: 1, size: 25, status: ['draft'], ai_generated: true })
    expect(writeCaseFilters(new URLSearchParams('tab=cases&q=old&type=e2e'), presetFilters('unautomated')).toString())
      .toBe('tab=cases&automation=not_automated')
  })

  it('a chip is active exactly when the state equals its filters', () => {
    for (const id of CASE_PRESET_IDS) expect(activePreset(presetFilters(id))).toBe(id)
    expect(activePreset(EMPTY_CASE_FILTERS)).toBeNull()
    expect(activePreset({ ...presetFilters('my_drafts'), q: 'login' }), 'one more filter: no chip').toBeNull()
    expect(activePreset({ ...presetFilters('my_p0_p1'), priorities: ['high', 'critical'] }), 'lists compare as sets')
      .toBe('my_p0_p1')
    expect(activePreset(read('owner=me&status=draft'))).toBe('my_drafts')
  })
})

describe('equality and the scope key', () => {
  it('sameFilters compares lists as sets and the search trimmed', () => {
    expect(sameFilters({ ...FULL, statuses: ['review_requested', 'draft'], q: ' login ' }, FULL)).toBe(true)
    expect(sameFilters({ ...FULL, owner: null }, FULL)).toBe(false)
    expect(sameFilters({ ...FULL, types: [] }, FULL)).toBe(false)
    expect(hasCaseFilters(EMPTY_CASE_FILTERS)).toBe(false)
    expect(hasCaseFilters({ ...EMPTY_CASE_FILTERS, aiGenerated: true })).toBe(true)
  })

  it('caseFiltersKey is stable for equal filters and changes with any filter', () => {
    expect(caseFiltersKey({ ...FULL, priorities: ['high', 'critical'] })).toBe(caseFiltersKey(FULL))
    const keys = new Set([
      caseFiltersKey(FULL),
      caseFiltersKey({ ...FULL, owner: USER }),
      caseFiltersKey({ ...FULL, automation: 'automated' }),
      caseFiltersKey({ ...FULL, requirement: null }),
      caseFiltersKey({ ...FULL, statuses: ['draft'] }),
      caseFiltersKey(EMPTY_CASE_FILTERS),
    ])
    expect(keys.size).toBe(6)
  })
})
