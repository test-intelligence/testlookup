/**
 * `lazyWithRetry` (moved out of App.tsx so the report pages' lazy sections
 * share it, R1-1): a failed chunk import reloads the page ONCE per session,
 * a second failure reaches the nearest error boundary, and a success re-arms
 * the one-shot reload.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { Suspense, type ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { CHUNK_RELOAD_FLAG, lazyWithRetry } from './lazyWithRetry'

const reload = vi.fn()
const realLocation = window.location

beforeEach(() => {
  reload.mockReset()
  sessionStorage.clear()
  Object.defineProperty(window, 'location', { configurable: true, value: { ...realLocation, reload } })
})
afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation })
})

function mount(importFn: () => Promise<{ default: () => ReactElement }>) {
  const Section = lazyWithRetry(importFn)
  return render(
    <SectionErrorBoundary message="Failed to load charts">
      <Suspense fallback={<p>loading</p>}>
        <Section />
      </Suspense>
    </SectionErrorBoundary>,
  )
}

const stale = () => Promise.reject(new Error('Failed to fetch dynamically imported module'))

describe('lazyWithRetry', () => {
  it('a stale chunk reloads the page once instead of showing an error', async () => {
    mount(stale)
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1))
    expect(sessionStorage.getItem(CHUNK_RELOAD_FLAG)).toBe('1')
    expect(screen.queryByText('Failed to load charts')).toBeNull()
    expect(screen.getByText('loading')).toBeInTheDocument()
  })

  it('after the one reload, a failure reaches the section boundary: no reload loop', async () => {
    sessionStorage.setItem(CHUNK_RELOAD_FLAG, '1')
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      mount(stale)
      expect(await screen.findByText('Failed to load charts')).toBeInTheDocument()
      expect(reload).not.toHaveBeenCalled()
    } finally {
      spy.mockRestore()
    }
  })

  it('a successful load draws the section and re-arms the reload', async () => {
    sessionStorage.setItem(CHUNK_RELOAD_FLAG, '1')
    mount(() => Promise.resolve({ default: () => <p>section</p> }))
    expect(await screen.findByText('section')).toBeInTheDocument()
    expect(sessionStorage.getItem(CHUNK_RELOAD_FLAG)).toBeNull()
  })
})
