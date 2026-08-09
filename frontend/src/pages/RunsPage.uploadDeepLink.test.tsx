/**
 * The ``/runs?upload=1`` deep link must never silently do nothing.
 *
 * Reported: "the upload report page displays runs page". The sidebar's
 * "Upload Report" entry links to ``/runs?upload=1``, and the page opened the
 * upload panel only when `!isAllProjects`. **All Projects is the default
 * scope**, so the plain link hit that branch every time and rendered an
 * ordinary runs list — no panel, no toast, no explanation. The one piece of
 * copy that explained it sat in a disabled button's `title` attribute, which
 * someone arriving from that click never hovers.
 *
 * Confirmed on the live homelab before the fix:
 *   active scope : all
 *   upload button: present, disabled
 *   file input   : drawer CLOSED
 *
 * The auto-open transition already preserves intent — ``?upload=1`` stays in
 * the URL, so picking a project afterwards opens the panel (also verified
 * live). Only the explanation was missing, which is what the 'scope' reason
 * renders.
 *
 * Second defect pinned here: the deep link checked the flag and the scope but
 * **not the role**, while the button checked all three. A non-QA-engineer
 * following the link got a panel the button denies them, then a 403 from
 * ``POST /api/v1/ingest/file`` on submit. The backend does enforce the role,
 * so this was a misleading affordance rather than a privilege gap.
 */
import { describe, expect, it } from 'vitest'

import { resolveUploadBlockedReason } from './RunsPage'

const base = { uploadRequested: true, isAllProjects: false, isQaEngineer: true }

describe('resolveUploadBlockedReason', () => {
  it('reports nothing when no upload was requested', () => {
    expect(resolveUploadBlockedReason({ ...base, uploadRequested: false })).toBeNull()
    // Not even when a precondition also fails — an unrequested upload is not
    // blocked, and a banner on every ordinary /runs visit would be noise.
    expect(
      resolveUploadBlockedReason({
        uploadRequested: false,
        isAllProjects: true,
        isQaEngineer: false,
      }),
    ).toBeNull()
  })

  it('reports nothing when the request can be honoured', () => {
    expect(resolveUploadBlockedReason(base)).toBeNull()
  })

  it('explains the All-Projects scope — the reported bug', () => {
    expect(resolveUploadBlockedReason({ ...base, isAllProjects: true })).toBe('scope')
  })

  it('explains a missing QA Engineer role', () => {
    expect(resolveUploadBlockedReason({ ...base, isQaEngineer: false })).toBe('role')
  })

  it('prefers the scope reason, which the user can fix themselves', () => {
    // Both fail: naming the role first would send an admin-less user to ask
    // for a role they may already need anyway, when picking a project is the
    // step actually in front of them.
    expect(
      resolveUploadBlockedReason({
        uploadRequested: true,
        isAllProjects: true,
        isQaEngineer: false,
      }),
    ).toBe('scope')
  })

  it('always names a reason when a requested upload cannot proceed', () => {
    // The property that matters: no combination of failed preconditions may
    // return null, because null renders nothing and nothing is the bug.
    for (const isAllProjects of [true, false]) {
      for (const isQaEngineer of [true, false]) {
        const canProceed = !isAllProjects && isQaEngineer
        const reason = resolveUploadBlockedReason({
          uploadRequested: true,
          isAllProjects,
          isQaEngineer,
        })
        expect(
          reason === null,
          `isAllProjects=${isAllProjects} isQaEngineer=${isQaEngineer} returned ${reason}`,
        ).toBe(canProceed)
      }
    }
  })
})
