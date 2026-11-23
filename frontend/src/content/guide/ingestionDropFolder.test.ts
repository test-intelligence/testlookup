/**
 * D-93 (2026-11-21): the in-app guide had no drop-folder section, though the
 * drop-folder watcher is on by default on every install. The repository guide
 * (user-guide/getting-results-in.md) covered it; the in-app one now does too.
 */
import { describe, expect, it } from 'vitest'

import { anchorIds } from './slug'
import { DOC_SOURCES } from './sources'

describe('in-app guide — Getting results in: drop folder (D-93)', () => {
  const page = DOC_SOURCES['ingestion'] ?? ''

  it('has a "Drop folder" section with a linkable anchor', () => {
    expect(anchorIds(page)).toContain('drop-folder-object-store')
  })

  it('names the folder layout, the file written last, and the flag that switches it', () => {
    expect(page).toContain('<project>/runs/<build>/')
    expect(page).toContain('upload_complete.json')
    expect(page).toContain('drop_folder_watcher')
  })
})
