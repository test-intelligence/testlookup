/**
 * W04 AGT-04: the Retry / Stop / "Start a new run" calls and their copy.
 *
 *  - retry/cancel post to the pipeline's own route with `suppressToast` (the
 *    page renders the 409 itself; the interceptor would toast
 *    "message: …" over it);
 *  - `triggerPipeline` sends `workflow_id` ONLY when asked (a deep pipeline's
 *    "Start a new run" must not silently queue the offline default; every
 *    existing one-argument caller keeps its old body);
 *  - `isResumablePipeline` mirrors the backend's `is_resumable` minus
 *    `retry_wait` (in progress: Stop, not Retry);
 *  - a 409's `detail.message` is read verbatim; anything else through the
 *    shared extractor, never silence.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { postData } = vi.hoisted(() => ({ postData: vi.fn() }))
vi.mock('./http', () => ({ getData: vi.fn(), postData, putData: vi.fn(), deleteData: vi.fn() }))

import agentService from './agentService'
import {
  cancelOutcomeText,
  conflictText,
  deepRetryOutcomeText,
  pipelineActionError,
  readPipelineActionConflict,
  retryOutcomeText,
} from './pipelineActions'
import { isResumablePipeline, type RetryPipelineResponse } from '@/types/agent'

const httpError = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })

const RETRY: RetryPipelineResponse = {
  mode: 'resume', pipeline_run_id: 'p-1', rerun_of: null, attempt: 2, max_attempts: 5,
  reason: 'config_unchanged', status: 'failed', public_status: 'failed', links: {},
}

beforeEach(() => {
  postData.mockReset()
  postData.mockResolvedValue({})
})

describe('agentService', () => {
  it('retry and cancel post to the pipeline\'s routes and render their own errors (no global toast)', async () => {
    await agentService.retryPipeline('p-1')
    expect(postData).toHaveBeenLastCalledWith('/api/v1/agents/pipelines/p-1/retry', undefined, { suppressToast: true })
    await agentService.cancelPipeline('p-1')
    expect(postData).toHaveBeenLastCalledWith('/api/v1/agents/pipelines/p-1/cancel', undefined, { suppressToast: true })
    // An id is a path segment, never a path.
    await agentService.retryPipeline('../x')
    expect(postData).toHaveBeenLastCalledWith('/api/v1/agents/pipelines/..%2Fx/retry', undefined, { suppressToast: true })
  })

  it('triggerPipeline sends workflow_id only when given', async () => {
    await agentService.triggerPipeline('run-1')
    expect(postData).toHaveBeenLastCalledWith('/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1' })
    await agentService.triggerPipeline('run-1', 'deep')
    expect(postData).toHaveBeenLastCalledWith('/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1', workflow_id: 'deep' })
    await agentService.triggerPipeline('run-1', 'offline')
    expect(postData).toHaveBeenLastCalledWith('/api/v1/agents/pipelines/trigger', { test_run_id: 'run-1', workflow_id: 'offline' })
  })
})

describe('isResumablePipeline', () => {
  it.each([
    [{ status: 'failed' }, true],
    [{ status: 'partial' }, true],
    [{ status: 'completed', execution_metadata: { stage_quality: 'degraded' } }, true],
    [{ status: 'completed', execution_metadata: {} }, false],
    [{ status: 'completed', execution_metadata: null }, false],
    [{ status: 'passed' }, false],
    [{ status: 'running' }, false],
    [{ status: 'pending' }, false],
    // The backend's is_resumable includes retry_wait; the UI offers Stop there.
    [{ status: 'retry_wait' }, false],
    [{ status: 'FAILED' }, true],
  ])('%j -> %s', (p, expected) => {
    expect(isResumablePipeline(p)).toBe(expected)
  })

  it('a missing pipeline is not resumable', () => {
    expect(isResumablePipeline(null)).toBe(false)
    expect(isResumablePipeline(undefined)).toBe(false)
  })
})

describe('the copy', () => {
  it('names what the server decided', () => {
    expect(retryOutcomeText(RETRY)).toBe('Resumed (attempt 2/5) — keeps the completed stages.')
    expect(retryOutcomeText({ ...RETRY, mode: 'rerun', reason: 'config_changed' })).toBe(
      'Started a new run — the analysis configuration changed since this run started.',
    )
    expect(retryOutcomeText({ ...RETRY, mode: 'rerun', reason: 'no_frozen_plan' })).toBe(
      'Started a new run — this run has no saved plan to resume.',
    )
    expect(deepRetryOutcomeText(RETRY)).toBe('Resuming from the last successful stage (attempt 2/5).')
    expect(deepRetryOutcomeText({ ...RETRY, mode: 'rerun', reason: 'config_changed' })).toBe(
      'The configuration changed, so a new deep run was started instead.',
    )
    expect(cancelOutcomeText({ pipeline_run_id: 'p', accepted: true, terminal: true, status: 'failed', public_status: 'failed', reason: '' })).toBe('Stopped.')
    expect(cancelOutcomeText({ pipeline_run_id: 'p', accepted: true, terminal: false, status: 'running', public_status: 'in_progress', reason: '' })).toBe(
      'Stop requested — the run ends at its next stage boundary.',
    )
  })

  it('reads a 409 detail verbatim, review rejection in its own line', () => {
    const ceiling = readPipelineActionConflict(httpError(409, { message: 'Pipeline reached its attempt ceiling (5/5). Start a new run instead.', attempt: 5, max_attempts: 5, links: { rerun: '/x' } }))
    expect(ceiling?.links?.rerun).toBe('/x')
    if (!ceiling) throw new Error('a 409 with a message is a conflict')
    expect(conflictText(ceiling)).toBe('Pipeline reached its attempt ceiling (5/5). Start a new run instead.')
    const rejected = readPipelineActionConflict(httpError(409, { message: 'rejected', reason: 'review_rejected' }))
    if (!rejected) throw new Error('a 409 with a message is a conflict')
    expect(conflictText(rejected)).toBe('Rejected in review — start a new run.')
  })

  it('is not a conflict unless it is a 409 with a message; other failures keep the server\'s words', () => {
    expect(readPipelineActionConflict(httpError(422, 'Deep investigation is disabled'))).toBeNull()
    expect(readPipelineActionConflict(httpError(409, 'plain string'))).toBeNull()
    expect(readPipelineActionConflict(httpError(409, { reason: 'x' }))).toBeNull()
    expect(readPipelineActionConflict(new Error('Network Error'))).toBeNull()
    expect(pipelineActionError(httpError(422, 'Deep investigation is disabled in Settings › AI.'), 'Retry failed')).toBe(
      'Deep investigation is disabled in Settings › AI.',
    )
    expect(pipelineActionError(httpError(403, 'Requires at least QA_ENGINEER role'), 'Retry failed')).toBe('Requires at least QA_ENGINEER role')
    expect(pipelineActionError(new Error('Network Error'), 'Retry failed')).toBe('Network Error')
    expect(pipelineActionError(undefined, 'Retry failed')).toBe('Retry failed')
  })
})
