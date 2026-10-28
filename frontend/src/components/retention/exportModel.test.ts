/**
 * W22b ACT-03 — the pure copy of export-before-delete.
 *
 * Every sentence the export step, the execute section and the deletion jobs
 * card show is pinned here once (§4.3).
 */
import { describe, expect, it } from 'vitest'
import { RESULT_STATUS_TOKEN } from '@/components/ui/statusVocabulary'
import {
  ARCHIVE_NOT_BACKUP,
  archiveLines,
  AWAITING_TONE,
  bytesText,
  collectByLine,
  downloadComplete,
  downloadLabel,
  executeParagraph,
  executeToast,
  expiredLine,
  fallbackArchiveName,
  jobErrorLine,
  jobKindLine,
  jobStatusLabel,
  jobStatusTone,
  JOBS_LOAD_ERROR,
  NOT_MEASURED,
  overCapTitle,
  reclaimedLines,
  reclaimLine,
  showingLine,
  TARGET_OPTIONS,
  tierLine,
  type JobLike,
} from './exportModel'

const MB = 1024 * 1024
const JOB_ID = 'b7c1d2e3-0a1b-4c2d-8e3f-a1b2c3d4e5f6'

function job(overrides: Partial<JobLike> = {}): JobLike {
  return { id: JOB_ID, job_kind: 'criteria', status: 'completed', ...overrides }
}

describe('the export step copy', () => {
  it('defaults to deleting without an archive and offers both targets', () => {
    expect(TARGET_OPTIONS.map((o) => o.label)).toEqual([
      'Delete without an archive',
      'Download an archive first',
      'Keep an archive in storage',
    ])
    expect(TARGET_OPTIONS[0].value).toBe('none')
  })

  it('says the archive is not a backup', () => {
    expect(ARCHIVE_NOT_BACKUP).toBe('Archive, not a backup — it cannot be re-imported.')
  })

  it('reads a tier estimate, and an unmeasured one never as 0', () => {
    expect(tierLine({ tier: 'summary', archive_bytes: 2 * MB, measured: true })).toMatch(
      /^Summary — archive ≈ /,
    )
    expect(tierLine({ tier: 'full', archive_bytes: null, measured: false })).toBe(
      `Full — archive ${NOT_MEASURED}`,
    )
    expect(tierLine({ tier: 'evidence', archive_bytes: 5, measured: false })).toBe(
      'Evidence — archive not measured',
    )
  })

  it('reads the reclaim estimate', () => {
    expect(reclaimLine({ reclaim_bytes: 180 * MB })).toMatch(/^Reclaims ≈ /)
    expect(reclaimLine({ reclaim_bytes: null })).toBe('Reclaims not measured')
  })

  it('titles an over-cap tier with the limit', () => {
    expect(overCapTitle(512 * MB)).toBe('Over the 512 MB limit')
  })

  it('writes the execute paragraph and toast per target', () => {
    expect(executeParagraph({ tier: 'evidence', target: 'collect' })).toBe(
      'An evidence archive is built and verified first. Nothing is deleted until you download it and confirm you have saved it.',
    )
    expect(executeParagraph({ tier: 'summary', target: 'retain' })).toBe(
      'A summary archive is built and verified first, kept in storage, and then the runs are deleted.',
    )
    expect(executeParagraph(null)).toBeNull()
    expect(executeToast(12, { tier: 'evidence', target: 'collect' })).toBe(
      'Archiving 12 runs — download it from Deletion jobs when it is ready',
    )
    expect(executeToast(1, { tier: 'full', target: 'retain' })).toBe(
      'Archiving 1 run, then deleting them — track it in Deletion jobs',
    )
    expect(executeToast(3, undefined)).toBeNull()
  })
})

describe('the deletion jobs card copy', () => {
  it('labels every status', () => {
    expect(
      ['running', 'awaiting_collection', 'completed', 'partial', 'failed', 'expired', 'queued', 'previewed'].map(
        jobStatusLabel,
      ),
    ).toEqual([
      'Running',
      'Awaiting collection',
      'Completed',
      'Partial',
      'Failed',
      'Expired',
      'Queued',
      'Previewed',
    ])
    expect(jobStatusLabel('mystery')).toBe('mystery')
  })

  it('tones each status through the one vocabulary', () => {
    expect(jobStatusTone('completed')).toBe(RESULT_STATUS_TOKEN.passed)
    expect(jobStatusTone('failed')).toBe(RESULT_STATUS_TOKEN.failed)
    expect(jobStatusTone('partial')).toBe(RESULT_STATUS_TOKEN.flaky)
    expect(jobStatusTone('running')).toBe(RESULT_STATUS_TOKEN.unknown)
    expect(jobStatusTone('queued')).toBe(RESULT_STATUS_TOKEN.unknown)
    expect(jobStatusTone('expired')).toBe(RESULT_STATUS_TOKEN.skipped)
    expect(jobStatusTone('awaiting_collection')).toBe(AWAITING_TONE)
  })

  it('reads the job kind with the run count only when known', () => {
    expect(jobKindLine(job({ export_summary: { run_count: 12 } }))).toBe('criteria · 12 runs')
    expect(jobKindLine(job({ counts: { postgres: { runs: 1 } } }))).toBe('criteria · 1 run')
    expect(jobKindLine(job({ job_kind: 'scheduled' }))).toBe('scheduled purge')
  })

  it('reads the archive column', () => {
    expect(archiveLines(job())).toEqual({ primary: '—', secondary: null })
    expect(
      archiveLines(job({ export_tier: 'evidence', export_target: 'collect', export_verified_at: '2026-10-28T14:02:00Z' })),
    ).toEqual({ primary: 'Evidence · collect', secondary: 'verified' })
    expect(archiveLines(job({ export_tier: 'full', export_target: 'retain' })).secondary).toBe('not verified')
  })

  it('reads reclaimed bytes, the retained net and the ratio; unmeasured never as 0', () => {
    expect(reclaimedLines(job({ bytes_reclaimed: null }))).toEqual([NOT_MEASURED])
    const retained = reclaimedLines(
      job({ export_tier: 'summary', export_target: 'retain', bytes_reclaimed: 130 * MB, export_bytes: 10 * MB }),
    )
    expect(retained).toHaveLength(3)
    expect(retained[0]).toBe(bytesText(130 * MB))
    expect(retained[1]).toBe(`net ${bytesText(120 * MB)}`)
    expect(retained[2]).toBe('ratio 1:13')
    expect(bytesText(130 * MB)).not.toMatch(/≈/)
    const collected = reclaimedLines(
      job({ export_tier: 'summary', export_target: 'collect', bytes_reclaimed: 130 * MB, export_bytes: 10 * MB }),
    )
    expect(collected.some((line) => line.startsWith('net'))).toBe(false)
  })

  it('reads the expired sentence and a failure', () => {
    expect(expiredLine()).toBe('Not collected within 72 hours — nothing was deleted; the archive was removed.')
    expect(expiredLine(1)).toBe('Not collected within 1 hour — nothing was deleted; the archive was removed.')
    expect(jobErrorLine(job({ status: 'expired', error: null }))).toBe(expiredLine())
    expect(jobErrorLine(job({ status: 'failed', error: 'Export failed — nothing was deleted: x.' }))).toBe(
      'Export failed — nothing was deleted: x.',
    )
    expect(jobErrorLine(job())).toBeNull()
  })

  it('reads the collect section', () => {
    const expires = '2026-10-31T19:02:00Z'
    expect(collectByLine(job({ collect_expires_at: expires }), () => 'Oct 31, 2026, 14:02')).toBe(
      'Collect by Oct 31, 2026, 14:02 — after that nothing is deleted and the archive is removed.',
    )
    expect(collectByLine(job(), () => 'x')).toBeNull()
    expect(downloadLabel(job())).toBe('Download archive')
    expect(downloadLabel(job({ export_bytes: 14 * MB }))).toBe(`Download archive (${bytesText(14 * MB)})`)
  })

  it('accepts a download only when its size is the archive size', () => {
    const awaiting = job({ status: 'awaiting_collection', export_bytes: 1234 })
    expect(downloadComplete({ size: 1234 }, awaiting)).toBe(true)
    expect(downloadComplete({ size: 1000 }, awaiting)).toBe(false)
    expect(downloadComplete(null, awaiting)).toBe(false)
    expect(downloadComplete({ size: 0 }, job({ export_bytes: null }))).toBe(false)
  })

  it('carries the load error, the count line and the fallback name', () => {
    expect(JOBS_LOAD_ERROR).toBe('Could not load the deletion jobs.')
    expect(showingLine(3, 5)).toBe('Showing 3 of 5')
    expect(fallbackArchiveName(JOB_ID)).toBe('deletion-archive-b7c1d2e3.zip')
  })
})
