/**
 * W03 AIH-04: the AI history's URL contract (contract §9.2). Every helper keeps the keys it does not own (`tab`,
 * the Tests filters), "one picker drives both" (an AI version moves `report_version`, a report version moves
 * `ai_version`), and an old `?report_version=` link still lands on the version that published that report.
 */
import { describe, expect, it } from 'vitest'

import { versionSummary } from './aiHistory.fixtures'
import {
  applyAiBaseline,
  applyAiCompare,
  applyAiVersion,
  applyReportVersion,
  digestVersionForReport,
  evidenceHref,
  isSnapshotVersion,
  readAiBaseline,
  readAiCompare,
  readAiVersion,
  readReportVersion,
  selectedDigestVersion,
} from './aiHistoryUrl'

const q = (s: string) => new URLSearchParams(s)

// v1 cited report 1, v2 published nothing, v3 cited report 2.
const VERSIONS = [
  versionSummary(3, { decision_report_ref: { report_id: 'r2', report_version: 2 } }),
  versionSummary(2, { decision_report_ref: null }),
  versionSummary(1, { decision_report_ref: { report_id: 'r1', report_version: 1 } }),
]

describe('reading the URL', () => {
  it('reads ai_version, ai_compare, ai_baseline and report_version, rejecting malformed values', () => {
    expect(readAiVersion(q('ai_version=3'))).toBe(3)
    for (const bad of ['ai_version=0', 'ai_version=-1', 'ai_version=1.5', 'ai_version=abc', 'ai_version=', '']) {
      expect(readAiVersion(q(bad)), bad).toBeNull()
    }
    expect(readAiCompare(q('ai_compare=2'))).toBe(2)
    expect(readAiCompare(q('ai_compare=live'))).toBe('live')
    expect(readAiCompare(q('ai_compare=latest'))).toBeNull()
    expect(readAiCompare(q(''))).toBeNull()
    expect(readAiBaseline(q('ai_baseline=44444444-4444-4444-8444-100000000002'))).toBe('44444444-4444-4444-8444-100000000002')
    expect(readAiBaseline(q('ai_baseline=not-a-uuid'))).toBeNull()
    expect(readAiBaseline(q('ai_baseline=../../admin'))).toBeNull()
    expect(readReportVersion(q('report_version=2'))).toBe(2)
    expect(readReportVersion(q('report_version=x'))).toBeNull()
  })
})

describe('the viewed version', () => {
  it('an explicit ai_version wins, else the version that published an old report_version link, else Current', () => {
    expect(selectedDigestVersion(VERSIONS, 2, 1)).toBe(2)
    expect(selectedDigestVersion(VERSIONS, null, 1)).toBe(1)
    expect(selectedDigestVersion(VERSIONS, null, 2)).toBe(3)
    // A report no stored version cites: the live view with that report (the pre-W03 behaviour).
    expect(selectedDigestVersion(VERSIONS, null, 9)).toBeNull()
    expect(selectedDigestVersion(VERSIONS, null, null)).toBeNull()
    // A pruned / unknown version stays named, so the snapshot can say it no longer exists.
    expect(selectedDigestVersion(VERSIONS, 7, null)).toBe(7)
  })

  it('the newest digest wins when two cite the same report', () => {
    const both = [versionSummary(4, { decision_report_ref: { report_id: 'r', report_version: 3 } }), versionSummary(5, { decision_report_ref: { report_id: 'r', report_version: 3 } })]
    expect(digestVersionForReport(both, 3)).toBe(5)
  })

  it('the latest version is the current analysis, not a snapshot', () => {
    expect(isSnapshotVersion(3, 3)).toBe(false)
    expect(isSnapshotVersion(null, 3)).toBe(false)
    expect(isSnapshotVersion(1, 3)).toBe(true)
    expect(isSnapshotVersion(1, null)).toBe(true)
  })
})

describe('the Analysis picker drives report_version (applyAiVersion)', () => {
  it('sets ai_version and the decision report it cited, keeps tab and the Tests filters, closes the diff', () => {
    const next = applyAiVersion(q('tab=analysis&status=FAILED&page=2&ai_compare=3'), 1, VERSIONS)
    expect(next.get('ai_version')).toBe('1')
    expect(next.get('report_version')).toBe('1')
    expect(next.get('ai_compare')).toBeNull()
    expect(next.get('tab')).toBe('analysis')
    expect(next.get('status')).toBe('FAILED')
    expect(next.get('page')).toBe('2')
  })

  it('a version that published no report clears report_version (the latest report, not a stale one)', () => {
    const next = applyAiVersion(q('tab=analysis&report_version=1'), 2, VERSIONS)
    expect(next.get('ai_version')).toBe('2')
    expect(next.get('report_version')).toBeNull()
  })

  it('Current clears both', () => {
    const next = applyAiVersion(q('tab=analysis&ai_version=1&report_version=1&ai_baseline=x'), null, VERSIONS)
    expect(next.get('ai_version')).toBeNull()
    expect(next.get('report_version')).toBeNull()
    expect(next.get('ai_baseline')).toBe('x')
    expect(next.get('tab')).toBe('analysis')
  })

  it('does not mutate the params it was given', () => {
    const before = q('tab=analysis')
    applyAiVersion(before, 1, VERSIONS)
    expect(before.toString()).toBe('tab=analysis')
  })
})

describe('the Evidence report select drives ai_version (applyReportVersion)', () => {
  it('sets report_version and the digest that cited it', () => {
    const next = applyReportVersion(q('tab=evidence'), 2, VERSIONS)
    expect(next.get('report_version')).toBe('2')
    expect(next.get('ai_version')).toBe('3')
    expect(next.get('tab')).toBe('evidence')
  })

  it('a report no digest cites clears ai_version; the latest report clears both', () => {
    let next = applyReportVersion(q('tab=evidence&ai_version=1'), 9, VERSIONS)
    expect(next.get('report_version')).toBe('9')
    expect(next.get('ai_version')).toBeNull()
    next = applyReportVersion(q('tab=evidence&ai_version=1&report_version=1'), null, VERSIONS)
    expect(next.get('report_version')).toBeNull()
    expect(next.get('ai_version')).toBeNull()
  })

  it('the two pickers agree: picking an AI version then its report on Evidence changes nothing else', () => {
    const fromAnalysis = applyAiVersion(q('tab=analysis'), 1, VERSIONS)
    const fromEvidence = applyReportVersion(fromAnalysis, Number(fromAnalysis.get('report_version')), VERSIONS)
    expect(fromEvidence.get('ai_version')).toBe('1')
    expect(fromEvidence.get('report_version')).toBe('1')
  })

  it('closes an open diff only when the viewed version moved', () => {
    expect(applyReportVersion(q('ai_version=1&ai_compare=2'), 1, VERSIONS).get('ai_compare')).toBe('2')
    expect(applyReportVersion(q('ai_version=1&ai_compare=2'), 2, VERSIONS).get('ai_compare')).toBeNull()
  })
})

describe('compare, baseline and the evidence link', () => {
  it('opens and closes the diff, keeping the rest', () => {
    expect(applyAiCompare(q('tab=analysis&ai_version=1'), 'live').toString()).toBe('tab=analysis&ai_version=1&ai_compare=live')
    expect(applyAiCompare(q('tab=analysis&ai_compare=2'), null).toString()).toBe('tab=analysis')
  })

  it('writes only a uuid-shaped baseline', () => {
    expect(applyAiBaseline(q('tab=analysis'), '44444444-4444-4444-8444-100000000002').get('ai_baseline')).toBe('44444444-4444-4444-8444-100000000002')
    expect(applyAiBaseline(q('tab=analysis&ai_baseline=44444444-4444-4444-8444-100000000002'), null).get('ai_baseline')).toBeNull()
    expect(applyAiBaseline(q('tab=analysis'), 'javascript:alert(1)').get('ai_baseline')).toBeNull()
  })

  it('links the Evidence tab at a report with the AI version that cited it', () => {
    expect(evidenceHref('run-1', 2, 3)).toBe('/runs/run-1?tab=evidence&report_version=2&ai_version=3')
  })
})
