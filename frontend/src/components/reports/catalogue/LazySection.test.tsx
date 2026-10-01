/**
 * K2: `useNearViewport` and `LazySection`.
 *
 * jsdom has no `IntersectionObserver`, so a fake one records how it was built
 * (root, margin) and lets the test say when the target intersects. The real
 * browser half — that a default-root observer does NOT pre-mount across an
 * inner scroller — is B0's scroll-margin e2e; this file pins the half that
 * decides it: the observer's root is the page's scroller.
 */
import { act, render, screen } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LazySection from './LazySection'
import { MAIN_SCROLLER_SELECTOR, useNearViewport } from './useNearViewport'

interface FakeObserver {
  callback: IntersectionObserverCallback
  options: IntersectionObserverInit | undefined
  targets: Element[]
  disconnect: ReturnType<typeof vi.fn>
  fire: (isIntersecting: boolean) => void
}

const observers: FakeObserver[] = []

class FakeIntersectionObserver {
  targets: Element[] = []
  disconnect = vi.fn()
  constructor(
    public callback: IntersectionObserverCallback,
    public options?: IntersectionObserverInit,
  ) {
    observers.push(this as unknown as FakeObserver)
  }
  observe(target: Element) {
    this.targets.push(target)
  }
  unobserve() {}
  takeRecords() {
    return []
  }
  fire(isIntersecting: boolean) {
    const entries = this.targets.map((target) => ({ target, isIntersecting }) as IntersectionObserverEntry)
    this.callback(entries, this as unknown as IntersectionObserver)
  }
}

const realObserver = (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver

function installObserver() {
  Object.defineProperty(globalThis, 'IntersectionObserver', {
    configurable: true,
    writable: true,
    value: FakeIntersectionObserver,
  })
}

function removeObserver() {
  Object.defineProperty(globalThis, 'IntersectionObserver', { configurable: true, writable: true, value: undefined })
}

const onMount = vi.fn()
const onUnmount = vi.fn()
function Section() {
  useEffect(() => {
    onMount()
    return () => onUnmount()
  }, [])
  return (
    <section data-testid="real-section">
      <h2>Suite by day</h2>
    </section>
  )
}

/** The app shell's scroller: pages scroll inside `<main id="main-content">`, not the window. */
function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ overflow: 'hidden' }}>
      <main id="main-content" style={{ overflow: 'auto' }}>
        <div className="wrapper">{children}</div>
      </main>
    </div>
  )
}

describe('useNearViewport / LazySection (K2)', () => {
  beforeEach(() => {
    observers.length = 0
    onMount.mockClear()
    onUnmount.mockClear()
    installObserver()
  })
  afterEach(() => {
    Object.defineProperty(globalThis, 'IntersectionObserver', { configurable: true, writable: true, value: realObserver })
  })

  it('observes with the page scroller (#main-content) as the root, and the margin on it', () => {
    render(
      <Shell>
        <LazySection minHeight={320} label="trends-heatmap">
          <Section />
        </LazySection>
      </Shell>,
    )
    expect(observers).toHaveLength(1)
    const main = document.querySelector(MAIN_SCROLLER_SELECTOR)
    expect(main).not.toBeNull()
    // NOT null: a default-root observer clips the target by <main> first, so a
    // margin on the viewport never pre-mounts anything below main's fold.
    expect(observers[0].options?.root).toBe(main)
    expect(observers[0].options?.rootMargin).toBe('200px 0px 200px 0px')
  })

  it('falls back to the viewport when there is no app scroller (a page outside the shell)', () => {
    render(
      <LazySection minHeight={100} label="bare">
        <Section />
      </LazySection>,
    )
    expect(observers[0].options?.root ?? null).toBeNull()
  })

  it('takes a caller margin', () => {
    function Probe() {
      const ref = useRef<HTMLDivElement>(null)
      const near = useNearViewport(ref, { marginPx: 600 })
      return <div ref={ref}>{near ? 'near' : 'far'}</div>
    }
    render(
      <Shell>
        <Probe />
      </Shell>,
    )
    expect(observers[0].options?.rootMargin).toBe('600px 0px 600px 0px')
  })

  it('renders a placeholder of the given height, hidden from assistive tech and with no heading', () => {
    const { container } = render(
      <Shell>
        <LazySection minHeight={320} label="trends-heatmap">
          <Section />
        </LazySection>
      </Shell>,
    )
    const placeholder = container.querySelector('[data-lazy-section]') as HTMLElement
    expect(placeholder).not.toBeNull()
    expect(placeholder.getAttribute('data-lazy-section')).toBe('trends-heatmap')
    expect(placeholder.getAttribute('aria-hidden')).toBe('true')
    expect(placeholder.style.minHeight).toBe('320px')
    expect(placeholder.querySelector('h1, h2, h3, h4, h5, h6, [role="heading"]')).toBeNull()
    expect(screen.queryByRole('heading')).toBeNull()
    expect(screen.queryByTestId('real-section')).toBeNull()
    expect(onMount).not.toHaveBeenCalled()
  })

  it('stays a placeholder while the section is far away', () => {
    render(
      <Shell>
        <LazySection minHeight={320} label="far">
          <Section />
        </LazySection>
      </Shell>,
    )
    act(() => observers[0].fire(false))
    expect(screen.queryByTestId('real-section')).toBeNull()
  })

  it('mounts the section once it is near, and the placeholder is gone', () => {
    const { container } = render(
      <Shell>
        <LazySection minHeight={320} label="near">
          <Section />
        </LazySection>
      </Shell>,
    )
    act(() => observers[0].fire(true))
    expect(screen.getByTestId('real-section')).toBeInTheDocument()
    expect(container.querySelector('[data-lazy-section]')).toBeNull()
    expect(observers[0].disconnect).toHaveBeenCalled()
  })

  it('latches: scrolling away again never unmounts (or remounts) the section', () => {
    const { rerender } = render(
      <Shell>
        <LazySection minHeight={320} label="latch">
          <Section />
        </LazySection>
      </Shell>,
    )
    act(() => observers[0].fire(true))
    const node = screen.getByTestId('real-section')
    act(() => observers[0].fire(false))
    rerender(
      <Shell>
        <LazySection minHeight={320} label="latch">
          <Section />
        </LazySection>
      </Shell>,
    )
    expect(screen.getByTestId('real-section')).toBe(node)
    // No NEW observer after the latch: nothing is watching any more.
    expect(observers).toHaveLength(1)
    expect(onMount).toHaveBeenCalledTimes(1)
    expect(onUnmount).not.toHaveBeenCalled()
    expect(screen.getAllByTestId('real-section')).toHaveLength(1)
  })

  it('disconnects its observer when unmounted before it was ever near', () => {
    const { unmount } = render(
      <Shell>
        <LazySection minHeight={320} label="gone">
          <Section />
        </LazySection>
      </Shell>,
    )
    unmount()
    expect(observers[0].disconnect).toHaveBeenCalled()
  })

  it('mounts at once where IntersectionObserver does not exist (jsdom, old browsers)', () => {
    removeObserver()
    const { container } = render(
      <Shell>
        <LazySection minHeight={320} label="no-io">
          <Section />
        </LazySection>
      </Shell>,
    )
    expect(screen.getByTestId('real-section')).toBeInTheDocument()
    expect(container.querySelector('[data-lazy-section]')).toBeNull()
    expect(observers).toHaveLength(0)
  })
})
