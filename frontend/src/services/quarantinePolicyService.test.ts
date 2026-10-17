/**
 * W09 FLK-02: the quarantine policy service — the defensive guard's case
 * table, and the two calls (the exact path, the whole body, no global toast).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const http = vi.hoisted(() => ({ getData: vi.fn(), putData: vi.fn() }))
vi.mock('./http', () => http)

import { parseQuarantinePolicy, quarantinePolicyService } from './quarantinePolicyService'

const VALID = {
  project_id: 'p1',
  sla_days: 14,
  auto_create_defect: false,
  auto_promote: false,
  promote_after_passes: 20,
  detection_flip_rate_threshold: 0.2,
  detection_min_runs: 10,
  max_active_quarantined: 8,
  is_default: true,
}

describe('parseQuarantinePolicy', () => {
  it.each([
    ['undefined', undefined],
    ['null', null],
    ['an empty object', {}],
    ['an array', [VALID]],
    ['a string', 'policy'],
    ['no project id', { ...VALID, project_id: '' }],
    ['a string SLA', { ...VALID, sla_days: '14' }],
    ['a NaN SLA', { ...VALID, sla_days: Number.NaN }],
    ['a string flip rate', { ...VALID, detection_flip_rate_threshold: '0.2' }],
    ['a missing pass streak', { ...VALID, promote_after_passes: undefined }],
    ['a string boolean', { ...VALID, auto_promote: 'true' }],
    ['a string cap', { ...VALID, max_active_quarantined: '8' }],
    ['a numeric is_default', { ...VALID, is_default: 1 }],
  ])('answers null for %s', (_label, raw) => {
    expect(parseQuarantinePolicy(raw)).toBeNull()
  })

  it('keeps a valid answer', () => {
    expect(parseQuarantinePolicy(VALID)).toEqual(VALID)
  })

  it('reads the default cap when a server before W09 does not send one', () => {
    const { max_active_quarantined: _omitted, ...older } = VALID
    expect(parseQuarantinePolicy(older)).toEqual({ ...VALID, max_active_quarantined: 8 })
    expect(parseQuarantinePolicy({ ...VALID, max_active_quarantined: 0 })?.max_active_quarantined).toBe(0)
  })

  it('reads an absent is_default as a stored row', () => {
    const { is_default: _omitted, ...stored } = VALID
    expect(parseQuarantinePolicy(stored)?.is_default).toBe(false)
  })
})

describe('quarantinePolicyService', () => {
  beforeEach(() => {
    http.getData.mockReset()
    http.putData.mockReset()
  })

  it('reads the project path without the global toast', async () => {
    http.getData.mockResolvedValue(VALID)
    await expect(quarantinePolicyService.get('p1')).resolves.toEqual(VALID)
    expect(http.getData).toHaveBeenCalledWith('/api/v1/projects/p1/quarantine/policy', { suppressToast: true })
  })

  it('PUTs the whole body to the same path without the global toast', async () => {
    const body = {
      sla_days: 7, auto_create_defect: false, auto_promote: true, promote_after_passes: 20,
      detection_flip_rate_threshold: 0.2, detection_min_runs: 10, max_active_quarantined: 8,
    }
    http.putData.mockResolvedValue({ ...VALID, ...body, is_default: false })
    await quarantinePolicyService.update('p1', body)
    expect(http.putData).toHaveBeenCalledWith('/api/v1/projects/p1/quarantine/policy', body, { suppressToast: true })
  })

  it('encodes the project id into the path', async () => {
    http.getData.mockResolvedValue(VALID)
    await quarantinePolicyService.get('a/b')
    expect(http.getData).toHaveBeenCalledWith('/api/v1/projects/a%2Fb/quarantine/policy', { suppressToast: true })
  })
})
