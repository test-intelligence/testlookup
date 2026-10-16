/**
 * W07 FAIL-10: the classification-hint hooks.
 *
 * The list asks for the given project only (never without one); the preview
 * asks nothing below the server's 3-character floor (after trimming), sends
 * the trimmed pattern with its field, and keys on both, so a change of field
 * is a new question.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({
  list: vi.fn(),
  preview: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
}))
vi.mock('@/services/classificationHintService', () => ({ classificationHintService: service }))

import { useClassificationHintPreview, useClassificationHints } from './useClassificationHints'

function wrapper() {
  const cache = new Map()
  const Wrapper = ({ children }: { children: ReactNode }) =>
    createElement(SWRConfig, { value: { provider: () => cache, dedupingInterval: 0 } }, children)
  return { Wrapper, cache }
}

describe('useClassificationHints', () => {
  beforeEach(() => {
    service.list.mockReset()
    service.list.mockResolvedValue({ items: [], total: 0, max_hints: 50 })
  })

  it("reads the given project's hints", async () => {
    const { Wrapper } = wrapper()
    const { result } = renderHook(() => useClassificationHints('proj-1'), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.data).toEqual({ items: [], total: 0, max_hints: 50 }))
    expect(service.list).toHaveBeenCalledTimes(1)
    expect(service.list).toHaveBeenCalledWith('proj-1')
  })

  it('asks nothing without a project', () => {
    const { Wrapper } = wrapper()
    renderHook(() => useClassificationHints(null), { wrapper: Wrapper })
    expect(service.list).not.toHaveBeenCalled()
  })
})

describe('useClassificationHintPreview', () => {
  beforeEach(() => {
    service.preview.mockReset()
    service.preview.mockResolvedValue({ matched: 7, sampled: 200, lookback_days: 30, examples: [] })
  })

  it('asks nothing below 3 characters after trimming', () => {
    const { Wrapper } = wrapper()
    renderHook(() => useClassificationHintPreview('proj-1', 'error_message', '  EC  '), { wrapper: Wrapper })
    renderHook(() => useClassificationHintPreview('proj-1', 'error_message', ''), { wrapper: Wrapper })
    expect(service.preview).not.toHaveBeenCalled()
  })

  it('asks with the field and the trimmed pattern at 3+ characters', async () => {
    const { Wrapper } = wrapper()
    const { result } = renderHook(
      () => useClassificationHintPreview('proj-1', 'error_message', ' ECONNREFUSED '),
      { wrapper: Wrapper },
    )
    await waitFor(() => expect(result.current.data?.matched).toBe(7))
    expect(service.preview).toHaveBeenCalledWith('proj-1', 'error_message', 'ECONNREFUSED')
  })

  it('keys on the field: changing it asks again', async () => {
    const { Wrapper } = wrapper()
    const { result, rerender } = renderHook(
      ({ field }: { field: string }) => useClassificationHintPreview('proj-1', field, 'timeout'),
      { wrapper: Wrapper, initialProps: { field: 'error_message' } },
    )
    await waitFor(() => expect(result.current.data).toBeDefined())
    rerender({ field: 'test_name' })
    await waitFor(() => expect(service.preview).toHaveBeenCalledTimes(2))
    expect(service.preview).toHaveBeenLastCalledWith('proj-1', 'test_name', 'timeout')
  })

  it('asks nothing without a project', () => {
    const { Wrapper } = wrapper()
    renderHook(() => useClassificationHintPreview(undefined, 'error_message', 'ECONNREFUSED'), { wrapper: Wrapper })
    expect(service.preview).not.toHaveBeenCalled()
  })
})
