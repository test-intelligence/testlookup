/**
 * W03 AIH-05: one test result's stored AI versions (contract §9.8, R5):
 * the current analysis from the history read, the versions oldest -> newest,
 * the loading / error / empty states, and every AI string as text (XSS).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AITestVersion, TestHistoryAI } from '@/types/aiTestHistory'

const mutate = vi.fn()
const versionsState: { data: unknown; error: unknown; isLoading: boolean; mutate: () => void } = {
  data: undefined,
  error: undefined,
  isLoading: false,
  mutate,
}
const hookCalls: unknown[][] = []
vi.mock('@/hooks/useTestAIVersions', () => ({
  useTestAIVersions: (...args: unknown[]) => {
    hookCalls.push(args)
    return versionsState
  },
}))

import TestAIVersionsPopover from './TestAIVersionsPopover'

const HOSTILE = '<img src=x onerror=alert(1)>'

const ai = (over: Partial<TestHistoryAI> = {}): TestHistoryAI => ({
  test_id: 't9',
  category: 'PRODUCT_BUG',
  confidence: 85,
  root_cause_excerpt: 'the tax line is skipped',
  root_cause_sha: 'aaaaaaaaaaaaaaaa',
  analysis_mode: 'llm',
  model: 'llama3.1',
  is_flaky: false,
  requires_human_review: true,
  version_count: 2,
  ...over,
})

const version = (n: number, over: Partial<AITestVersion> = {}): AITestVersion => ({
  version_id: `v-${n}`,
  version: n,
  trigger: 'pipeline',
  pipeline_run_id: `p-${n}`,
  actor: null,
  failure_category: 'PRODUCT_BUG',
  confidence_score: 80,
  analysis_mode: 'llm',
  llm_provider: 'ollama',
  llm_model: 'llama3.1',
  is_flaky: false,
  requires_human_review: false,
  root_cause_excerpt: 'text',
  content_sha256: 'f'.repeat(64),
  created_at: '2026-10-14T10:00:00Z',
  ...over,
})

function renderPopover(props: Partial<Parameters<typeof TestAIVersionsPopover>[0]> = {}) {
  const onClose = vi.fn()
  const utils = render(
    <MemoryRouter>
      <TestAIVersionsPopover runId="run-9" runLabel="Run #9" ai={ai()} onClose={onClose} {...props} />
    </MemoryRouter>,
  )
  return { ...utils, onClose }
}

describe('TestAIVersionsPopover', () => {
  beforeEach(() => {
    hookCalls.length = 0
    mutate.mockReset()
    versionsState.data = undefined
    versionsState.error = undefined
    versionsState.isLoading = false
  })

  it('heads with the current analysis from the history read, and closes', () => {
    const { container, onClose } = renderPopover()
    const current = container.querySelector('[data-test-ai-current]') as HTMLElement
    expect(current.textContent).toContain('PRODUCT_BUG')
    expect(current.textContent).toContain('85%')
    expect(current.textContent).toContain('LLM llama3.1')
    expect(current.textContent).toContain('needs human review')
    expect(container.querySelector('[data-test-ai-current-excerpt]')?.textContent).toBe('the tax line is skipped')
    fireEvent.click(screen.getByRole('button', { name: 'Close AI versions' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('reads the versions of THAT point (its run and its own test id)', () => {
    renderPopover()
    expect(hookCalls[hookCalls.length - 1]).toEqual(['run-9', 't9'])
  })

  it('a missing confidence and engine read "not reported", never 0', () => {
    const { container } = renderPopover({ ai: ai({ confidence: null, analysis_mode: null, model: null, root_cause_excerpt: '' }) })
    const current = container.querySelector('[data-test-ai-current]') as HTMLElement
    expect(current.textContent).toContain('confidence not reported')
    expect(current.textContent).toContain('engine not reported')
    expect(current.textContent).not.toMatch(/\b0%/)
    expect(screen.getByText('Root cause not reported.')).toBeInTheDocument()
  })

  it('loading, error (with Retry) and an empty answer', () => {
    versionsState.isLoading = true
    const first = renderPopover()
    expect(first.container.querySelector('[data-test-ai-versions-state="loading"]')).not.toBeNull()
    first.unmount()

    versionsState.isLoading = false
    versionsState.error = new Error('boom')
    const second = renderPopover()
    expect(second.container.querySelector('[data-test-ai-versions-state="error"]')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
    second.unmount()

    versionsState.error = undefined
    versionsState.data = { run_id: 'run-9', test_id: 't9', test_fingerprint: null, test_name: 'x', current: null, versions: [], total: 0 }
    const third = renderPopover()
    expect(third.getByText('No versions stored yet')).toBeInTheDocument()
  })

  it('lists the versions oldest -> newest with the latest marked, and says when the list is cut', () => {
    versionsState.data = {
      run_id: 'run-9',
      test_id: 't9',
      test_fingerprint: 'fp',
      test_name: 'test_pay',
      current: null,
      versions: [version(12), version(11, { trigger: 'manual_analyze' })],
      total: 12,
    }
    const { container } = renderPopover()
    const rows = Array.from(container.querySelectorAll('[data-test-ai-version]'))
    expect(rows.map((r) => r.querySelector('[data-test-ai-version-label]')?.textContent)).toEqual(['v11 manual analysis', 'v12 LLM'])
    expect(rows[1].textContent).toContain('latest')
    expect(rows[0].textContent).not.toContain('latest')
    expect(screen.getByText('Showing the latest 2 of 12 versions.')).toBeInTheDocument()
  })

  it('renders hostile root causes, actor names and model names as text (no element, no handler)', () => {
    versionsState.data = {
      run_id: 'run-9',
      test_id: 't9',
      test_fingerprint: 'fp',
      test_name: HOSTILE,
      current: null,
      versions: [
        version(2, { trigger: 'human_correction', actor: { user_id: 'u', name: HOSTILE }, root_cause_excerpt: HOSTILE, llm_model: HOSTILE }),
      ],
      total: 1,
    }
    const { container } = renderPopover({ ai: ai({ root_cause_excerpt: HOSTILE, model: HOSTILE, category: HOSTILE }), runLabel: HOSTILE })
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    expect(container.querySelector('[data-test-ai-current-excerpt]')?.textContent).toBe(HOSTILE)
    expect(container.querySelector('[data-test-ai-version-excerpt]')?.textContent).toBe(HOSTILE)
    expect(container.querySelector('[data-test-ai-version-label]')?.textContent).toBe(`v2 human correction (${HOSTILE})`)
    expect(container.querySelector('[data-test-ai-version-summary]')?.textContent).toBe(`v2 human correction (${HOSTILE})`)
  })

  it("names the model of an AI version (as text), never of a person's correction", () => {
    versionsState.data = {
      run_id: 'run-9',
      test_id: 't9',
      test_fingerprint: 'fp',
      test_name: 'test_pay',
      current: null,
      versions: [
        version(1, { llm_model: HOSTILE }),
        // The correction row inherits the model of the AI row it edited.
        version(2, { trigger: 'human_correction', actor: { user_id: 'u', name: 'anna' }, llm_model: 'llama3.1' }),
      ],
      total: 2,
    }
    const { container } = renderPopover()
    const [ai, person] = Array.from(container.querySelectorAll('[data-test-ai-version]'))
    expect(ai.querySelector('[data-test-ai-version-model]')?.textContent).toBe(HOSTILE)
    expect(container.querySelector('img')).toBeNull()
    expect(person.querySelector('[data-test-ai-version-label]')?.textContent).toBe('v2 human correction (anna)')
    expect(person.querySelector('[data-test-ai-version-model]')).toBeNull()
    expect(person.textContent).not.toContain('llama3.1')
  })

  it('a legacy point without a run id cannot read versions and says so', () => {
    const { container } = renderPopover({ runId: null })
    expect(hookCalls[hookCalls.length - 1]).toEqual([null, 't9'])
    expect(container.querySelector('[data-test-ai-versions-state="unavailable"]')).not.toBeNull()
    expect(container.querySelector('[data-test-ai-open-run]')).toBeNull()
  })
})
