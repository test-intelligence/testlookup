/**
 * W24 ADM-05 — the Project data cards' model: every state, singular / plural, sizes, the file
 * pre-check, and refusals read as the server's sentence (never Axios's "Request failed …").
 * W30 (ADM-05 run history): the history form, the estimate line, the phase / upload lines, the part
 * plan and the history report — every count in the singular for 1.
 */
import { describe, expect, it } from 'vitest'

import {
  activeJobOf,
  clearedSentence,
  DOWNLOAD_LABEL,
  ERROR_SENTENCES,
  estimateLine,
  estimateOptionsOf,
  EXPORT_LABEL,
  EXPORT_QUEUED,
  exportBodyFromForm,
  exportCardState,
  exportFormBlocked,
  exportHistoryLine,
  exportSummaryLine,
  formatProgress,
  formatSize,
  HISTORY_DEFAULT_WINDOW,
  HISTORY_WINDOW_OPTIONS,
  historyDaysOf,
  historyOptionsFromForm,
  historyReportLines,
  IMPORT_FAILED_FALLBACK,
  IMPORT_QUEUED,
  importJobState,
  importOptionsFromForm,
  importReportLines,
  importSummaryLine,
  isLargeDownload,
  isUploadingJob,
  LARGE_DOWNLOAD_BYTES,
  largeDownloadHint,
  newestJob,
  optionsIgnoredLines,
  partPlan,
  phaseLine,
  SIGNED_ELSEWHERE,
  SIGNED_HERE,
  signatureLine,
  skippedSentence,
  UI_SENTENCES,
  UNSIGNED,
  uploadLine,
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

describe('validateImportFile (the 2 GB pre-check, in the server words — W30)', () => {
  it('accepts a .zip up to 2 GB (by name or by type); over 50 MB it travels in parts', () => {
    expect(validateImportFile({ name: 'payment.zip', size: 3 * 1024 })).toBeNull()
    expect(validateImportFile({ name: 'PAYMENT.ZIP', size: 50 * MB })).toBeNull()
    expect(validateImportFile({ name: 'big.zip', size: 60 * MB }), 'W24 refused this; W30 sends it in parts').toBeNull()
    expect(validateImportFile({ name: 'big.zip', size: 2048 * MB })).toBeNull()
    expect(validateImportFile({ name: 'export', size: 10, type: 'application/zip' })).toBeNull()
  })

  it('over 2 GB → "The file is larger than 2 GB." (W24\'s 50 MB sentence stays the single route\'s)', () => {
    expect(validateImportFile({ name: 'huge.zip', size: 2048 * MB + 1 })).toBe('The file is larger than 2 GB.')
    expect(validateImportFile({ name: 'huge.zip', size: 2.1 * 1024 * MB })).toBe(ERROR_SENTENCES.upload_too_large_history)
    expect(ERROR_SENTENCES.upload_too_large).toBe('The file is larger than 50 MB.')
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

  it('a table this version dropped (W25d retired_table): "1 row was skipped: the table no longer exists in this version"', () => {
    const lines = importReportLines({
      ...report,
      skipped: [{ entity: 'quality_gates', column: null, reason: 'retired_table', count: 1 }],
      cleared: [],
    }).notImported
    expect(lines.map((l) => [l.entity, l.text])).toEqual([
      ['quality_gates', '1 row was skipped: the table no longer exists in this version'],
    ])
    expect(skippedSentence(3, 'retired_table')).toBe('3 rows were skipped: the table no longer exists in this version')
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

// ── W30 (ADM-05 run history): run history ────────────────────────────────────────

describe('the history form (W30)', () => {
  // W37 (contract §3, deliberate): a 5th choice "One release" (value `release`) after All history.
  it('the window choices: 30 / 90 / 365 days, All history and One release (W37), 90 by default', () => {
    expect(HISTORY_WINDOW_OPTIONS.map((o) => o.label)).toEqual(['30 days', '90 days', '365 days', 'All history', 'One release'])
    expect(HISTORY_WINDOW_OPTIONS.map((o) => o.value)).toEqual(['30', '90', '365', 'all', 'release'])
    expect(HISTORY_DEFAULT_WINDOW).toBe('90')
    expect(historyDaysOf('365')).toBe(365)
    expect(historyDaysOf('all')).toBeNull()
  })

  it('unticked → no body (the W24 POST); ticked → include_history, the window (null = all) and max_runs 2000', () => {
    expect(historyOptionsFromForm({ includeHistory: false, window: '365' })).toBeUndefined()
    expect(historyOptionsFromForm({ includeHistory: true, window: '365' })).toEqual({ include_history: true, history_days: 365, max_runs: 2000 })
    expect(historyOptionsFromForm({ includeHistory: true, window: 'all' })).toEqual({ include_history: true, history_days: null, max_runs: 2000 })
    expect(estimateOptionsOf('90')).toEqual({ historyDays: 90 })
    expect(estimateOptionsOf('all')).toEqual({ historyDays: null })
  })
})

describe('estimateLine (W30)', () => {
  const measured = { postgres: true, documents: true, objects: true }

  it('measured: "About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments"', () => {
    const e = estimateLine({
      runs: 412, documents: { raw_allure_json: 9000, run_summaries: 120 }, objects: 1830,
      estimated_bytes: Math.round(412.3 * MB), over_limit: false, measured,
    })
    expect(e).toEqual({ kind: 'measured', line: 'About 412.3 MB · 412 runs · 9,120 documents · 1,830 attachments', detail: null, blocksExport: false })
  })

  it('singular for 1: "1 run · 1 document · 1 attachment"', () => {
    const e = estimateLine({ runs: 1, documents: { run_summaries: 1 }, objects: 1, estimated_bytes: 2048, over_limit: false, measured })
    expect(e.line).toBe('About 2 KB · 1 run · 1 document · 1 attachment')
  })

  it('a store that was not read says "not measured" and names itself — never 0', () => {
    const e = estimateLine({ runs: 412, documents: null, objects: 1830, estimated_bytes: null, over_limit: null, measured: { ...measured, documents: false } })
    expect(e.kind).toBe('unmeasured')
    expect(e.line).toBe('Size unknown: the document store could not be read.')
    expect(e.detail).toBe('412 runs · documents not measured · 1,830 attachments')
    expect(e.blocksExport).toBe(false)
    expect(`${e.line} ${e.detail}`).not.toMatch(/\b0 /)
    const both = estimateLine({ runs: 3, documents: null, objects: null, estimated_bytes: null, measured: { postgres: true, documents: false, objects: false } })
    expect(both.line).toBe('Size unknown: the document store and attachment storage could not be read.')
    expect(both.detail).toBe('3 runs · documents not measured · attachments not measured')
  })

  it('over 2 GB: the measured line, the over sentence, Export blocked', () => {
    const e = estimateLine({ runs: 2000, documents: { raw_allure_json: 1 }, objects: 2, estimated_bytes: 3 * 1024 * MB, over_limit: true, measured })
    expect(e).toMatchObject({ kind: 'over', line: 'About 3.0 GB · 2,000 runs · 1 document · 2 attachments', blocksExport: true })
    expect(e.detail).toBe('Larger than 2 GB — choose a shorter window.')
  })
})

describe('phaseLine, uploadLine, partPlan (W30)', () => {
  it('the catalog phase (and a W24 job without a phase) keeps "Exporting… 12 of 56 tables"', () => {
    expect(phaseLine({ done: 12, total: 56, phase: 'catalog' })).toBe('Exporting… 12 of 56 tables')
    expect(phaseLine({ done: 12, total: 56 })).toBe('Exporting… 12 of 56 tables')
    expect(phaseLine({ done: 5, total: 30, phase: 'catalog' }, 'import')).toBe('Importing… 5 of 30 tables')
  })

  it('a later phase: "Runs and results… 120 of 412", every phase label, just the label before a count', () => {
    expect(phaseLine({ done: 29, total: 56, phase: 'runs', phase_done: 120, phase_total: 412 })).toBe('Runs and results… 120 of 412')
    expect(phaseLine({ done: 56, total: 56, phase: 'documents', phase_done: 5000, phase_total: 9120 })).toBe('Stored documents… 5,000 of 9,120')
    expect(phaseLine({ done: 56, total: 56, phase: 'objects', phase_done: 100, phase_total: 1830 })).toBe('Attachments… 100 of 1,830')
    expect(phaseLine({ done: 56, total: 56, phase: 'verify' })).toBe('Verifying…')
    expect(phaseLine({ done: 56, total: 56, phase: 'index' }, 'import')).toBe('Search index…')
    expect(phaseLine({ done: 0, total: 0, phase: 'runs', phase_done: 500, phase_total: 412 }), 'done never passes total').toBe('Runs and results… 412 of 412')
    expect('Runs and results… 1,200 of 2,000'.length).toBeLessThanOrEqual(40)
  })

  it('"Uploading… 1 of 2 parts", singular for a one-part upload', () => {
    expect(uploadLine({ parts: 2, received: [1] })).toBe('Uploading… 1 of 2 parts')
    expect(uploadLine({ parts: 12, received: [] })).toBe('Uploading… 0 of 12 parts')
    expect(uploadLine({ parts: 1, received: [1] })).toBe('Uploading… 1 of 1 part')
  })

  it('partPlan: ≤ 50 MB the single POST; above, ceil(bytes / 32 MB) parts, every part but the last exactly 32 MB', () => {
    expect(partPlan(50 * MB)).toEqual({ mode: 'single', parts: 1, sizes: [50 * MB] })
    expect(partPlan(60 * MB)).toEqual({ mode: 'parted', parts: 2, sizes: [32 * MB, 28 * MB] })
    expect(partPlan(64 * MB)).toEqual({ mode: 'parted', parts: 2, sizes: [32 * MB, 32 * MB] })
    const max = partPlan(2048 * MB)
    expect(max.parts).toBe(64)
    expect(max.sizes.every((s) => s === 32 * MB)).toBe(true)
    expect(partPlan(0)).toEqual({ mode: 'single', parts: 0, sizes: [] })
  })

  // W37 (contract §3, deliberate): the hint now says large archives download straight to disk (the ticket).
  it('the large-download hint only above 512 MB', () => {
    expect(largeDownloadHint(512 * MB, 'proj')).toBeNull()
    expect(largeDownloadHint(Math.round(512.1 * MB), 'proj')).toBe(
      'Large archives download straight to your disk; the CLI also works: testlookup projects export proj --history',
    )
    expect(largeDownloadHint(null, 'proj')).toBeNull()
  })
})

describe('a history export on the Export card (W30)', () => {
  const historyCounts = () => ({
    projects: 1, managed_test_cases: 40, test_runs: 412, test_cases: 9000,
    'documents.raw_allure_json': 9000, 'documents.run_summaries': 120, objects: 1830,
  })

  it('running: the phase line ("Runs and results… 120 of 412"), Export disabled', () => {
    const s = exportCardState({ status: 'running', progress: { done: 29, total: 56, phase: 'runs', phase_done: 120, phase_total: 412 } })
    expect(s).toMatchObject({ kind: 'running', line: 'Runs and results… 120 of 412', history: null })
    expect(s.button.disabled).toBe(true)
  })

  it('ready: the rows line leaves documents / objects out; the counts move to a second line', () => {
    const job = {
      status: 'succeeded', options: { include_history: true }, counts: historyCounts(), bytes: Math.round(412.3 * MB),
      signature: 'signed', download_path: '/x', expires_at: null,
    }
    const s = exportCardState(job)
    expect(s.line).toBe('9,453 rows in 4 tables')
    expect(s.line.length).toBeLessThanOrEqual(44)
    expect(s.history).toBe('412 runs · 9,120 documents · 1,830 attachments')
    expect(s.button.label).toBe('Download (412.3 MB)')
    expect(exportHistoryLine({ counts: { test_runs: 1, 'documents.run_summaries': 1, objects: 1 } })).toBe('1 run · 1 document · 1 attachment')
  })

  it('a catalog-only export has no history line', () => {
    expect(exportHistoryLine({ counts: { projects: 1 } })).toBeNull()
    expect(exportCardState({ status: 'succeeded', counts: { projects: 1 }, download_path: '/x' }).history).toBeNull()
  })
})

describe('historyReportLines (W30)', () => {
  const history = () => ({
    runs: 412, test_cases: 9000,
    oldest_run_at: new Date(2026, 7, 3, 12).toISOString(), newest_run_at: new Date(2026, 10, 1, 12).toISOString(),
    documents: { raw_allure_json: 9000, run_summaries: 120, decision_reports: 0 },
    objects: 1830, object_bytes: Math.round(412.3 * MB),
    skipped: [
      { entity: 'objects', reason: 'too_large', count: 2 },
      { entity: 'agent_pipeline_runs', reason: 'pipeline_not_terminal', count: 1 },
    ],
    retention_paused_until: new Date(2027, 1, 1, 12).toISOString(),
    runs_past_retention: 300, retention_days: 30, recent_runs: 4, search_reindex: 'queued',
  })

  it('the runs line, the documents (non-zero only), the attachments, the skips naming what they skipped', () => {
    const lines = historyReportLines(history())
    expect(lines.imported).toBe('412 runs from Aug 3, 2026 to Nov 1, 2026')
    expect(lines.documents.map((l) => `${l.entity}: ${l.text}`)).toEqual(['raw_allure_json: 9,000 documents', 'run_summaries: 120 documents'])
    expect(lines.attachments.map((l) => `${l.entity}: ${l.text}`)).toEqual(['objects: 1,830 attachments · 412.3 MB'])
    expect(lines.skipped.map((l) => `${l.entity}: ${l.text}`)).toEqual([
      'objects: 2 attachments were skipped: it is larger than 256 MB',
      'agent_pipeline_runs: 1 row was skipped: its AI pipeline was still running at export',
    ])
  })

  it('the notes: the pause, the runs past retention, the recent runs, the reindex', () => {
    expect(historyReportLines(history()).notes).toEqual([
      'The retention purge of the new project is paused until Feb 1, 2027.',
      "Runs older than this project's retention window (30 days) will be purged on the next nightly purge.",
      '4 imported runs are from the last 7 days; an all-projects digest can list them.',
      'Search is being rebuilt for the new project.',
    ])
  })

  it('singular for 1; retention off; reindex not queued; no recent runs → no digest note', () => {
    const lines = historyReportLines({
      ...history(), runs: 1, documents: { run_summaries: 1 }, objects: 1, object_bytes: 812,
      skipped: [{ entity: 'run_summaries', reason: 'run_not_exported', count: 1 }],
      retention_days: null, recent_runs: 1, search_reindex: 'not_queued',
    })
    expect(lines.imported).toBe('1 run from Aug 3, 2026 to Nov 1, 2026')
    expect(lines.documents[0].text).toBe('1 document')
    expect(lines.attachments[0].text).toBe('1 attachment · 812 B')
    expect(lines.skipped[0].text).toBe('1 document was skipped: its run is not in the export')
    expect(lines.notes).toContain('Retention is off for this project: imported runs are kept.')
    expect(lines.notes).toContain('1 imported run is from the last 7 days; an all-projects digest can list it.')
    expect(lines.notes).toContain('Search could not be rebuilt automatically — use Reindex on the Search page.')
    expect(historyReportLines({ ...history(), recent_runs: 0 }).notes.some((n) => n.includes('digest'))).toBe(false)
  })

  it('no retention window in the report → no retention sentence, never a raw "{days}"', () => {
    const { retention_days: _omit, ...rest } = history()
    const notes = historyReportLines(rest).notes
    expect(notes.join(' ')).not.toContain('{')
    expect(notes.some((n) => n.startsWith('Runs older'))).toBe(false)
  })

  it('an uploading import reads "Uploading… n of m parts"; a later phase reads its phase line', () => {
    expect(isUploadingJob({ status: 'uploading' })).toBe(true)
    expect(importJobState({ status: 'uploading', upload: { parts: 2, received: [1] } })).toEqual({ kind: 'uploading', line: 'Uploading… 1 of 2 parts' })
    expect(importJobState({ status: 'running', progress: { done: 56, total: 56, phase: 'documents', phase_done: 5000, phase_total: 9120 } })).toEqual({
      kind: 'running', line: 'Stored documents… 5,000 of 9,120',
    })
  })
})

// ── W37: one release, the include / import options, the large download, the report's new lines ──

describe('the Export form (W37)', () => {
  const base = { includeHistory: false, window: '90' as const, releaseId: '', includeMembers: false, includeReviews: false }

  it("nothing ticked → no body (W24's POST); a day window → W30's body exactly", () => {
    expect(exportBodyFromForm(base)).toBeUndefined()
    expect(exportBodyFromForm({ ...base, includeHistory: true, window: '365' })).toStrictEqual({
      include_history: true, history_days: 365, max_runs: 2000,
    })
  })

  it('One release → history_release_id and NO history_days key; Export waits until a release is chosen', () => {
    const release = { ...base, includeHistory: true, window: 'release' as const }
    expect(exportFormBlocked(release)).toBe(true)
    expect(exportFormBlocked({ ...release, releaseId: 'rel-spring' })).toBe(false)
    expect(exportFormBlocked({ ...base, window: 'release' }), 'unticked history never waits').toBe(false)
    expect(exportFormBlocked({ ...base, includeHistory: true }), 'a day window never waits').toBe(false)
    const body = exportBodyFromForm({ ...release, releaseId: 'rel-spring' })
    expect(body).toStrictEqual({ include_history: true, max_runs: 2000, history_release_id: 'rel-spring' })
    expect(body).not.toHaveProperty('history_days')
    expect(historyDaysOf('release')).toBeNull()
  })

  it('the include boxes add a key only when ticked, with or without history', () => {
    expect(exportBodyFromForm({ ...base, includeMembers: true })).toStrictEqual({ include_members: true })
    expect(exportBodyFromForm({ ...base, includeReviews: true })).toStrictEqual({ include_reviews: true })
    expect(exportBodyFromForm({ ...base, includeHistory: true, includeMembers: true, includeReviews: true })).toStrictEqual({
      include_history: true, history_days: 90, max_runs: 2000, include_members: true, include_reviews: true,
    })
  })

  it('the estimate options: a day window stays {historyDays}; One release names the release and no day window', () => {
    expect(estimateOptionsOf('90', 'rel-spring')).toStrictEqual({ historyDays: 90 })
    expect(estimateOptionsOf('release', 'rel-spring')).toStrictEqual({ historyDays: null, historyReleaseId: 'rel-spring' })
  })
})

describe('the large download (W37, F-W30-6)', () => {
  it('a ticket above 512 MB only; at or below (or no size) the Blob path', () => {
    expect(LARGE_DOWNLOAD_BYTES).toBe(512 * MB)
    expect(isLargeDownload(512 * MB)).toBe(false)
    expect(isLargeDownload(512 * MB + 1)).toBe(true)
    expect(isLargeDownload(null)).toBe(false)
    expect(isLargeDownload(undefined)).toBe(false)
  })
})

describe('the import options and the report (W37, F-2 / F-7)', () => {
  it('the two boxes → a key only when ticked', () => {
    expect(importOptionsFromForm({ addMembers: false, keepReviews: false })).toStrictEqual({})
    expect(importOptionsFromForm({ addMembers: true, keepReviews: false })).toStrictEqual({ addMembers: true })
    expect(importOptionsFromForm({ addMembers: false, keepReviews: true })).toStrictEqual({ keepReviews: true })
  })

  it('members, owners and reviews: inserted rows, skips read with THEIR reason (never "assignments"), the unclaim suffix', () => {
    const lines = importReportLines({
      project_name: 'Payment Service (imported)',
      inserted: { project_members: 3, test_suite_owners: 1, test_case_reviews: 2, test_case_comments: 0 },
      skipped: [
        { entity: 'project_members', reason: 'email_not_found', count: 2 },
        { entity: 'project_members', reason: 'email_ambiguous', count: 1 },
        { entity: 'project_members', reason: 'role_unknown', count: 1 },
        { entity: 'test_suite_owners', reason: 'owner_not_lead', count: 1 },
        { entity: 'test_case_comments', reason: 'not_chosen', count: 4 },
      ],
      cleared: [{ entity: 'managed_test_cases', column: 'author_id', reason: 'user_not_found', count: 1 }],
      switched_off: { 'managed_test_cases.review_unclaimed': 2 },
    })
    expect(lines.inserted.map((l) => [l.entity, l.text])).toEqual([
      ['project_members', '3 rows'], ['test_suite_owners', '1 row'], ['test_case_reviews', '2 rows'],
    ])
    expect(lines.notImported.map((l) => [l.entity, l.text])).toEqual([
      ['project_members', '2 rows were skipped: no active account here has this e-mail address'],
      ['project_members', '1 row was skipped: more than one account here has this e-mail address'],
      ['project_members', '1 row was skipped: its project role is not one this deployment knows'],
      ['test_suite_owners', '1 row was skipped: the owner is not a QA lead in the new project'],
      ['test_case_comments', '4 rows were skipped: the option to bring them was not chosen'],
      ['managed_test_cases.author_id', '1 assignment was cleared: the person does not exist here'],
    ])
    expect(lines.switchedOff.map((l) => [l.entity, l.text])).toEqual([
      ['managed_test_cases.review_unclaimed', '2 rows returned to Review requested — the reviewer is not a member here'],
    ])
    expect(lines.optionsIgnored).toEqual([])
  })

  it('options_ignored: one sentence per option in the server order, once each; an unknown option adds nothing', () => {
    expect(optionsIgnoredLines(['keep_reviews', 'add_members', 'keep_reviews', 'merge_everything'])).toEqual([
      UI_SENTENCES.option_ignored_keep_reviews,
      UI_SENTENCES.option_ignored_add_members,
    ])
    expect(UI_SENTENCES.option_ignored_add_members).toBe('Add members: this file lists no members — export it again with Include members.')
    expect(optionsIgnoredLines(undefined)).toEqual([])
    expect(importReportLines({ options_ignored: ['add_members'] }).optionsIgnored).toEqual([UI_SENTENCES.option_ignored_add_members])
  })
})
