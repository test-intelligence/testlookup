/** W13 TCL-06: the requirement copy (the server's sentences, pinned) and helpers. */
import { describe, expect, it } from 'vitest'
import {
  GAP_REASON_TEXT,
  LINK_TYPES,
  LINK_TYPE_OPTIONS,
  REQUIREMENT_KEY_INVALID,
  UNMEASURED_TEXT,
  VERIFIED_LABEL,
  COVERAGE_TITLE,
  addedByLine,
  casesWithoutLine,
  gapReason,
  gapsPageLine,
  keyProblem,
  linkTypeLabel,
  partlyLine,
  sourcesLine,
  verifiedValue,
} from './requirementsModel'

describe('requirementsModel', () => {
  it('holds the server sentences verbatim (backend/app/core/test_library_vocab.py)', () => {
    expect(GAP_REASON_TEXT).toEqual({
      no_link: 'No case links it.',
      related_only: 'Only "related" links: no case verifies it.',
      partly_verified: 'Only partly verified.',
    })
    expect(REQUIREMENT_KEY_INVALID).toBe('A requirement key is 1 to 200 characters with no control characters.')
    expect(UNMEASURED_TEXT).toBe('No requirement keys are known to this project yet: add one to a test case.')
    expect(LINK_TYPES).toEqual(['verifies', 'partially_verifies', 'related'])
    expect(LINK_TYPE_OPTIONS.map((o) => o.value)).toEqual([...LINK_TYPES])
  })

  it('never names the old invented stat (TestManagementPage.fabricated-numbers.test.ts)', () => {
    for (const text of [VERIFIED_LABEL, COVERAGE_TITLE]) {
      expect(text).not.toMatch(/Req coverage/i)
      expect(text).not.toMatch(/requirements tracked/i)
    }
  })

  it('judges a key as the server does', () => {
    expect(keyProblem('REQ-PAY-07')).toBeNull()
    expect(keyProblem('   ')).toBe(REQUIREMENT_KEY_INVALID)
    expect(keyProblem('R'.repeat(200))).toBeNull()
    expect(keyProblem('R'.repeat(201))).toBe(REQUIREMENT_KEY_INVALID)
    expect(keyProblem('REQ\t12')).toBe(REQUIREMENT_KEY_INVALID)
    expect(keyProblem(`REQ${String.fromCharCode(0)}`)).toBe(REQUIREMENT_KEY_INVALID)
  })

  it('words the rows, the stat and the gaps with both numbers', () => {
    expect(addedByLine({ created_by_name: 'Priya Shah' })).toBe('added by Priya Shah')
    expect(addedByLine({ created_by_name: null })).toBe('added by not recorded')
    expect(['verifies', 'partially_verifies', 'related', 'other_kind'].map(linkTypeLabel)).toEqual(['verifies', 'partly verifies', 'related', 'other kind'])
    expect(verifiedValue({ verified: 12, known: 15 })).toBe('12 of 15')
    expect(verifiedValue({ verified: 0, known: 1200 })).toBe('0 of 1,200')
    expect(partlyLine({ partly_verified: 2 })).toBe('2 partly verified')
    expect(gapReason({ reason: 'related_only' })).toBe('Only "related" links: no case verifies it.')
    expect(casesWithoutLine(4)).toBe('4 approved or active cases link no requirement')
    expect(casesWithoutLine(1)).toBe('1 approved or active case links no requirement')
    expect(gapsPageLine(1, 25, 25, 40)).toBe('1-25 of 40')
    expect(gapsPageLine(2, 25, 15, 40)).toBe('26-40 of 40')
    expect(gapsPageLine(1, 25, 0, 0)).toBe('0 gaps')
  })

  it('says where a key is known from', () => {
    expect(sourcesLine({ sources: ['link'] })).toBe('known from a link')
    expect(sourcesLine({ sources: ['generation_batch'], batch_claimed_cases: 3 })).toBe('known from a generation batch (claims 3 cases)')
    expect(sourcesLine({ sources: ['generation_batch'], batch_claimed_cases: 1 })).toBe('known from a generation batch (claims 1 case)')
    expect(sourcesLine({ sources: ['generation_batch', 'knowledge_source'], batch_claimed_cases: 0 })).toBe('known from a generation batch, a knowledge source')
    expect(sourcesLine({ sources: [] })).toBe('')
  })
})
