/**
 * W23a FAIL-11 / TRND-03: the insights and expected-runs services, and the two hooks' key builders.
 *
 * The request paths are the backend contract (the SPA wire-contract guard reads them), so each one is
 * pinned here in full, with its params / body; every write passes `suppressToast: true` (the caller shows
 * the refusal inline). The SWR keys are the request URLs: `null` (no request) without a single project, and
 * the status key snaps the page window onto `STATUS_DAYS`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }))
vi.mock('./api', () => ({ api: apiMock }))

import { dismissInsight, listInsights } from './insightsService'
import {
  cadenceStatus,
  createCadence,
  deleteCadence,
  listCadences,
  pauseCadence,
  resumeCadence,
  updateCadence,
} from './runCadenceService'
import { insightsKey } from '@/hooks/useInsights'
import { cadenceStatusKey, cadencesKey, snapStatusDays } from '@/hooks/useRunCadences'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import { STATUS_DAYS } from '@/types/runCadence'

const PROJECT = '00000000-0000-4000-8000-00000000e0a1'
const INSIGHT = '00000000-0000-4000-8000-00000000e0c3'
const CADENCE = '00000000-0000-4000-8000-00000000e0d4'

describe('insightsService', () => {
  beforeEach(() => {
    apiMock.get.mockReset().mockResolvedValue({ data: { items: [] } })
    apiMock.post.mockReset().mockResolvedValue({ data: { id: INSIGHT } })
  })

  it('lists with range and include_dismissed by their wire names, and returns the raw answer', async () => {
    const answer = { items: [], total: 0 }
    apiMock.get.mockResolvedValue({ data: answer })
    await expect(listInsights(PROJECT, '7d', false)).resolves.toBe(answer)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/insights`, {
      params: { range: '7d', include_dismissed: false },
    })
  })

  it('adds limit only when given (the Inbox tab asks 100)', async () => {
    await listInsights(PROJECT, '24h', true, 100)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/insights`, {
      params: { range: '24h', include_dismissed: true, limit: 100 },
    })
  })

  it('dismisses with no body and no toast', async () => {
    await expect(dismissInsight(PROJECT, INSIGHT)).resolves.toEqual({ id: INSIGHT })
    expect(apiMock.post).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/insights/${INSIGHT}/dismiss`, undefined, {
      suppressToast: true,
    })
  })
})

describe('runCadenceService', () => {
  beforeEach(() => {
    apiMock.get.mockReset().mockResolvedValue({ data: { items: [], total: 0 } })
    apiMock.post.mockReset().mockResolvedValue({ data: { id: CADENCE } })
    apiMock.patch.mockReset().mockResolvedValue({ data: { id: CADENCE } })
    apiMock.delete.mockReset().mockResolvedValue({ data: undefined })
  })

  it('lists the cadences of one project', async () => {
    await expect(listCadences(PROJECT)).resolves.toEqual({ items: [], total: 0 })
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/run-cadences`, undefined)
  })

  it('reads the status with days', async () => {
    await cadenceStatus(PROJECT, 30)
    expect(apiMock.get).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/run-cadences/status`, { params: { days: 30 } })
  })

  it('creates with the body as given and no toast', async () => {
    const body = {
      suite_name: 'nightly-regression',
      mode: 'weekly' as const,
      weekdays: ['mon' as const, 'tue' as const, 'wed' as const, 'thu' as const, 'fri' as const],
      expected_by: '06:00',
      timezone: 'Europe/Berlin',
      grace_minutes: 60,
    }
    await createCadence(PROJECT, body)
    expect(apiMock.post).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/run-cadences`, body, { suppressToast: true })
  })

  it('patches only what it is given', async () => {
    await updateCadence(PROJECT, CADENCE, { clear_suite: true })
    expect(apiMock.patch).toHaveBeenCalledWith(
      `/api/v1/projects/${PROJECT}/run-cadences/${CADENCE}`,
      { clear_suite: true },
      { suppressToast: true },
    )
  })

  it('deletes with no body and no toast', async () => {
    await deleteCadence(PROJECT, CADENCE)
    expect(apiMock.delete).toHaveBeenCalledWith(`/api/v1/projects/${PROJECT}/run-cadences/${CADENCE}`, {
      suppressToast: true,
    })
  })

  it('pauses with {until, reason} and resumes with no body', async () => {
    const until = new Date(Date.now() + 2 * 86_400_000).toISOString()
    await pauseCadence(PROJECT, CADENCE, { until, reason: 'Release freeze sentinel' })
    expect(apiMock.post).toHaveBeenCalledWith(
      `/api/v1/projects/${PROJECT}/run-cadences/${CADENCE}/pause`,
      { until, reason: 'Release freeze sentinel' },
      { suppressToast: true },
    )
    await resumeCadence(PROJECT, CADENCE)
    expect(apiMock.post).toHaveBeenLastCalledWith(`/api/v1/projects/${PROJECT}/run-cadences/${CADENCE}/resume`, undefined, {
      suppressToast: true,
    })
  })
})

describe('the hooks’ SWR keys', () => {
  it('are the request URLs for one project', () => {
    expect(insightsKey(PROJECT, '7d')).toBe(`/api/v1/projects/${PROJECT}/insights?range=7d&include_dismissed=false`)
    expect(insightsKey(PROJECT, '24h', true, 100)).toBe(
      `/api/v1/projects/${PROJECT}/insights?range=24h&include_dismissed=true&limit=100`,
    )
    expect(cadencesKey(PROJECT)).toBe(`/api/v1/projects/${PROJECT}/run-cadences`)
    expect(cadenceStatusKey(PROJECT, 14)).toBe(`/api/v1/projects/${PROJECT}/run-cadences/status?days=14`)
  })

  it('are null (no request) in All Projects and without a project', () => {
    for (const id of [ALL_PROJECTS_ID, null, undefined, '']) {
      expect(insightsKey(id, '7d')).toBeNull()
      expect(cadencesKey(id)).toBeNull()
      expect(cadenceStatusKey(id, 14)).toBeNull()
    }
  })

  it('snap the status window onto STATUS_DAYS', () => {
    for (const days of STATUS_DAYS) expect(snapStatusDays(days)).toBe(days)
    expect(snapStatusDays(0)).toBe(1)
    expect(snapStatusDays(3)).toBe(1)
    expect(snapStatusDays(5)).toBe(7)
    expect(snapStatusDays(21)).toBe(14)
    expect(snapStatusDays(45)).toBe(30)
    expect(snapStatusDays(365)).toBe(90)
    expect(snapStatusDays(Number.NaN)).toBe(14)
    expect(cadenceStatusKey(PROJECT, 21)).toBe(`/api/v1/projects/${PROJECT}/run-cadences/status?days=14`)
  })
})
