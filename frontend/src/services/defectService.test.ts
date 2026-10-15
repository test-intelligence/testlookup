/**
 * W06 DEF-01: the triage PATCH names the defect's OWN project in the path and
 * sends the body as given, on the shared Axios base, with the caller's
 * per-request options (the dialogs pass `suppressToast`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ patch: vi.fn() }))
vi.mock('@/services/api', () => ({ api }))

import { updateDefect } from './defectService'

beforeEach(() => {
  api.patch.mockReset()
})

describe('defectService.updateDefect', () => {
  it('PATCHes /api/v1/projects/{project}/defects/{defect} with the body and config', async () => {
    const answer = { defect: { id: 'd-1' }, changed_fields: ['resolution_status'] }
    api.patch.mockResolvedValue({ data: answer })
    const result = await updateDefect('p-1', 'd-1', { resolution_status: 'IN_PROGRESS' }, { suppressToast: true })
    expect(api.patch).toHaveBeenCalledTimes(1)
    expect(api.patch).toHaveBeenCalledWith(
      '/api/v1/projects/p-1/defects/d-1',
      { resolution_status: 'IN_PROGRESS' },
      { suppressToast: true },
    )
    expect(result).toBe(answer)
  })

  it('encodes the ids it puts in the path', async () => {
    api.patch.mockResolvedValue({ data: {} })
    await updateDefect('p/1', 'd?1', { jira_key: null })
    expect(api.patch).toHaveBeenCalledWith('/api/v1/projects/p%2F1/defects/d%3F1', { jira_key: null }, undefined)
  })
})
