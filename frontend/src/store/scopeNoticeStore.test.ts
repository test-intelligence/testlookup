/**
 * The dropped-value notices (VIZ-303 / VIZ-306), and the `drill` dimension
 * Wave 3 adds (C5): a drill-down level a shared link named that the page could
 * not open is said out loud in the same place as a dropped suite or release.
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { DROP_REASONS, useScopeNoticeStore, type ScopeDimension } from './scopeNoticeStore'

const DRILL_SENTENCE = 'Part of the drill-down in this link was not valid, so the view opens at the level above it.'

beforeEach(() => useScopeNoticeStore.getState().dismiss())

describe('scopeNoticeStore', () => {
  it('names the three dimensions a notice can be about', () => {
    const dimensions: ScopeDimension[] = ['release', 'suite', 'drill']
    for (const dimension of dimensions) {
      useScopeNoticeStore.getState().pushNotice({ dimension, values: [`${dimension}-value`], reason: 'why' })
    }
    expect(useScopeNoticeStore.getState().notices.map((n) => n.dimension)).toEqual(['release', 'suite', 'drill'])
  })

  it('a drill notice is kept apart from a suite notice with the same reason', () => {
    const { pushNotice } = useScopeNoticeStore.getState()
    pushNotice({ dimension: 'suite', values: ['payments'], reason: DROP_REASONS.malformed })
    pushNotice({ dimension: 'drill', values: [DRILL_SENTENCE], reason: DROP_REASONS.malformed })
    const notices = useScopeNoticeStore.getState().notices
    expect(notices).toHaveLength(2)
    expect(notices[1]).toEqual({ dimension: 'drill', values: [DRILL_SENTENCE], reason: DROP_REASONS.malformed })
  })

  it('the same drill sentence pushed on every render is one notice, and the store does not change', () => {
    const { pushNotice } = useScopeNoticeStore.getState()
    pushNotice({ dimension: 'drill', values: [DRILL_SENTENCE], reason: 'link' })
    const before = useScopeNoticeStore.getState().notices
    pushNotice({ dimension: 'drill', values: [DRILL_SENTENCE], reason: 'link' })
    expect(useScopeNoticeStore.getState().notices).toBe(before)
  })

  it('merges new values into a notice of the same dimension and reason; empty values are a no-op', () => {
    const { pushNotice } = useScopeNoticeStore.getState()
    pushNotice({ dimension: 'suite', values: [], reason: 'r' })
    expect(useScopeNoticeStore.getState().notices).toEqual([])
    pushNotice({ dimension: 'suite', values: ['a', 'a'], reason: 'r' })
    pushNotice({ dimension: 'suite', values: ['b', 'a'], reason: 'r' })
    expect(useScopeNoticeStore.getState().notices).toEqual([{ dimension: 'suite', values: ['a', 'b'], reason: 'r' }])
    useScopeNoticeStore.getState().dismiss()
    expect(useScopeNoticeStore.getState().notices).toEqual([])
  })
})
