/** W13 TCL-01: the Views row's helpers. */
import { describe, expect, it } from 'vitest'
import { EMPTY_CASE_FILTERS } from './caseFilterState'
import {
  CASE_VIEW_PAGE,
  NAME_PROBLEM,
  isMine,
  nameProblem,
  orderCaseViews,
  savePayload,
  shareLabel,
  viewIsActive,
  viewState,
} from './caseViewsModel'

const v = (name: string, user_id: string) => ({ name, user_id })

describe('caseViewsModel', () => {
  it('orders mine first, then the shared ones, each by name', () => {
    const ordered = orderCaseViews([v('b', 'u-2'), v('z', 'u-me'), v('a', 'u-2'), v('c', 'u-me')], 'u-me')
    expect(ordered.map((x) => x.name)).toEqual(['c', 'z', 'a', 'b'])
    expect(isMine(v('x', 'u-me'), 'u-me')).toBe(true)
    expect(isMine(v('x', 'u-me'), null)).toBe(false)
  })

  it('reads a stored view through the URL rules (unknown values dropped)', () => {
    const state = viewState({ filters: { kind: 'test_case_view', statuses: ['draft', 'bogus'], owner: 'me', extra: 1 } })
    expect(state).toEqual({ ...EMPTY_CASE_FILTERS, statuses: ['draft'], owner: 'me' })
    expect(viewIsActive({ filters: { kind: 'test_case_view', statuses: ['draft'], owner: 'me' } }, state)).toBe(true)
    expect(viewIsActive({ filters: { kind: 'test_case_view', statuses: ['draft'] } }, state)).toBe(false)
  })

  it('builds the create body: page test_management, kind test_case_view, only the set filters', () => {
    expect(CASE_VIEW_PAGE).toBe('test_management')
    expect(savePayload('p-1', ' Mine ', { ...EMPTY_CASE_FILTERS, types: ['e2e'], aiGenerated: true }, false)).toEqual({
      project_id: 'p-1', name: 'Mine', page: 'test_management',
      filters: { kind: 'test_case_view', types: ['e2e'], ai_generated: true }, is_shared: false,
    })
  })

  it('judges a name and words the share box', () => {
    expect(nameProblem('ab')).toBeNull()
    expect(nameProblem(' a ')).toBe(NAME_PROBLEM)
    expect(nameProblem('x'.repeat(256))).toBe(NAME_PROBLEM)
    expect(shareLabel('Payments')).toBe('Share with Payments')
    expect(shareLabel(null)).toBe('Share with this project')
  })
})
