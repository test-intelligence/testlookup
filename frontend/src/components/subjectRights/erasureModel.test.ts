/**
 * W31 STRAT-12: the Erase panel's pure model (contract §2.3 E-1 / E-8 / E-11b, §4.3, §4.4) — the exact typed
 * confirm, blockers, "not counted", the phase line per phase, resume, the plurals and the row rules.
 */
import { describe, expect, it } from 'vitest'

import {
  blockerFixture,
  erasurePreviewFixture,
  incompleteErasureFixture,
  SUBJECT_PSEUDONYM,
  SUBJECT_USERNAME,
  subjectJobFixture,
} from '@/test/subjectRightsMocks'
import { ERASED_DISPLAY, UI_SENTENCES } from '@/types/subjectRights'
import {
  askedLine,
  canErase,
  confirmLabel,
  effectiveBlockers,
  erasedLine,
  erasedName,
  erasureCountLines,
  erasureView,
  IN_PROGRESS,
  isErasedUser,
  locationCount,
  locationLine,
  NOT_COUNTED,
  offersPersonalData,
  phaseLine,
  previewSummary,
  requesterDisplay,
} from './erasureModel'

describe('canErase — the typed confirm', () => {
  it('enables ONLY on the exact username: case, surrounding and inner whitespace all count', () => {
    const preview = erasurePreviewFixture()
    expect(canErase(preview, SUBJECT_USERNAME)).toBe(true)
    for (const typed of ['', 'quinn', SUBJECT_USERNAME.toUpperCase(), 'Quinn.tester', ` ${SUBJECT_USERNAME}`, `${SUBJECT_USERNAME} `, 'quinn .tester']) {
      expect(canErase(preview, typed), JSON.stringify(typed)).toBe(false)
    }
    expect(canErase(undefined, SUBJECT_USERNAME)).toBe(false)
  })

  it('any blocker disables it, whatever is typed', () => {
    for (const code of ['last_admin', 'export_blocks_erasure', 'not_a_person', 'self_erasure'] as const) {
      expect(canErase(erasurePreviewFixture({ blockers: [blockerFixture(code)] }), SUBJECT_USERNAME)).toBe(false)
    }
  })

  it('a resumable erasure is not blocked by its own "already in progress" / "erased"; other blockers still count', () => {
    const job = incompleteErasureFixture('objects')
    const resume = erasurePreviewFixture({ erasure_job: job, blockers: [blockerFixture('erasure_running'), blockerFixture('user_erased')] })
    expect(effectiveBlockers(resume)).toEqual([])
    expect(canErase(resume, SUBJECT_USERNAME)).toBe(true)
    const stillBlocked = erasurePreviewFixture({ erasure_job: job, blockers: [blockerFixture('last_admin')] })
    expect(canErase(stillBlocked, SUBJECT_USERNAME)).toBe(false)
    // A job that is merely running (no stop recorded) is not a resume: its blocker holds.
    const running = erasurePreviewFixture({ erasure_job: subjectJobFixture({ kind: 'erasure', status: 'running' }), blockers: [blockerFixture('erasure_running')] })
    expect(canErase(running, SUBJECT_USERNAME)).toBe(false)
  })
})

describe('counts and lines', () => {
  it('measured: false reads "not counted (took too long)", never 0; a count reads as a number', () => {
    expect(locationCount({ count: null, measured: false })).toBe(NOT_COUNTED)
    expect(locationCount({ count: 0, measured: false })).toBe(NOT_COUNTED)
    expect(locationCount({ count: undefined, measured: true })).toBe(NOT_COUNTED)
    expect(locationCount({ count: 0, measured: true })).toBe('0')
    expect(locationCount({ count: 1234, measured: true })).toBe('1,234')
  })

  it('locationLine carries the label, the count and a kept row\'s reason', () => {
    const [kept] = erasurePreviewFixture().kept
    expect(locationLine(kept)).toEqual({ key: kept.key, label: kept.label, count: '6', reason: kept.reason })
    expect(locationLine({ key: 'a.b', label: 'A', action: 'delete', count: 2, measured: true }).reason).toBeNull()
  })

  it('the summary pluralises right: "6 changes · 4 kept · 2 open items" and "1 change · 1 kept · 1 open item"', () => {
    expect(previewSummary(erasurePreviewFixture())).toBe('6 changes · 4 kept · 2 open items')
    const one = erasurePreviewFixture()
    expect(previewSummary({ changes: one.changes.slice(0, 1), kept: one.kept.slice(0, 1), open_items: one.open_items?.slice(0, 1) })).toBe('1 change · 1 kept · 1 open item')
    expect(previewSummary({ changes: [], kept: [], open_items: undefined })).toBe('0 changes · 0 kept · 0 open items')
  })

  it('the confirm label and the done line fill their placeholders', () => {
    expect(confirmLabel(SUBJECT_USERNAME)).toBe(`Type ${SUBJECT_USERNAME} to confirm`)
    expect(erasedLine(SUBJECT_PSEUDONYM)).toBe(`Erased. This account now reads as “Erased user” (${SUBJECT_PSEUDONYM}).`)
  })

  it('a finished erasure\'s counts: by label, largest first, "1 row" in the singular; the untouched ones in one line', () => {
    const counts = { 'users.*': 1, 'project_members.user_id': 3, 'chat_sessions.user_id': 0, 'zqxw.unlabelled': 2 }
    const count_labels = { 'users.*': 'Name, username and e-mail', 'project_members.user_id': 'Project memberships' }
    expect(erasureCountLines({ counts, count_labels })).toEqual({
      lines: [
        { key: 'project_members.user_id', label: 'Project memberships', count: '3 rows' },
        { key: 'zqxw.unlabelled', label: 'zqxw.unlabelled', count: '2 rows' },
        { key: 'users.*', label: 'Name, username and e-mail', count: '1 row' },
      ],
      untouched: '1 other location held nothing of this person.',
    })
    expect(erasureCountLines({ counts: { a: 0, b: 0 } }).untouched).toBe('2 other locations held nothing of this person.')
    expect(erasureCountLines(null)).toEqual({ lines: [], untouched: null })
  })
})

describe('the phase line and the erasure view', () => {
  it('names each committed step; a job at lockout reads "Erasure in progress", never "Erased"', () => {
    expect(phaseLine({ phase: 'lockout' })).toBe(`${IN_PROGRESS} Step 1 of 4: account locked.`)
    expect(phaseLine({ phase: 'postgres' })).toBe(`${IN_PROGRESS} Step 2 of 4: records pseudonymised.`)
    expect(phaseLine({ phase: 'objects' })).toBe(`${IN_PROGRESS} Step 3 of 4: stored exports removed.`)
    expect(phaseLine({ phase: 'redis' })).toBe(`${IN_PROGRESS} Step 4 of 4: cached sessions cleared.`)
    expect(phaseLine({ phase: null })).toBe(IN_PROGRESS)
    const lockout = erasureView(subjectJobFixture({ kind: 'erasure', status: 'queued' }), SUBJECT_PSEUDONYM)
    expect(lockout.kind).toBe('in_progress')
    expect(lockout.line).not.toMatch(/Erased/)
  })

  it('done → erase_done with the pseudonym; an incomplete job → the phase, its sentence and Resume', () => {
    const done = erasureView(subjectJobFixture({ kind: 'erasure', status: 'succeeded' }), SUBJECT_PSEUDONYM)
    expect(done).toEqual({ kind: 'done', line: erasedLine(SUBJECT_PSEUDONYM), detail: null, resumable: false })
    const stopped = incompleteErasureFixture('objects')
    expect(erasureView(stopped, SUBJECT_PSEUDONYM)).toEqual({
      kind: 'incomplete',
      line: phaseLine({ phase: 'objects' }),
      detail: stopped.error?.message,
      resumable: true,
    })
    expect(erasureView(null, SUBJECT_PSEUDONYM).kind).toBe('none')
    expect(UI_SENTENCES.erase_done).toContain('{pseudonym}')
  })
})

describe('the row rules', () => {
  const row = { id: 'u1', username: 'quinn.tester', full_name: 'Quinn Tester' }

  it('no Personal data action on a service or synthetic account, nor on the admin\'s own row', () => {
    expect(offersPersonalData(row, 'admin-id')).toBe(true)
    expect(offersPersonalData({ ...row, is_service_account: true }, 'admin-id')).toBe(false)
    expect(offersPersonalData({ ...row, is_synthetic: true }, 'admin-id')).toBe(false)
    expect(offersPersonalData(row, 'u1')).toBe(false)
    // An erased row keeps the action (its section shows the erasure's outcome).
    expect(offersPersonalData({ ...row, is_erased: true }, 'admin-id')).toBe(true)
  })

  it('erased = is_erased or erased_at; the name line is the server\'s "Erased user"', () => {
    expect(isErasedUser(row)).toBe(false)
    expect(isErasedUser({ ...row, is_erased: true })).toBe(true)
    expect(isErasedUser({ ...row, erased_at: '2026-11-04T10:00:00Z' })).toBe(true)
    expect(erasedName({ ...row, full_name: ERASED_DISPLAY })).toBe(ERASED_DISPLAY)
    expect(erasedName({ ...row, full_name: null })).toBe(ERASED_DISPLAY)
  })
})

describe('W38: the requester and the pending request', () => {
  it('requesterDisplay: SCIM reads "Your identity provider (SCIM)" even without a display; else the display; else null', () => {
    expect(requesterDisplay({ source: 'scim', requested_by_display: null })).toBe(UI_SENTENCES.requester_scim)
    expect(requesterDisplay({ source: 'scim', requested_by_display: 'Ignored' })).toBe('Your identity provider (SCIM)')
    expect(requesterDisplay({ source: 'admin', requested_by_display: ' Morgan Admin ' })).toBe('Morgan Admin')
    expect(requesterDisplay({ source: 'admin', requested_by_display: null })).toBeNull()
    expect(requesterDisplay(null)).toBeNull()
  })

  it('askedLine: "This person asked for erasure on <day>."', () => {
    expect(askedLine('2026-09-18T10:00:00Z')).toBe('This person asked for erasure on Sep 18, 2026.')
  })
})
