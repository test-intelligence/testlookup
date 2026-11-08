/**
 * W38 (W31 F-1, contract §4): the administrators' queue model — the status and decided cells, which actions a row
 * offers (none on the reader's own request), the reasons in the fixed order with the sentence the person reads,
 * the Personal data section's view of the person, and the open row kept after a re-read drops it.
 */
import { describe, expect, it } from 'vitest'

import { ADMIN_DISPLAY, erasureRequestRowFixture, OTHER_PERSON_ID, SUBJECT_DISPLAY, SUBJECT_USER_ID, SUBJECT_USERNAME } from '@/test/subjectRightsMocks'
import { DECLINE_CODES, DECLINE_SENTENCES, ERASED_DISPLAY, ERROR_SENTENCES } from '@/types/subjectRights'
import { formatCompactDateTime } from '@/utils/formatters'
import {
  asUserRow,
  decidedLabel,
  DECLINE_OPTIONS,
  declinePreview,
  DEFAULT_FILTER,
  FILTERS,
  OWN_REQUEST_NOTE,
  personName,
  rowActions,
  statusLabel,
  withOpenRow,
} from './erasureRequestsModel'

describe('erasureRequestsModel', () => {
  it('the filter opens on Pending; the order is Pending / Decided / All', () => {
    expect(DEFAULT_FILTER).toBe('pending')
    expect([...FILTERS]).toEqual(['pending', 'decided', 'all'])
  })

  it('statusLabel: Pending, "Accepted — erasure started", "Declined — <reason>", Withdrawn', () => {
    expect(statusLabel(erasureRequestRowFixture())).toBe('Pending')
    expect(statusLabel(erasureRequestRowFixture({ status: 'accepted' }))).toBe('Accepted — erasure started')
    expect(statusLabel(erasureRequestRowFixture({ status: 'declined', decline_code: 'legal_obligation' }))).toBe('Declined — Legal obligation')
    expect(statusLabel(erasureRequestRowFixture({ status: 'declined', decline_code: 'not_a_code' }))).toBe('Declined — Other')
    expect(statusLabel(erasureRequestRowFixture({ status: 'withdrawn' }))).toBe('Withdrawn')
  })

  it('decidedLabel: "—" while pending; the stamp and the decider; a withdrawal names nobody', () => {
    expect(decidedLabel(erasureRequestRowFixture())).toBe('—')
    const declined = erasureRequestRowFixture({ status: 'declined' })
    expect(decidedLabel(declined)).toBe(`${formatCompactDateTime(declined.decided_at)} by ${ADMIN_DISPLAY}`)
    const withdrawn = erasureRequestRowFixture({ status: 'withdrawn' })
    expect(decidedLabel(withdrawn)).toBe(formatCompactDateTime(withdrawn.decided_at))
  })

  it('rowActions: a pending row is reviewed, the reader\'s OWN only noted, a decided row offers nothing', () => {
    expect(rowActions(erasureRequestRowFixture())).toBe('review')
    expect(rowActions(erasureRequestRowFixture({ requester_is_you: true }))).toBe('own')
    expect(OWN_REQUEST_NOTE).toBe(ERROR_SENTENCES.own_request)
    for (const status of ['accepted', 'declined', 'withdrawn'] as const) {
      expect(rowActions(erasureRequestRowFixture({ status })), status).toBe('none')
      expect(rowActions(erasureRequestRowFixture({ status, requester_is_you: true })), status).toBe('none')
    }
  })

  it('the reasons: the four codes in the vocabulary order; the preview is the sentence the person reads', () => {
    expect(DECLINE_OPTIONS.map((o) => o.code)).toEqual([...DECLINE_CODES])
    expect(DECLINE_OPTIONS.map((o) => o.label)).toEqual(['Legal obligation', 'Work in progress', 'Identity not confirmed', 'Other'])
    expect(declinePreview('active_contract')).toBe(`They will read: “${DECLINE_SENTENCES.active_contract}”`)
  })

  it('the person: the server display, else the username; an erased person reads "Erased user"', () => {
    expect(personName(erasureRequestRowFixture())).toBe(SUBJECT_DISPLAY)
    expect(personName(erasureRequestRowFixture({ subject_display: ' ', subject_username: 'quinn' }))).toBe('quinn')
    expect(personName(erasureRequestRowFixture({ subject_display: '', subject_username: null }))).toBe(ERASED_DISPLAY)
    expect(asUserRow(erasureRequestRowFixture({ subject_is_erased: true }))).toEqual({
      id: SUBJECT_USER_ID, username: SUBJECT_USERNAME, full_name: SUBJECT_DISPLAY, is_erased: true,
    })
  })

  it('withOpenRow: keeps the open row a re-read dropped (accepted when its Review erased); never duplicates it', () => {
    const open = erasureRequestRowFixture()
    const other = erasureRequestRowFixture({ id: '00000000-0000-4000-8000-0000000e5a02', subject_user_id: OTHER_PERSON_ID })
    expect(withOpenRow([open, other], open, new Set())).toEqual([open, other])
    expect(withOpenRow([other], null, new Set())).toEqual([other])
    expect(withOpenRow([other], open, new Set())).toEqual([open, other])
    const kept = withOpenRow([other], open, new Set([open.id]))
    expect(kept[0]).toEqual({ ...open, status: 'accepted' })
    expect(statusLabel(kept[0])).toBe('Accepted — erasure started')
  })
})
