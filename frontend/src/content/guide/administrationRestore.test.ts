/**
 * D-74 (2026-11-21): the guide said "There is no un-archive in the app" while
 * Projects › Archived has Restore (W17 ADM-04). It now says how a project comes
 * back and what stays revoked (S-D-3: the shared QA-lead account returns
 * through Reset QA Lead password).
 */
import { describe, expect, it } from 'vitest'

import { DOC_SOURCES } from './sources'

describe('in-app guide — Administration: restoring an archived project (D-74)', () => {
  const page = DOC_SOURCES['administration'] ?? ''

  it('no longer says there is no un-archive', () => {
    expect(page).not.toMatch(/no un-archive/i)
  })

  it('names Restore, what stays revoked, and how the QA-lead account comes back', () => {
    expect(page).toContain('**Restore** on the **Archived** tab')
    expect(page).toContain('Its API keys stay revoked')
    expect(page).toContain('**Reset QA Lead password**')
  })
})
