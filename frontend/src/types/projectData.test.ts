/**
 * W24 ADM-05: the project-data vocabulary. Its values are pinned against the server by
 * `backend/tests/test_project_export_vocab_parity.py` (which reads this file as text); here: every sentence is
 * present and non-empty, the placeholders fill, and the status helpers agree with the lists.
 */
import { describe, expect, it } from 'vitest'

import {
  ACTIVE_STATUSES,
  ERROR_SENTENCES,
  EXPORT_TTL_HOURS,
  fillSentence,
  IMPORT_UPLOAD_MAX_BYTES,
  isActiveJob,
  JOB_KINDS,
  JOB_POLL_MS,
  JOB_STATUSES,
  JOBS_LIST_LIMIT,
  SKIP_REASONS,
  TERMINAL_STATUSES,
  UI_SENTENCES,
} from './projectData'

describe('projectData vocabulary', () => {
  it('declares the job kinds and statuses, terminal and active partitioning the statuses', () => {
    expect(JOB_KINDS).toEqual(['export', 'import'])
    expect(JOB_STATUSES).toEqual(['queued', 'running', 'succeeded', 'failed', 'expired'])
    expect([...ACTIVE_STATUSES, ...TERMINAL_STATUSES].sort()).toEqual([...JOB_STATUSES].sort())
  })

  it('keeps the numbers the server uses', () => {
    expect(IMPORT_UPLOAD_MAX_BYTES).toBe(50 * 1024 * 1024)
    expect(EXPORT_TTL_HOURS).toBe(72)
    expect(JOBS_LIST_LIMIT).toBe(10)
    expect(JOB_POLL_MS).toBe(2000)
  })

  it.each([
    ['ERROR_SENTENCES', ERROR_SENTENCES],
    ['UI_SENTENCES', UI_SENTENCES],
    ['SKIP_REASONS', SKIP_REASONS],
  ])('every %s entry is a non-empty, trimmed sentence', (_name, table) => {
    const entries = Object.entries(table)
    expect(entries.length).toBeGreaterThan(0)
    for (const [key, sentence] of entries) {
      expect(sentence, key).toBe(sentence.trim())
      expect(sentence.length, key).toBeGreaterThan(0)
    }
  })

  it('carries the sentences the cards and the 4xx answers render', () => {
    expect(ERROR_SENTENCES.export_expired).toBe('This export was removed after 72 hours; start a new one.')
    expect(ERROR_SENTENCES.upload_too_large).toBe('The file is larger than 50 MB.')
    expect(UI_SENTENCES.load_exports_failed).toBe("Could not load this project's exports.")
    expect(UI_SENTENCES.load_imports_failed).toBe('Could not load your imports.')
    expect(UI_SENTENCES.export_heading).toBe('Export settings and catalog')
    expect(UI_SENTENCES.import_heading).toBe('Import as a new project')
  })

  it('fills placeholders and leaves an unknown one as written', () => {
    expect(fillSentence(UI_SENTENCES.export_running, { done: 12, total: 30 })).toBe('Exporting… 12 of 30 tables')
    expect(fillSentence(UI_SENTENCES.skipped_one, { reason: SKIP_REASONS.row_unresolved })).toBe(
      '1 row was skipped: it pointed at a row that is not in the export',
    )
    expect(fillSentence(ERROR_SENTENCES.schema_newer, { found: 2, supported: 1 })).toContain('(schema 2)')
    expect(fillSentence('Created {name}.', {})).toBe('Created {name}.')
  })

  it('reads a job as active only while it is queued or running', () => {
    expect(isActiveJob({ status: 'queued' })).toBe(true)
    expect(isActiveJob({ status: 'running' })).toBe(true)
    for (const status of TERMINAL_STATUSES) expect(isActiveJob({ status })).toBe(false)
    expect(isActiveJob(null)).toBe(false)
    expect(isActiveJob(undefined)).toBe(false)
  })
})
