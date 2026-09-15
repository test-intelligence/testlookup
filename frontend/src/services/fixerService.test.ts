import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./http', () => ({ getData: vi.fn(), postData: vi.fn(), putData: vi.fn() }))

import { getData, putData } from './http'
import { fixerService } from './fixerService'

const view = {
  config: {
    agent_id: 'fixer', enabled: false, mode: 'shadow',
    extensions: {
      investigator: null,
      fixer: {
        runner: { type: 'none', runner_image: null, command_template: null, workflow_ref: null },
        test_globs: ['tests/**'],
        budgets: { max_tests_per_run: 3, max_attempts_per_test: 2, validation_reruns: 5,
          max_concurrent_open_prs: 2 },
        schedule: 'off',
      },
    },
  },
}

describe('fixerService AgentConfig adapter', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads and writes the pinned FixerConfig through agent-configs', async () => {
    ;(getData as ReturnType<typeof vi.fn>).mockResolvedValue(view)
    ;(putData as ReturnType<typeof vi.fn>).mockImplementation(async (_path, config) => ({ config }))
    const current = await fixerService.getConfig('proj-1')
    expect(current.enabled).toBe(false)

    await fixerService.updateConfig('proj-1', {
      ...current,
      enabled: true,
      mode: 'suggest',
      runner: { type: 'docker', runner_image: 'python:3.12', command_template: 'pytest', workflow_ref: null },
    })

    const path = '/api/v1/projects/proj-1/agent-configs/fixer'
    expect(getData).toHaveBeenCalledWith(path)
    expect(putData).toHaveBeenCalledWith(path, expect.objectContaining({
      enabled: true,
      mode: 'suggest',
      extensions: expect.objectContaining({ fixer: expect.objectContaining({
        runner: expect.objectContaining({ runner_image: 'python:3.12' }),
      }) }),
    }))
    expect(String((putData as ReturnType<typeof vi.fn>).mock.calls[0]?.[0])).not.toContain('/fixer/config')
  })
})
