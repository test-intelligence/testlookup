/**
 * W24 ADM-05 — the Project data cards' model: every state, singular / plural, sizes, the 50 MB
 * pre-check, and refusals read as the server's sentence (never Axios's "Request failed …").
 */
import { describe, expect, it } from 'vitest'

import {
  activeJobOf,
  clearedSentence,
  DOWNLOAD_LABEL,
  ERROR_SENTENCES,
  EXPORT_LABEL,
  EXPORT_QUEUED,
  exportCardState,
  exportSummaryLine,
  formatProgress,
  formatSize,
  IMPORT_FAILED_FALLBACK,
  IMPORT_QUEUED,
  importJobState,
  importReportLines,
  importSummaryLine,
  newestJob,
  SIGNED_ELSEWHERE,
  SIGNED_HERE,
  signatureLine,
  skippedSentence,
  UI_SENTENCES,
  UNSIGNED,
  validateImportFile,
  validateImportName,
} from './projectDataModel'

const MB = 1024 * 1024

describe('formatters', () => {
  it('formatSize: 1024-based with the server unit names, one decimal for MB', () => {
    expect(formatSize(Math.round(2.1 * MB))).toBe('2.1 MB')
    expect(formatSize(3 * 1024)).toBe('3 KB')
    expect(formatSize(812)).toBe('812 B')
    expect(formatSize(50 * MB)).toBe('50.0 MB')
    expect(formatSize(-1)).toBe('')
  })

  it('formatProgress: "Exporting… 12 of 30 tables", "Importing… 5 of 30 tables", singular for a plan of one', () => {
    expect(formatProgress({ done: 12, total: 30 })).toBe('Exporting… 12 of 30 tables')
    expect(formatProgress({ done: 5, total: 30 }, 'import')).toBe('Importing… 5 of 30 tables')
    expect(formatProgress({ done: 0, total: 1 })).toBe('Exporting… 0 of 1 table')
    expect(formatProgress({ done: 40, total: 30 }), 'done never passes total').toBe('Exporting… 30 of 30 tables')
    expect(formatProgress(null)).toBe('Exporting…')
    expect(formatProgress({ done: 0, total: 0 }, 'import')).toBe('Importing…')
  })
})

describe('exportCardState', () => {
  it('no export yet: the sentence and an enabled Export', () => {
    const s = exportCardState(null)
    expect(s).toMatchObject({ kind: 'none', line: UI_SENTENCES.no_export, detail: null, again: false })
    expect(s.button).toEqual({ label: EXPORT_LABEL, action: 'export', disabled: false })
  })

  it('queued then running: the progress line, Export disabled', () => {
    expect(exportCardState({ status: 'queued' })).toMatchObject({ kind: 'running', line: EXPORT_QUEUED, button: { disabled: true } })
    const running = exportCardState({ status: 'running', progress: { done: 12, total: 30 } })
    expect(running).toMatchObject({ kind: 'running', line: 'Exporting… 12 of 30 tables', button: { label: EXPORT_LABEL, disabled: true } })
  })

  it('ready: rows and tables, then the signature + "Available until …", Download (2.1 MB) and Export again', () => {
    const now = Date.now()
    const job = {
      status: 'succeeded',
      bytes: Math.round(2.1 * MB),
      counts: { test_plans: 4000, releases: 210 },
      signature: 'signed',
      expires_at: new Date(now + 3_600_000).toISOString(),
      download_path: '/api/v1/project-data-jobs/j1/download',
    }
    const s = exportCardState(job, now)
    expect(s.kind).toBe('ready')
    expect(s.line).toBe('4,210 rows in 2 tables')
    expect(s.signature).toBe('Signed by this deployment')
    expect(s.detail).toMatch(/^Available until .+\.$/)
    expect(s.button).toEqual({ label: 'Download (2.1 MB)', action: 'download', disabled: false })
    expect(s.again).toBe(true)
  })

  it('ready, singular: "1 row in 1 table", "Unsigned" without an expiry; no size → plain Download', () => {
    const now = Date.now()
    const s = exportCardState({ status: 'succeeded', counts: { releases: 1 }, signature: 'unsigned', download_path: '/x' }, now)
    expect(s.line).toBe('1 row in 1 table')
    expect(s.signature).toBe(UNSIGNED)
    expect(s.detail).toBeNull()
    expect(s.button.label).toBe(DOWNLOAD_LABEL)
  })

  // W25a (W24 F-8): `job.counts` carries every exported table, zeros included; the line counts only the tables
  // that hold rows (as the import line does).
  it('the summary counts only tables with rows: zeros are left out, singular for 1', () => {
    expect(exportSummaryLine({ status: 'succeeded', counts: { a: 4200, b: 0, c: 10 } })).toBe('4,210 rows in 2 tables')
    expect(exportSummaryLine({ status: 'succeeded', counts: { a: 1, b: 0 } })).toBe('1 row in 1 table')
    expect(exportSummaryLine({ status: 'succeeded', counts: { test_suites: 3, managed_test_cases: 8, releases: 0 } }))
      .toBe('11 rows in 2 tables')
  })

  it('nothing exported reads "0 rows in 0 tables" (all zero, empty or missing counts)', () => {
    expect(exportSummaryLine({ status: 'succeeded', counts: { a: 0, b: 0 } })).toBe('0 rows in 0 tables')
    expect(exportSummaryLine({ status: 'succeeded', counts: {} })).toBe('0 rows in 0 tables')
    expect(exportSummaryLine({ status: 'succeeded', counts: null })).toBe('0 rows in 0 tables')
    expect(exportSummaryLine({ status: 'succeeded' })).toBe('0 rows in 0 tables')
  })

  it('the ready card line leaves a zero-count table out', () => {
    const s = exportCardState({ status: 'succeeded', counts: { test_plans: 4000, releases: 210, quality_gates: 0 }, download_path: '/x' })
    expect(s.line).toBe('4,210 rows in 2 tables')
  })

  it('expired: by status, by the viewer clock past expires_at, or without a download path', () => {
    const now = Date.now()
    const past = new Date(now - 1000).toISOString()
    for (const job of [
      { status: 'expired' },
      { status: 'succeeded', download_path: '/x', expires_at: past },
      { status: 'succeeded' },
    ]) {
      const s = exportCardState(job, now)
      expect(s).toMatchObject({ kind: 'expired', line: ERROR_SENTENCES.export_expired, again: false })
      expect(s.button).toEqual({ label: EXPORT_LABEL, action: 'export', disabled: false })
    }
    expect(ERROR_SENTENCES.export_expired).toBe('This export was removed after 72 hours; start a new one.')
  })

  it("failed: the server's sentence, Export enabled", () => {
    const sentence = "The export could not be built (OperationalError)."
    expect(exportCardState({ status: 'failed', error: { code: 'export_build', message: sentence } })).toMatchObject({
      kind: 'failed', line: sentence, button: { disabled: false },
    })
    expect(exportCardState({ status: 'failed' }).line).toBe(ERROR_SENTENCES.export_failed)
  })
})

describe('validateImportFile (the 50 MB pre-check, in the server words)', () => {
  it('accepts a .zip up to 50 MB (by name or by type)', () => {
    expect(validateImportFile({ name: 'payment.zip', size: 3 * 1024 })).toBeNull()
    expect(validateImportFile({ name: 'PAYMENT.ZIP', size: 50 * MB })).toBeNull()
    expect(validateImportFile({ name: 'export', size: 10, type: 'application/zip' })).toBeNull()
  })

  it('over 50 MB → "The file is larger than 50 MB."', () => {
    expect(validateImportFile({ name: 'big.zip', size: 60 * MB })).toBe('The file is larger than 50 MB.')
    expect(validateImportFile({ name: 'big.zip', size: 50 * MB + 1 })).toBe(ERROR_SENTENCES.upload_too_large)
  })

  it('the optional name: empty is fine, otherwise 2 to 255 characters after trimming', () => {
    expect(validateImportName('')).toBeNull()
    expect(validateImportName('   ')).toBeNull()
    expect(validateImportName('QA copy')).toBeNull()
    expect(validateImportName(' x ')).toBe('A project name of 2 to 255 characters is required.')
    expect(validateImportName('n'.repeat(256))).toBe(ERROR_SENTENCES.name_invalid)
  })

  it('no file, not a zip, or empty → upload_empty', () => {
    expect(validateImportFile(null)).toBe(ERROR_SENTENCES.upload_empty)
    expect(validateImportFile({ name: 'notes.txt', size: 10, type: 'text/plain' })).toBe(ERROR_SENTENCES.upload_empty)
    expect(validateImportFile({ name: 'empty.zip', size: 0 })).toBe(ERROR_SENTENCES.upload_empty)
  })
})

describe('the import report', () => {
  const report = {
    project_id: 'p-new',
    project_name: 'Payment Service (imported)',
    inserted: { test_plans: 3, test_cases_unused: 0, releases: 1 },
    switched_off: { webhook_subscriptions: 1, run_cadences: 2, notification_transition_policies: 1 },
    skipped: [{ entity: 'test_plan_items', reason: 'row_unresolved', count: 1 }],
    cleared: [{ entity: 'managed_test_cases', column: 'assignee_id', reason: 'assignment', count: 2 }],
    signature: 'valid',
  }

  it('the created line and the Open button name the new project', () => {
    const lines = importReportLines(report)
    expect(lines.created).toBe('Created Payment Service (imported).')
    expect(lines.openLabel).toBe('Open Payment Service (imported)')
    expect(lines.signature).toBe(SIGNED_HERE)
  })

  it('inserted: non-zero only, "N rows", singular for 1', () => {
    expect(importReportLines(report).inserted.map((l) => [l.entity, l.text])).toEqual([
      ['test_plans', '3 rows'],
      ['releases', '1 row'],
    ])
  })

  it('switched off: why each one is off (secret required, paused), singular for 1', () => {
    expect(importReportLines(report).switchedOff.map((l) => [l.entity, l.text])).toEqual([
      ['webhook_subscriptions', '1 row switched off — secret required'],
      ['run_cadences', '2 rows paused for 90 days'],
      ['notification_transition_policies', '1 row switched off'],
    ])
  })

  it('a reset review reads "returned to draft", never "switched off" (W24 demo)', () => {
    const lines = importReportLines({ ...report, switched_off: { 'managed_test_cases.review_reset': 1 } }).switchedOff
    expect(lines.map((l) => [l.entity, l.text])).toEqual([
      ['managed_test_cases.review_reset', '1 row returned to draft — review requests are not exported'],
    ])
  })

  it('skipped and cleared: the SINGULAR for 1 ("1 row was skipped: …"), the plural otherwise', () => {
    expect(importReportLines(report).notImported.map((l) => [l.entity, l.text])).toEqual([
      ['test_plan_items', '1 row was skipped: it pointed at a row that is not in the export'],
      ['managed_test_cases.assignee_id', '2 assignments were cleared: the new project has no other members yet'],
    ])
    expect(skippedSentence(3, 'row_unresolved')).toBe('3 rows were skipped: it pointed at a row that is not in the export')
    expect(clearedSentence(1, 'user_not_found')).toBe('1 assignment was cleared: the person does not exist here')
    expect(skippedSentence(1200, 'row_unresolved')).toMatch(/^1,200 rows were skipped: /)
  })

  it('the collapsed summary: rows in tables, singular for 1', () => {
    expect(importSummaryLine(report)).toBe('4 rows in 2 tables')
    expect(importSummaryLine({ inserted: { releases: 1 } })).toBe('1 row in 1 table')
  })

  it('signatures: this deployment, another deployment, unsigned', () => {
    expect(signatureLine('valid')).toBe(SIGNED_HERE)
    expect(signatureLine('unknown_key')).toBe(SIGNED_ELSEWHERE)
    expect(signatureLine('unsigned')).toBe(UNSIGNED)
    expect(signatureLine(undefined)).toBe(UNSIGNED)
  })
})

describe('importJobState and activeJobOf', () => {
  it('queued, running, done, failed', () => {
    expect(importJobState({ status: 'queued' })).toEqual({ kind: 'running', line: IMPORT_QUEUED })
    expect(importJobState({ status: 'running', progress: { done: 5, total: 30 } })).toEqual({ kind: 'running', line: 'Importing… 5 of 30 tables' })
    expect(importJobState({ status: 'succeeded', import_report: { project_name: 'X (imported)' } })).toEqual({ kind: 'done', line: 'Created X (imported).' })
    const sentence = 'The archive does not match its manifest (part-00001.jsonl); nothing was imported.'
    expect(importJobState({ status: 'failed', error: { code: 'tampered', message: sentence } })).toEqual({ kind: 'failed', line: sentence })
    expect(importJobState({ status: 'failed' }).line).toBe(IMPORT_FAILED_FALLBACK)
  })

  it('activeJobOf: the newest queued / running job of a newest-first list', () => {
    const items = [{ id: 'a', status: 'succeeded' }, { id: 'b', status: 'running' }, { id: 'c', status: 'queued' }]
    expect(activeJobOf(items)?.id).toBe('b')
    expect(activeJobOf([{ id: 'a', status: 'failed' }])).toBeNull()
    expect(activeJobOf(undefined)).toBeNull()
  })

  it('newestJob: undefined while loading, null for an empty list, else the first item', () => {
    expect(newestJob(undefined)).toBeUndefined()
    expect(newestJob({ items: [], total: 0 })).toBeNull()
    expect(newestJob({ items: [{ id: 'a' }, { id: 'b' }], total: 2 })).toEqual({ id: 'a' })
  })
})
