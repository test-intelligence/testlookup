/**
 * W27 PAN-01: one open form per page section. A replacement or a guarded
 * action over a form with typed input waits for the discard answer; a clean
 * slot replaces at once; Cancel / X / Escape (close) never ask.
 */
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useInlineSlot } from './useInlineSlot'

function slot() {
  return renderHook(() => useInlineSlot<'edit' | 'link' | 'reason:1'>())
}

describe('useInlineSlot', () => {
  it('starts closed and clean', () => {
    const { result } = slot()
    expect(result.current).toMatchObject({ openKey: null, dirty: false, asking: false })
  })

  it('open: at once when clean; a no-op for the key already open', () => {
    const { result } = slot()
    act(() => result.current.open('edit'))
    expect(result.current).toMatchObject({ openKey: 'edit', dirty: false, asking: false })
    act(() => result.current.open('link'))
    expect(result.current.openKey).toBe('link')

    act(() => result.current.setDirty(true))
    act(() => result.current.open('link'))
    expect(result.current).toMatchObject({ openKey: 'link', dirty: true, asking: false })
  })

  it('open over typed input asks; Discard changes opens the new key clean', () => {
    const { result } = slot()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.open('link'))
    expect(result.current).toMatchObject({ openKey: 'edit', dirty: true, asking: true })

    act(() => result.current.answer(true))
    expect(result.current).toMatchObject({ openKey: 'link', dirty: false, asking: false })
  })

  it('Keep editing keeps the open form and its typed input', () => {
    const { result } = slot()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.open('link'))
    act(() => result.current.answer(false))
    expect(result.current).toMatchObject({ openKey: 'edit', dirty: true, asking: false })
  })

  it('a newer replacement while asking is the one Discard changes opens', () => {
    const { result } = slot()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.open('link'))
    act(() => result.current.open('reason:1'))
    act(() => result.current.answer(true))
    expect(result.current.openKey).toBe('reason:1')
  })

  it('close never asks, and resets dirty and a pending question', () => {
    const { result } = slot()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.open('link'))
    act(() => result.current.close())
    expect(result.current).toMatchObject({ openKey: null, dirty: false, asking: false })
    // The replacement that was waiting is gone with it.
    act(() => result.current.answer(true))
    expect(result.current.openKey).toBeNull()
  })

  it('guard runs the action at once when clean, and leaves a clean form open', () => {
    const { result } = slot()
    const action = vi.fn()
    act(() => result.current.open('edit'))
    act(() => result.current.guard(action))
    expect(action).toHaveBeenCalledTimes(1)
    expect(result.current.openKey).toBe('edit')
  })

  it('guard over typed input waits; Discard changes closes the form, then runs it', () => {
    const { result } = slot()
    const action = vi.fn()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.guard(action))
    expect(action).not.toHaveBeenCalled()
    expect(result.current.asking).toBe(true)

    act(() => result.current.answer(true))
    expect(action).toHaveBeenCalledTimes(1)
    expect(result.current).toMatchObject({ openKey: null, dirty: false, asking: false })
  })

  it('guard over typed input, Keep editing: the action never runs', () => {
    const { result } = slot()
    const action = vi.fn()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.guard(action))
    act(() => result.current.answer(false))
    expect(action).not.toHaveBeenCalled()
    expect(result.current).toMatchObject({ openKey: 'edit', dirty: true, asking: false })
  })

  it('setDirty(false) (the form unmounting) clears a pending question', () => {
    const { result } = slot()
    const action = vi.fn()
    act(() => result.current.open('edit'))
    act(() => result.current.setDirty(true))
    act(() => result.current.guard(action))
    act(() => result.current.setDirty(false))
    expect(result.current).toMatchObject({ openKey: 'edit', dirty: false, asking: false })
    act(() => result.current.answer(true))
    expect(action).not.toHaveBeenCalled()
  })

  it('keeps its identity while nothing changes', () => {
    const { result, rerender } = slot()
    const first = result.current
    rerender()
    expect(result.current).toBe(first)
  })
})
