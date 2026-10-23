/**
 * W10 (contract §1.6): the service additions are optional and change nothing
 * for an existing caller.
 *
 * - `getAllStatus` asks `?scope=all` ONLY for the string 'all' — `useIntegrationStatus`
 *   hands it SWR's key array, which must stay today's deployment-wide read.
 * - `triggerProbe` / `getProviderHistory` add `project_id` only when given.
 * - `updateIntegrationsConfig` / `projectsService.update` pass `suppressToast`
 *   only when asked, and otherwise make exactly today's call.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }))
vi.mock('./api', () => ({ api }))

import { getAllStatus, getProviderHistory, triggerProbe } from './integrationHealthService'
import { appSettingsService } from './appSettingsService'
import { projectsService } from './projectsService'

beforeEach(() => {
  for (const fn of Object.values(api)) {
    fn.mockReset()
    fn.mockResolvedValue({ data: [] })
  }
})

describe('integrationHealthService (W10 INT-04)', () => {
  it('getAllStatus: today\'s read with no argument, and with the SWR key array', async () => {
    await getAllStatus()
    await getAllStatus(['integration-health-status'])
    expect(api.get).toHaveBeenNthCalledWith(1, '/api/v1/integration-health/status', undefined)
    expect(api.get).toHaveBeenNthCalledWith(2, '/api/v1/integration-health/status', undefined)
  })

  it('getAllStatus("all") asks scope=all', async () => {
    await getAllStatus('all')
    expect(api.get).toHaveBeenCalledWith('/api/v1/integration-health/status', { params: { scope: 'all' } })
  })

  it('triggerProbe: no params, the provider alone, or the provider and the project', async () => {
    await triggerProbe()
    await triggerProbe('jira')
    await triggerProbe('gitlab', 'p-2')
    expect(api.post).toHaveBeenNthCalledWith(1, '/api/v1/integration-health/probe', null, { params: undefined })
    expect(api.post).toHaveBeenNthCalledWith(2, '/api/v1/integration-health/probe', null, { params: { provider: 'jira' } })
    expect(api.post).toHaveBeenNthCalledWith(3, '/api/v1/integration-health/probe', null, { params: { provider: 'gitlab', project_id: 'p-2' } })
  })

  it('getProviderHistory: days as today; project_id only when given', async () => {
    await getProviderHistory('jira', 7)
    await getProviderHistory('github', 7, 'p-1')
    expect(api.get).toHaveBeenNthCalledWith(1, '/api/v1/integration-health/history/jira', { params: { days: 7 } })
    expect(api.get).toHaveBeenNthCalledWith(2, '/api/v1/integration-health/history/github', { params: { days: 7, project_id: 'p-1' } })
  })
})

describe('appSettingsService.updateIntegrationsConfig (W10 INT-01)', () => {
  it('today\'s call without options; suppressToast only when asked', async () => {
    api.put.mockResolvedValue({ data: {} })
    await appSettingsService.updateIntegrationsConfig({ jira_enabled: true })
    await appSettingsService.updateIntegrationsConfig({ splunk_base_url: 'https://s.example' }, { suppressToast: true })
    expect(api.put).toHaveBeenNthCalledWith(1, '/api/v1/settings/integrations', { jira_enabled: true }, undefined)
    expect(api.put).toHaveBeenNthCalledWith(2, '/api/v1/settings/integrations', { splunk_base_url: 'https://s.example' }, { suppressToast: true })
  })
})

describe('projectsService.update (W10 INT-03)', () => {
  it('today\'s call without options; suppressToast only when asked', async () => {
    api.put.mockResolvedValue({ data: {} })
    await projectsService.update('p-1', { description: 'x' })
    await projectsService.update('p-1', { splunk_index: '' }, { suppressToast: true })
    expect(api.put).toHaveBeenNthCalledWith(1, '/api/v1/projects/p-1', { description: 'x' }, undefined)
    expect(api.put).toHaveBeenNthCalledWith(2, '/api/v1/projects/p-1', { splunk_index: '' }, { suppressToast: true })
  })
})
