import { describe, expect, it } from 'vitest'
import { SIMILAR_EMPTY_TEXT, emptyStateText, hasSimilarFailures } from './similarFailuresModel'

describe('similarFailuresModel (W12 SRCH-01)', () => {
  it('an empty answer says why, by semantic_status; a missing status reads ok', () => {
    expect(emptyStateText('ok')).toBe("No similar failures in this project's semantic index.")
    expect(emptyStateText(undefined)).toBe(SIMILAR_EMPTY_TEXT.ok)
    expect(emptyStateText('not_configured')).toBe('Similar failures need the semantic index: no ChromaDB server answers here.')
    expect(emptyStateText('unavailable')).toBe('The semantic index answered with an error. Try again later.')
    expect(emptyStateText('something-new')).toBe(SIMILAR_EMPTY_TEXT.ok)
  })

  it('only FAILED and BROKEN results have the tab', () => {
    expect(['FAILED', 'BROKEN', 'failed'].every(hasSimilarFailures)).toBe(true)
    expect(['PASSED', 'SKIPPED', 'UNKNOWN', '', null, undefined].some(hasSimilarFailures)).toBe(false)
  })
})
