/**
 * Clicking "Upload Report" must let you upload a report.
 *
 * Two attempts at this.
 *
 * The first was a silent no-op: the sidebar entry deep-links to
 * `/runs?upload=1`, the page opened the panel only when the scope was a single
 * project, and **All Projects is the default** — so the link rendered an
 * ordinary runs list and said nothing.
 *
 * The fix for that rendered an explanation ("choose a project to upload into").
 * It was reported again, correctly: a user who clicks "Upload Report" wants to
 * upload, and being told to go and change a header selector first is still a
 * dead end. The explanation fixed the *silence*, not the *goal* — and the
 * probe suite passed throughout, because it encoded the behaviour that had
 * been decided rather than the one that was asked for.
 *
 * The panel now opens under any scope and asks for the project itself. Scope
 * is a question, not a refusal.
 *
 * Role remains a genuine block: `POST /api/v1/ingest/file` rejects a
 * non-QA-Engineer with 403, so opening the panel would only defer the failure
 * to submit time. That distinction — a precondition the UI can satisfy versus
 * one it cannot — is what this file pins.
 */
import { describe, expect, it } from 'vitest'

import { resolveUploadBlockedReason } from './RunsPage'

describe('resolveUploadBlockedReason', () => {
  it('does not block on scope — the modal asks for the project', () => {
    // The regression that was reported twice. There is no scope input left to
    // pass: All Projects must not stop the panel from opening.
    expect(
      resolveUploadBlockedReason({ uploadRequested: true, isQaEngineer: true }),
    ).toBeNull()
  })

  it('reports nothing when no upload was requested', () => {
    expect(
      resolveUploadBlockedReason({ uploadRequested: false, isQaEngineer: true }),
    ).toBeNull()
    // Not even when the role is missing — an unrequested upload is not
    // blocked, and a banner on every ordinary /runs visit would be noise.
    expect(
      resolveUploadBlockedReason({ uploadRequested: false, isQaEngineer: false }),
    ).toBeNull()
  })

  it('still blocks on role, which the UI cannot satisfy', () => {
    expect(
      resolveUploadBlockedReason({ uploadRequested: true, isQaEngineer: false }),
    ).toBe('role')
  })

  it('only ever blocks for a reason the user cannot resolve in the panel', () => {
    // The property behind both bug reports: every remaining block must be one
    // the modal genuinely cannot work around. Anything the UI could ask for
    // should be asked for, not refused.
    for (const isQaEngineer of [true, false]) {
      const reason = resolveUploadBlockedReason({ uploadRequested: true, isQaEngineer })
      expect(reason === null).toBe(isQaEngineer)
    }
  })
})
