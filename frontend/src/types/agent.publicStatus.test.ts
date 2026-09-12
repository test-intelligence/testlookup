/**
 * E7.5: the UI shows exactly four pipeline statuses.
 *
 * `publicPipelineStatus` is the single place the six internal states (and the
 * retired `partial` / `cancelled`) collapse to `in_progress | completed |
 * failed | passed`. It must agree with backend
 * `workflow_run_state.PUBLIC_STATUS`, prefer the server's `public_status`, and
 * never produce a fifth value -- a cached payload from before E7.5 has no
 * `public_status`, and that is exactly when a raw `retry_wait` would leak.
 */
import { describe, expect, it } from 'vitest'

import {
  PUBLIC_PIPELINE_STATUS_LABEL,
  isPipelineInProgress,
  publicPipelineStatus,
  type PublicPipelineStatus,
} from './agent'

const PUBLIC: PublicPipelineStatus[] = ['in_progress', 'completed', 'failed', 'passed']

describe('publicPipelineStatus', () => {
  it.each([
    ['pending', 'in_progress'],
    ['running', 'in_progress'],
    ['retry_wait', 'in_progress'],
    ['completed', 'completed'],
    ['passed', 'passed'],
    ['failed', 'failed'],
  ] as const)('projects internal %s to %s (mirrors backend PUBLIC_STATUS)', (internal, expected) => {
    expect(publicPipelineStatus({ status: internal })).toBe(expected)
  })

  it('maps the retired literals the way the backend legacy table does', () => {
    expect(publicPipelineStatus({ status: 'partial' })).toBe('completed')
    expect(publicPipelineStatus({ status: 'cancelled' })).toBe('failed')
  })

  it("prefers the server's public_status over its own projection", () => {
    expect(publicPipelineStatus({ status: 'completed', public_status: 'passed' })).toBe('passed')
  })

  it('ignores a public_status that is not one of the four', () => {
    expect(publicPipelineStatus({ status: 'running', public_status: 'retry_wait' })).toBe('in_progress')
  })

  it('treats an unknown status as failed, never as a spinner that spins forever', () => {
    expect(publicPipelineStatus({ status: 'bogus' })).toBe('failed')
  })

  it('treats a missing status as in progress, as the backend reads it as pending', () => {
    expect(publicPipelineStatus({ status: null })).toBe('in_progress')
    expect(publicPipelineStatus(undefined)).toBe('in_progress')
  })

  it('never returns anything outside the four public values', () => {
    const inputs = ['pending', 'running', 'retry_wait', 'completed', 'passed', 'failed', 'partial',
      'cancelled', 'canceled', 'in_progress', '', 'RUNNING', 'x']
    for (const status of inputs) {
      expect(PUBLIC, status).toContain(publicPipelineStatus({ status }))
    }
  })

  it('has a label for every public value and nothing else', () => {
    expect(Object.keys(PUBLIC_PIPELINE_STATUS_LABEL).sort()).toEqual([...PUBLIC].sort())
  })
})

describe('isPipelineInProgress', () => {
  it('covers pending and retry_wait, not only running', () => {
    expect(isPipelineInProgress('pending')).toBe(true)
    expect(isPipelineInProgress('running')).toBe(true)
    expect(isPipelineInProgress('retry_wait')).toBe(true)
  })

  it('is false for every terminal state and for no status', () => {
    for (const status of ['completed', 'passed', 'failed', 'partial', 'cancelled']) {
      expect(isPipelineInProgress(status), status).toBe(false)
    }
    expect(isPipelineInProgress(null)).toBe(false)
    expect(isPipelineInProgress(undefined)).toBe(false)
  })
})
