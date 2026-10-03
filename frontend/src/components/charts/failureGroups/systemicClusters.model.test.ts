import { describe, expect, it } from 'vitest'
import { tooltipText } from '../tooltip'
import { clustersBody } from './failureGroups.fixtures'
import {
  causeLabel,
  clustersTitle,
  clusterTipContent,
  clusterWindowDays,
  EMPTY_CLUSTERS_FALLBACK,
  validateClustersResponse,
} from './systemicClusters.model'

describe('validateClustersResponse', () => {
  it('reads the clusters, keyed by membership_key (the identity), never by cluster_key', () => {
    const checked = validateClustersResponse(clustersBody())
    expect(checked.ok).toBe(true)
    if (!checked.ok) return
    expect(checked.value.clusters.map((c) => c.key)).toEqual(['a'.repeat(32), 'b'.repeat(32)])
    expect(checked.value.clusters[0]).toMatchObject({ clusterKey: 'sfc_001', size: 14, cohesion: 0.82, coFailureRuns: 9 })
    expect(checked.value.clusters[0].members[0]).toEqual({
      fingerprint: 'fp1',
      name: '<img src=x onerror="window.__xss=1">test_pay',
      failureRuns: 9,
    })
    expect(checked.value.emptyIsNormal).toBe('Most projects have no systemic clusters.')
    expect(checked.value.scopeNote).toBeNull()
    expect(checked.value.meta).not.toBeNull()
  })

  it('the SAME cluster after a sweep that swapped the ranks keeps its key (M-207c)', () => {
    const body = clustersBody()
    const [a, b] = body.items as Record<string, unknown>[]
    const swapped = clustersBody({
      items: [
        { ...a, cluster_key: 'sfc_002' },
        { ...b, cluster_key: 'sfc_001' },
      ],
    })
    const before = validateClustersResponse(body)
    const after = validateClustersResponse(swapped)
    if (!before.ok || !after.ok) throw new Error('invalid')
    expect(after.value.clusters.map((c) => c.key)).toEqual(before.value.clusters.map((c) => c.key))
  })

  it('a member-less cluster (null key) falls back to its rank key; a duplicate member set is listed once', () => {
    const body = clustersBody()
    const [a] = body.items as Record<string, unknown>[]
    const checked = validateClustersResponse(
      clustersBody({ items: [a, { ...a, label: 'dup' }, { ...a, membership_key: null, cluster_key: 'sfc_9' }] }),
    )
    if (!checked.ok) throw new Error(checked.errors.join())
    expect(checked.value.clusters.map((c) => c.key)).toEqual(['a'.repeat(32), 'rank:sfc_9'])
  })

  it('reads a filtered body’s membership note', () => {
    const checked = validateClustersResponse(
      clustersBody({ scope: { membership: 'suite', note: 'Cluster statistics are computed project-wide.' } }),
    )
    expect(checked.ok && checked.value.scopeNote).toBe('Cluster statistics are computed project-wide.')
  })

  it('rejects what it cannot draw, naming the field', () => {
    expect(validateClustersResponse(null)).toEqual({ ok: false, errors: ['invalid_type: a clusters response must be an object'] })
    expect(validateClustersResponse({ items: 'x' })).toEqual({ ok: false, errors: ['items: required_field: a list of clusters'] })
    const bad = validateClustersResponse({ items: [{ label: 'x', size: -1 }, 7, { size: 2 }] })
    expect(bad.ok).toBe(false)
    if (bad.ok) return
    expect(bad.errors).toEqual([
      'items[0].size: invalid_type: a whole number of tests',
      'items[1]: invalid_type: a cluster must be an object',
      'items[2].label: required_field',
    ])
    const meta = validateClustersResponse({ items: [], meta: { nope: 1 } })
    expect(meta.ok).toBe(false)
  })

  it('caps the error list for a hostile body', () => {
    const bad = validateClustersResponse({ items: Array.from({ length: 50 }, () => 1) })
    expect(!bad.ok && bad.errors.length).toBe(10)
  })

  it('reads malformed optional fields as null, never 0', () => {
    const checked = validateClustersResponse({
      items: [{ label: 'x', size: 2, cohesion: 7, co_failure_runs: 'n', members: [{ test_fingerprint: 'f' }, 'junk'] }],
    })
    if (!checked.ok) throw new Error('invalid')
    expect(checked.value.clusters[0]).toMatchObject({
      cohesion: null,
      coFailureRuns: null,
      windowDays: null,
      members: [{ fingerprint: 'f', name: 'f', failureRuns: null }],
    })
    expect(checked.value.emptyIsNormal).toBeNull()
  })
})

describe('words', () => {
  const clusters = (() => {
    const checked = validateClustersResponse(clustersBody())
    if (!checked.ok) throw new Error('invalid')
    return checked.value.clusters
  })()

  it('the title says the window and the scope, never "AI"', () => {
    expect(clustersTitle(clusters)).toBe('Tests that fail together (last 60 days, whole project)')
    expect(clustersTitle([])).toBe('Tests that fail together (last 60 days, whole project)')
    expect(clusterWindowDays([{ ...clusters[0], windowDays: 30 }])).toBe(30)
    expect(
      clusterWindowDays([
        { ...clusters[0], windowDays: 30 },
        { ...clusters[1], windowDays: 45 },
      ]),
    ).toBe(60)
    expect(clustersTitle(clusters)).not.toMatch(/\bAI\b/)
    expect(EMPTY_CLUSTERS_FALLBACK).not.toMatch(/\bAI\b/)
  })

  it('an unknown cause is said as unknown, never guessed', () => {
    expect(causeLabel('unknown')).toBe('Unknown cause')
    expect(causeLabel(null)).toBe('Unknown cause')
    expect(causeLabel('external_dependency')).toBe('external dependency')
  })

  it('the readout of a cluster', () => {
    expect(tooltipText(clusterTipContent(clusters[0], 1))).toBe(
      '#1 checkout tests fail together. Cause: external dependency. Tests: 14. Cohesion: 82.0%. Runs failing together: 9',
    )
  })
})
