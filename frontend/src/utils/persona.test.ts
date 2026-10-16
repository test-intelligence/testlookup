/**
 * FAIL-07 (owner decision D03): the reader's own role's row comes first in
 * Recommended actions; nothing else moves, nothing is stored.
 */
import { describe, expect, it } from 'vitest'

import { actionRowForRole, orderActionRows, ROLE_ACTION_ROW } from './persona'

const ROWS = ['DEVELOPER', 'QA', 'RELEASE_MANAGER', 'SRE'].map((id) => ({ id }))
const ids = (role: string | null | undefined) => orderActionRows(ROWS, role).rows.map((r) => r.id)

describe('orderActionRows — my role first', () => {
  it.each(['QA_ENGINEER', 'QA_LEAD', 'TESTER'])('%s → QA first, the rest in their order', (role) => {
    expect(ids(role)).toEqual(['QA', 'DEVELOPER', 'RELEASE_MANAGER', 'SRE'])
    expect(orderActionRows(ROWS, role).movedId).toBe('QA')
  })

  it('ADMIN → Release Manager first', () => {
    expect(ids('ADMIN')).toEqual(['RELEASE_MANAGER', 'DEVELOPER', 'QA', 'SRE'])
    expect(orderActionRows(ROWS, 'ADMIN').movedId).toBe('RELEASE_MANAGER')
  })

  it.each([['VIEWER'], [undefined], [null], [''], ['SOMETHING_NEW']])('%s → today\'s order, nothing moved', (role) => {
    expect(ids(role)).toEqual(['DEVELOPER', 'QA', 'RELEASE_MANAGER', 'SRE'])
    expect(orderActionRows(ROWS, role).movedId).toBeNull()
  })

  it('reads the `UserRole.X` spelling', () => {
    expect(actionRowForRole('UserRole.QA_LEAD')).toBe('QA')
    expect(actionRowForRole('UserRole.ADMIN')).toBe('RELEASE_MANAGER')
  })

  it('a row already first is not "moved", and a missing row changes nothing', () => {
    const qaFirst = [{ id: 'QA' }, { id: 'DEVELOPER' }]
    expect(orderActionRows(qaFirst, 'QA_LEAD')).toEqual({ rows: qaFirst, movedId: null })
    const noRm = [{ id: 'DEVELOPER' }, { id: 'QA' }]
    expect(orderActionRows(noRm, 'ADMIN')).toEqual({ rows: noRm, movedId: null })
  })

  it('never returns the caller\'s array and maps no role to the Developer row (there is no developer role)', () => {
    expect(orderActionRows(ROWS, 'VIEWER').rows).not.toBe(ROWS)
    expect(Object.values(ROLE_ACTION_ROW)).not.toContain('DEVELOPER')
    expect(Object.keys(ROLE_ACTION_ROW).sort()).toEqual(['ADMIN', 'QA_ENGINEER', 'QA_LEAD', 'TESTER'])
  })
})
