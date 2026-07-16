/**
 * Fixer settings card (AI-2): pinned PUT payload shape (incl. per-runner-type
 * conditional fields), the suggest-without-runner 422 warning, and the
 * Run-now toast per response code. Hermetic — hook and service mocked.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FixerConfigCard from './FixerConfigCard'
import type { FixerConfig } from '@/types/fixer'

vi.mock('@/hooks/useFixer', () => ({
  useFixerConfig: vi.fn(),
}))

vi.mock('@/services/fixerService', () => ({
  fixerService: {
    getConfig: vi.fn(),
    updateConfig: vi.fn(),
    startRun: vi.fn(),
    listAttempts: vi.fn(),
    getAttempt: vi.fn(),
  },
}))

vi.mock('react-hot-toast', () => {
  const toast = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() })
  return { default: toast }
})

const CONFIG: FixerConfig = {
  enabled: true,
  mode: 'shadow',
  runner: { type: 'none', runner_image: null, command_template: null, workflow_ref: null },
  test_globs: ['tests/**', '**/*.spec.*', '**/*.test.*'],
  budgets: {
    max_tests_per_run: 3,
    max_attempts_per_test: 2,
    validation_reruns: 5,
    max_concurrent_open_prs: 2,
  },
  schedule: 'off',
}

async function mockConfig(config: FixerConfig = CONFIG) {
  const { useFixerConfig } = await import('@/hooks/useFixer')
  ;(useFixerConfig as ReturnType<typeof vi.fn>).mockReturnValue({
    data: config,
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  })
}

function axiosErr(status: number, detail: string) {
  return { isAxiosError: true, response: { status, data: { detail } }, message: `HTTP ${status}` }
}

describe('FixerConfigCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders shadow/suggest with trust-ladder copy; act is disabled — merging is always human', async () => {
    await mockConfig()
    render(<FixerConfigCard projectId="proj-1" />)

    expect(screen.getByRole('radio', { name: 'Shadow' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('radio', { name: 'Suggest' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText(/Suggest opens DRAFT pull requests — merging is always human/)).toBeInTheDocument()

    const act = screen.getByRole('radio', { name: 'Act' })
    expect(act).toBeDisabled()
    expect(act).toHaveAttribute('title', 'The Fixer never acts autonomously — merging is always human')
  })

  it('PUTs the exact contract payload for a docker runner (workflow_ref stays null)', async () => {
    await mockConfig()
    const { fixerService } = await import('@/services/fixerService')
    ;(fixerService.updateConfig as ReturnType<typeof vi.fn>).mockResolvedValue(CONFIG)
    render(<FixerConfigCard projectId="proj-1" />)

    fireEvent.change(screen.getByLabelText('Runner type'), { target: { value: 'docker' } })
    fireEvent.change(screen.getByLabelText('Runner image'), { target: { value: 'python:3.11-slim' } })
    fireEvent.change(screen.getByLabelText('Command template'), { target: { value: 'pytest {test} -x' } })
    fireEvent.change(screen.getByLabelText('Fixer schedule'), { target: { value: 'daily' } })
    fireEvent.change(screen.getByLabelText('Max tests / run'), { target: { value: '5' } })

    // Glob editor: drop one, add one.
    fireEvent.click(screen.getByLabelText('Remove glob **/*.spec.*'))
    fireEvent.change(screen.getByLabelText('New test glob'), { target: { value: 'e2e/**' } })
    fireEvent.click(screen.getByRole('button', { name: /add/i }))

    fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))

    await waitFor(() =>
      expect(fixerService.updateConfig).toHaveBeenCalledWith('proj-1', {
        enabled: true,
        mode: 'shadow',
        runner: {
          type: 'docker',
          runner_image: 'python:3.11-slim',
          command_template: 'pytest {test} -x',
          workflow_ref: null,
        },
        test_globs: ['tests/**', '**/*.test.*', 'e2e/**'],
        budgets: {
          max_tests_per_run: 5,
          max_attempts_per_test: 2,
          validation_reruns: 5,
          max_concurrent_open_prs: 2,
        },
        schedule: 'daily',
      }),
    )
  })

  it('PUTs workflow_ref for a workflow_dispatch runner and nulls the docker fields', async () => {
    await mockConfig({
      ...CONFIG,
      runner: { type: 'docker', runner_image: 'python:3.11', command_template: 'pytest {test}', workflow_ref: null },
    })
    const { fixerService } = await import('@/services/fixerService')
    ;(fixerService.updateConfig as ReturnType<typeof vi.fn>).mockResolvedValue(CONFIG)
    render(<FixerConfigCard projectId="proj-1" />)

    fireEvent.change(screen.getByLabelText('Runner type'), { target: { value: 'workflow_dispatch' } })
    fireEvent.change(screen.getByLabelText('Workflow ref'), {
      target: { value: '.github/workflows/fixer.yml@main' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))

    await waitFor(() => expect(fixerService.updateConfig).toHaveBeenCalledTimes(1))
    const payload = (fixerService.updateConfig as ReturnType<typeof vi.fn>).mock.calls[0]?.[1] as FixerConfig
    expect(payload.runner).toEqual({
      type: 'workflow_dispatch',
      runner_image: null,
      command_template: null,
      workflow_ref: '.github/workflows/fixer.yml@main',
    })
  })

  it('warns with the 422 rule when mode=suggest and runner=none, and clears once a runner is chosen', async () => {
    await mockConfig()
    render(<FixerConfigCard projectId="proj-1" />)

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Suggest' }))

    const warning = screen.getByRole('alert')
    expect(warning).toHaveTextContent('a validation runner is required before the Fixer may open PRs')

    fireEvent.change(screen.getByLabelText('Runner type'), { target: { value: 'docker' } })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('blocks saving on invalid budgets', async () => {
    await mockConfig()
    const { fixerService } = await import('@/services/fixerService')
    render(<FixerConfigCard projectId="proj-1" />)

    fireEvent.change(screen.getByLabelText('Validation reruns'), { target: { value: '0' } })
    expect(screen.getByText(/positive whole numbers/i)).toBeInTheDocument()

    const save = screen.getByRole('button', { name: /save configuration/i })
    expect(save).toBeDisabled()
    fireEvent.click(save)
    expect(fixerService.updateConfig).not.toHaveBeenCalled()
  })

  describe('Run now toasts', () => {
    it('202 → success toast with the fixer_run_id', async () => {
      await mockConfig()
      const { fixerService } = await import('@/services/fixerService')
      ;(fixerService.startRun as ReturnType<typeof vi.fn>).mockResolvedValue({ fixer_run_id: 'fixrun-abcdef99' })
      const toast = (await import('react-hot-toast')).default
      render(<FixerConfigCard projectId="proj-1" />)

      fireEvent.click(screen.getByRole('button', { name: /run now/i }))

      await waitFor(() => expect(fixerService.startRun).toHaveBeenCalledWith('proj-1'))
      expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('fixrun-a'))
    })

    it('403 disabled → error toast with the response detail', async () => {
      await mockConfig()
      const { fixerService } = await import('@/services/fixerService')
      ;(fixerService.startRun as ReturnType<typeof vi.fn>).mockRejectedValue(
        axiosErr(403, 'The Fixer is disabled for this project'),
      )
      const toast = (await import('react-hot-toast')).default
      render(<FixerConfigCard projectId="proj-1" />)

      fireEvent.click(screen.getByRole('button', { name: /run now/i }))

      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith('The Fixer is disabled for this project'),
      )
    })

    it('409 running → info toast with the response detail', async () => {
      await mockConfig()
      const { fixerService } = await import('@/services/fixerService')
      ;(fixerService.startRun as ReturnType<typeof vi.fn>).mockRejectedValue(
        axiosErr(409, 'A Fixer run is already in progress'),
      )
      const toast = (await import('react-hot-toast')).default
      render(<FixerConfigCard projectId="proj-1" />)

      fireEvent.click(screen.getByRole('button', { name: /run now/i }))

      await waitFor(() =>
        expect(toast).toHaveBeenCalledWith('A Fixer run is already in progress', { icon: 'ℹ️' }),
      )
      expect(toast.error).not.toHaveBeenCalled()
    })

    it('422 runner-required → error toast with the response detail', async () => {
      await mockConfig()
      const { fixerService } = await import('@/services/fixerService')
      ;(fixerService.startRun as ReturnType<typeof vi.fn>).mockRejectedValue(
        axiosErr(422, 'a validation runner is required before the Fixer may open PRs'),
      )
      const toast = (await import('react-hot-toast')).default
      render(<FixerConfigCard projectId="proj-1" />)

      fireEvent.click(screen.getByRole('button', { name: /run now/i }))

      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith(
          'a validation runner is required before the Fixer may open PRs',
        ),
      )
    })
  })
})
