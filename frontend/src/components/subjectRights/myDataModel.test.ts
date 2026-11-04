/**
 * W31 STRAT-12: the export card's pure model (contract §4.3, §4.4) — status → sentence, size / expiry, the
 * unsigned and expired cases, and which job a card polls.
 */
import { describe, expect, it } from 'vitest'

import { subjectJobFixture, subjectJobList } from '@/test/subjectRightsMocks'
import { ERROR_SENTENCES, UI_SENTENCES } from '@/types/subjectRights'
import {
  EXPORT_QUEUED_PERSON,
  exportView,
  newestErasure,
  newestExport,
  readyLine,
  watchedJobId,
} from './myDataModel'

const MB = 1024 * 1024

describe('exportView', () => {
  it('no export yet: the sentence, the start button enabled, no Download', () => {
    expect(exportView(null)).toEqual({ kind: 'none', line: UI_SENTENCES.export_none, download: false, startDisabled: false })
  })

  it('a queued or running export: "Preparing your archive…" for the person, the start button disabled', () => {
    for (const status of ['queued', 'running'] as const) {
      const view = exportView(subjectJobFixture({ status }))
      expect(view).toEqual({ kind: 'running', line: UI_SENTENCES.export_queued, download: false, startDisabled: true })
    }
    expect(exportView(subjectJobFixture({ status: 'running' }), { audience: 'person' }).line).toBe(EXPORT_QUEUED_PERSON)
  })

  it('a ready export: size, signed, the expiry, and Download', () => {
    const now = Date.now()
    const expires = new Date(now + 3 * 3_600_000).toISOString()
    const job = subjectJobFixture({ status: 'succeeded', bytes: Math.round(1.4 * MB), expires_at: expires })
    const view = exportView(job, { now })
    expect(view.kind).toBe('ready')
    expect(view.download).toBe(true)
    expect(view.startDisabled).toBe(false)
    expect(view.line).toMatch(/^Ready — 1\.4 MB, signed\. Available until [A-Z][a-z]{2} \d{1,2}, \d{2}:\d{2}\.$/)
  })

  it('an unsigned archive never reads "signed"; no expiry drops the second sentence', () => {
    const unsigned = readyLine({ bytes: 2048, signature: 'unsigned', expires_at: new Date(Date.now() + 60_000).toISOString() })
    expect(unsigned).toMatch(/^Ready — 2 KB, unsigned\. Available until .+\.$/)
    expect(readyLine({ bytes: 812, signature: 'signed', expires_at: null })).toBe('Ready — 812 B, signed.')
    expect(readyLine({ bytes: null, signature: 'signed', expires_at: null })).toBe('Ready — size not reported, signed.')
  })

  it('a failed export reads the job error, else the generic sentence; the start button stays enabled', () => {
    const failed = subjectJobFixture({ status: 'failed' })
    expect(exportView(failed)).toMatchObject({ kind: 'failed', line: failed.error?.message, startDisabled: false })
    expect(exportView({ ...failed, error: null }).line).toBe(ERROR_SENTENCES.export_failed)
  })

  it('expired (status, past expires_at, or no download path) reads the expired sentence — never a dead Download', () => {
    const now = Date.now()
    const expired = subjectJobFixture({ status: 'expired' })
    const past = subjectJobFixture({ status: 'succeeded', expires_at: new Date(now - 1000).toISOString() })
    const pathless = subjectJobFixture({ status: 'succeeded', download_path: null })
    for (const job of [expired, past, pathless]) {
      expect(exportView(job, { now })).toEqual({ kind: 'expired', line: ERROR_SENTENCES.export_expired, download: false, startDisabled: false })
    }
  })
})

describe('the list helpers', () => {
  it('newestExport: undefined while loading, null when empty, else the newest export (erasures skipped)', () => {
    expect(newestExport(undefined)).toBeUndefined()
    expect(newestExport(subjectJobList([]))).toBeNull()
    const erasure = subjectJobFixture({ kind: 'erasure', status: 'succeeded' })
    const exported = subjectJobFixture({ status: 'succeeded' })
    expect(newestExport(subjectJobList([erasure, exported]))).toBe(exported)
    expect(newestErasure(subjectJobList([exported, erasure]))).toBe(erasure)
    expect(newestErasure(undefined)).toBeNull()
  })

  it('watchedJobId: the started job, else the newest while it is still working, else none', () => {
    const running = subjectJobFixture({ status: 'running' })
    const done = subjectJobFixture({ status: 'succeeded' })
    expect(watchedJobId('started-id', done)).toBe('started-id')
    expect(watchedJobId(null, running)).toBe(running.id)
    expect(watchedJobId(null, done)).toBeNull()
    expect(watchedJobId(null, null)).toBeNull()
  })
})
