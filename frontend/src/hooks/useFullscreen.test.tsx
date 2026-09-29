import { act, fireEvent, render, screen } from '@testing-library/react'
import { useCallback, useRef, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ESCAPE_SETTLE_MS, GRANT_TIMEOUT_MS, useFullscreen } from './useFullscreen'

/**
 * A stand-in Fullscreen API. jsdom has none, which is the "unavailable" case
 * for free; this adds one that behaves like a browser: the request resolves
 * (or rejects), and `fullscreenchange` fires AFTERWARDS, in a later task — the
 * hook must take its state from the event, not from having asked.
 */
function stubFullscreenApi({
  reject = false,
  throws = false,
  pending = false,
}: { reject?: boolean; throws?: boolean; pending?: boolean } = {}) {
  let current: Element | null = null
  const grant = (element: Element) => {
    current = element
  }
  const change = () => setTimeout(() => document.dispatchEvent(new Event('fullscreenchange')), 0)
  Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, get: () => true })
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => current })
  // Each pending request's own settlers, in order, so a test can answer any of them late.
  const settlers: { resolve: () => void; reject: (reason: unknown) => void }[] = []
  const request = vi.fn(function (this: Element) {
    if (throws) throw new TypeError('not allowed')
    if (reject) return Promise.reject(new TypeError('Permissions check failed'))
    // A host that does not answer (observed in an embedded browser pane) — until a test makes it.
    if (pending)
      return new Promise<void>((resolve, rejectPending) => {
        settlers.push({ resolve, reject: rejectPending })
      })
    grant(this)
    change()
    return Promise.resolve()
  })
  const exit = vi.fn(() => {
    current = null
    change()
    return Promise.resolve()
  })
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, writable: true, value: request })
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, writable: true, value: exit })
  /** The browser leaving full screen on its own (the reader pressed Escape, or F11). */
  const browserExits = () => {
    current = null
    document.dispatchEvent(new Event('fullscreenchange'))
  }
  /** The browser granting a request late (after the overlay stood in for it). */
  const grantLate = (element: Element) => {
    grant(element)
    document.dispatchEvent(new Event('fullscreenchange'))
    settlers[settlers.length - 1]?.resolve()
  }
  /** The browser refusing a pending request late: the latest one, or the `index`th. */
  const rejectLate = (index = settlers.length - 1) => settlers[index]?.reject(new TypeError('Permissions check failed'))
  return { request, exit, browserExits, grantLate, rejectLate, isFullscreen: () => current !== null }
}

function removeFullscreenApi() {
  for (const key of ['fullscreenEnabled', 'fullscreenElement', 'exitFullscreen'] as const) {
    delete (document as unknown as Record<string, unknown>)[key]
  }
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).requestFullscreen
}

/** Lets the stub's `fullscreenchange` (a 0 ms timer) and any promise settle, inside act. */
const settle = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 5)))

function Harness({
  as = 'div',
  escapeFirst,
  children,
}: {
  as?: 'div' | 'dialog'
  escapeFirst?: () => readonly Element[]
  children?: ReactNode
}) {
  const button = useRef<HTMLButtonElement | null>(null)
  const returnFocus = useCallback(() => button.current, [])
  const { ref, mode, isFullscreen, supported, enter, exit } = useFullscreen<HTMLElement>({ returnFocus, escapeFirst })
  const Target = as
  return (
    <div>
      <button type="button">before</button>
      <Target
        ref={ref}
        data-testid="target"
        data-mode={mode ?? 'off'}
        data-supported={String(supported)}
        role={isFullscreen ? 'dialog' : undefined}
        aria-modal={isFullscreen ? true : undefined}
        aria-label="Chart"
      >
        <button ref={button} type="button" onClick={isFullscreen ? exit : enter}>
          {isFullscreen ? 'Exit full screen' : 'Full screen'}
        </button>
        <a href="#details">details</a>
        {children}
        <button type="button">last</button>
      </Target>
      <button type="button">after</button>
    </div>
  )
}

const target = () => screen.getByTestId('target')
const toggle = () => screen.getByRole('button', { name: /full screen/i })

afterEach(() => {
  removeFullscreenApi()
  document.documentElement.style.overflow = ''
})

describe('useFullscreen — the Fullscreen API', () => {
  it('enters on the element from the click, and takes its state from fullscreenchange', async () => {
    const api = stubFullscreenApi()
    render(<Harness />)
    expect(target()).toHaveAttribute('data-supported', 'true')
    fireEvent.click(toggle())
    expect(api.request).toHaveBeenCalledTimes(1)
    expect(api.request.mock.contexts[0]).toBe(target())
    // Asked, not yet granted: still off until the browser says so.
    expect(target()).toHaveAttribute('data-mode', 'off')
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'api')
    expect(toggle()).toHaveAccessibleName('Exit full screen')
  })

  it('"Exit full screen" calls exitFullscreen, and focus returns to the button', async () => {
    const api = stubFullscreenApi()
    render(<Harness />)
    toggle().focus()
    fireEvent.click(toggle())
    await settle()
    // Something else inside took focus while full screen.
    screen.getByRole('link', { name: 'details' }).focus()
    fireEvent.click(toggle())
    expect(api.exit).toHaveBeenCalledTimes(1)
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'off')
    expect(document.activeElement).toBe(toggle())
    expect(toggle()).toHaveAccessibleName('Full screen')
  })

  it('the browser leaving on its own (Escape) turns it off, restores the scroll and returns focus', async () => {
    const api = stubFullscreenApi()
    const { container } = render(<Harness />)
    // The app scrolls <main>, not the window: an ancestor scroll position.
    const scroller = container.parentElement as HTMLElement
    let top = 480
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => (top = v) })
    fireEvent.click(toggle())
    await settle()
    screen.getByRole('button', { name: 'last' }).focus()
    top = 0 // the page re-laid out underneath and the scroller clamped
    act(() => api.browserExits())
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'off')
    expect(top).toBe(480)
    expect(document.activeElement).toBe(toggle())
    delete (scroller as unknown as Record<string, unknown>).scrollTop
  })

  it('Tab is trapped inside while full screen (the page underneath is focusable and invisible)', async () => {
    stubFullscreenApi()
    render(<Harness />)
    fireEvent.click(toggle())
    await settle()
    const last = screen.getByRole('button', { name: 'last' })
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(toggle())
  })

  it('unmounting while full screen leaves full screen', async () => {
    const api = stubFullscreenApi()
    const { unmount } = render(<Harness />)
    fireEvent.click(toggle())
    await settle()
    unmount()
    expect(api.exit).toHaveBeenCalledTimes(1)
  })

  it('announces a re-layout (resize) on the way in and on the way out', async () => {
    stubFullscreenApi()
    const onResize = vi.fn()
    window.addEventListener('resize', onResize)
    try {
      render(<Harness />)
      fireEvent.click(toggle())
      await settle()
      expect(onResize).toHaveBeenCalledTimes(1)
      fireEvent.click(toggle())
      await settle()
      expect(onResize).toHaveBeenCalledTimes(2)
    } finally {
      window.removeEventListener('resize', onResize)
    }
  })
})

describe('useFullscreen — the maximised overlay fallback', () => {
  it('API absent → the overlay at once, with the modal keyboard: Tab cycles, Escape exits', async () => {
    render(<Harness />)
    expect(target()).toHaveAttribute('data-supported', 'false')
    toggle().focus()
    fireEvent.click(toggle())
    expect(target()).toHaveAttribute('data-mode', 'overlay')
    expect(screen.getByRole('dialog', { name: 'Chart' })).toBe(target())
    // The page underneath does not scroll behind the overlay.
    expect(document.documentElement.style.overflow).toBe('hidden')

    const last = screen.getByRole('button', { name: 'last' })
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(toggle())
    fireEvent.keyDown(toggle(), { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    // Focus that escaped (a click on the page) is pulled back in on the next Tab.
    const outside = screen.getByRole('button', { name: 'after' })
    outside.focus()
    fireEvent.keyDown(outside, { key: 'Tab' })
    expect(target()).toContainElement(document.activeElement as HTMLElement)

    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'off')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(toggle())
    expect(document.documentElement.style.overflow).toBe('')
  })

  it('the request REJECTED (not a user gesture, iframe without allow="fullscreen") → the overlay', async () => {
    const api = stubFullscreenApi({ reject: true })
    render(<Harness />)
    fireEvent.click(toggle())
    expect(api.request).toHaveBeenCalledTimes(1)
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'overlay')
    fireEvent.click(toggle())
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'off')
    // The overlay never asked the API to leave a full screen it never entered.
    expect(api.exit).not.toHaveBeenCalled()
  })

  it('a request NEVER answered (an embedding host that ignores it) → the overlay after the grant timeout', async () => {
    vi.useFakeTimers()
    try {
      const api = stubFullscreenApi({ pending: true })
      render(<Harness />)
      fireEvent.click(toggle())
      expect(api.request).toHaveBeenCalledTimes(1)
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS - 1))
      // Not yet: a real grant can take this long (macOS animates into full screen).
      expect(target()).toHaveAttribute('data-mode', 'off')
      act(() => vi.advanceTimersByTime(1))
      expect(target()).toHaveAttribute('data-mode', 'overlay')
      // The browser grants it after all: the real thing replaces the stand-in.
      act(() => api.grantLate(target()))
      expect(target()).toHaveAttribute('data-mode', 'api')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a request GRANTED in time never flashes the overlay', async () => {
    vi.useFakeTimers()
    try {
      stubFullscreenApi()
      render(<Harness />)
      fireEvent.click(toggle())
      act(() => vi.advanceTimersByTime(1)) // the stub's fullscreenchange
      expect(target()).toHaveAttribute('data-mode', 'api')
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS * 2))
      expect(target()).toHaveAttribute('data-mode', 'api')
    } finally {
      vi.useRealTimers()
    }
  })

  it('the request THROWING synchronously → the overlay', async () => {
    stubFullscreenApi({ throws: true })
    render(<Harness />)
    fireEvent.click(toggle())
    await settle()
    expect(target()).toHaveAttribute('data-mode', 'overlay')
  })

  it('never calls requestFullscreen on a <dialog> element', async () => {
    const api = stubFullscreenApi()
    render(<Harness as="dialog" />)
    // A closed <dialog> hides its content from the accessibility tree: by text.
    fireEvent.click(screen.getByText('Full screen'))
    await settle()
    expect(api.request).not.toHaveBeenCalled()
    expect(target()).toHaveAttribute('data-mode', 'overlay')
  })

  it('restores every scrolled ancestor on exit', async () => {
    const { container } = render(<Harness />)
    const scroller = container.parentElement as HTMLElement
    let top = 900
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, get: () => top, set: (v: number) => (top = v) })
    fireEvent.click(toggle())
    top = 120 // the fixed overlay left the flow and the page got shorter
    fireEvent.keyDown(toggle(), { key: 'Escape' })
    await settle()
    expect(top).toBe(900)
    delete (scroller as unknown as Record<string, unknown>).scrollTop
  })
})

describe('useFullscreen — a late answer to a request the reader no longer wants (review F2)', () => {
  it('a late GRANT after the reader left the overlay is undone, not adopted', () => {
    vi.useFakeTimers()
    try {
      const api = stubFullscreenApi({ pending: true })
      render(<Harness />)
      fireEvent.click(toggle())
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS))
      expect(target()).toHaveAttribute('data-mode', 'overlay')
      fireEvent.click(toggle()) // "Exit full screen"
      expect(target()).toHaveAttribute('data-mode', 'off')
      // The browser answers the ORIGINAL request only now.
      act(() => api.grantLate(target()))
      expect(target()).toHaveAttribute('data-mode', 'off')
      // …and is told to leave the full screen it has just entered.
      expect(api.exit).toHaveBeenCalledTimes(1)
      act(() => vi.advanceTimersByTime(1)) // the stub's fullscreenchange for that exit
      expect(api.isFullscreen()).toBe(false)
      expect(target()).toHaveAttribute('data-mode', 'off')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a late REFUSAL after the reader left the overlay re-opens nothing', async () => {
    vi.useFakeTimers()
    try {
      const api = stubFullscreenApi({ pending: true })
      render(<Harness />)
      fireEvent.click(toggle())
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS))
      fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Escape' })
      expect(target()).toHaveAttribute('data-mode', 'off')
      await act(async () => {
        api.rejectLate()
        await Promise.resolve()
        await Promise.resolve()
      })
      expect(target()).toHaveAttribute('data-mode', 'off')
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(document.documentElement.style.overflow).toBe('')
    } finally {
      vi.useRealTimers()
    }
  })

  it('the refusal of an OLD request does not decide a newer one', async () => {
    vi.useFakeTimers()
    try {
      const api = stubFullscreenApi({ pending: true })
      render(<Harness />)
      fireEvent.click(toggle())
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS))
      fireEvent.click(toggle()) // leave the stand-in overlay…
      fireEvent.click(toggle()) // …and ask again: a second request, still unanswered
      expect(api.request).toHaveBeenCalledTimes(2)
      await act(async () => {
        api.rejectLate(0)
        await Promise.resolve()
        await Promise.resolve()
      })
      // The second request has not been answered, nor timed out: still waiting.
      expect(target()).toHaveAttribute('data-mode', 'off')
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS))
      expect(target()).toHaveAttribute('data-mode', 'overlay')
    } finally {
      vi.useRealTimers()
    }
  })

  it('a second press while the first request is still out sends no second request', () => {
    vi.useFakeTimers()
    try {
      const api = stubFullscreenApi({ pending: true })
      render(<Harness />)
      fireEvent.click(toggle())
      fireEvent.click(toggle()) // still "Full screen": nothing has answered yet
      expect(api.request).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a late grant for a reader who is STILL in the overlay replaces it (unchanged)', () => {
    vi.useFakeTimers()
    try {
      const api = stubFullscreenApi({ pending: true })
      render(<Harness />)
      fireEvent.click(toggle())
      act(() => vi.advanceTimersByTime(GRANT_TIMEOUT_MS))
      act(() => api.grantLate(target()))
      expect(target()).toHaveAttribute('data-mode', 'api')
      expect(api.exit).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('useFullscreen — the page behind is inert (review A10)', () => {
  const outside = () => [
    screen.getByRole('button', { name: 'before', hidden: true }),
    screen.getByRole('button', { name: 'after', hidden: true }),
  ]

  it('overlay: every sibling of the element and of its ancestors is inert, and live again on exit', async () => {
    const { container } = render(<Harness />)
    // A sibling of an ANCESTOR, not just of the element.
    const aside = document.createElement('aside')
    document.body.appendChild(aside)
    try {
      fireEvent.click(toggle())
      expect(target()).toHaveAttribute('data-mode', 'overlay')
      for (const el of outside()) expect(el).toHaveAttribute('inert')
      expect(aside).toHaveAttribute('inert')
      // The element itself, its content and its ancestors stay live.
      expect(target()).not.toHaveAttribute('inert')
      expect(toggle()).not.toHaveAttribute('inert')
      expect(container).not.toHaveAttribute('inert')
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      await settle()
      for (const el of outside()) expect(el).not.toHaveAttribute('inert')
      expect(aside).not.toHaveAttribute('inert')
    } finally {
      aside.remove()
    }
  })

  it('the Fullscreen API too, and an element that was inert already stays inert', async () => {
    stubFullscreenApi()
    const preInert = document.createElement('div')
    preInert.setAttribute('inert', '')
    document.body.appendChild(preInert)
    try {
      render(<Harness />)
      fireEvent.click(toggle())
      await settle()
      expect(target()).toHaveAttribute('data-mode', 'api')
      for (const el of outside()) expect(el).toHaveAttribute('inert')
      fireEvent.click(toggle())
      await settle()
      for (const el of outside()) expect(el).not.toHaveAttribute('inert')
      expect(preInert).toHaveAttribute('inert')
    } finally {
      preInert.remove()
    }
  })

  it('unmounting while full screen gives the page back', () => {
    const { unmount } = render(<Harness />)
    const aside = document.createElement('aside')
    document.body.appendChild(aside)
    try {
      fireEvent.click(toggle())
      expect(aside).toHaveAttribute('inert')
      unmount()
      expect(aside).not.toHaveAttribute('inert')
    } finally {
      aside.remove()
    }
  })
})

describe('useFullscreen — Escape, innermost first (review A7)', () => {
  /**
   * A tooltip inside the element, dismissed by its OWN document listener —
   * which, like the chart cursor's, is registered before full screen opens
   * and hides the box at once, so by the time the modal's listener runs the
   * box is already gone. `stuck`: a tooltip that ignores Escape altogether.
   */
  function withTip({ stuck = false } = {}) {
    let tip: HTMLElement | null = null
    const onKey = (event: KeyboardEvent) => {
      if (!stuck && event.key === 'Escape' && tip) tip.hidden = true
    }
    document.addEventListener('keydown', onKey)
    const escapeFirst = () => (tip && !tip.hidden ? [tip] : [])
    function Tip() {
      return (
        <span
          ref={(node) => {
            tip = node
          }}
          data-testid="tip"
          hidden
        >
          a tooltip
        </span>
      )
    }
    const show = () => {
      if (tip) tip.hidden = false
    }
    return { escapeFirst, Tip, show, cleanup: () => document.removeEventListener('keydown', onKey) }
  }

  it('overlay: with a tooltip up and focus elsewhere, the first Escape dismisses the tooltip ONLY; the next one leaves', async () => {
    const tip = withTip()
    try {
      render(
        <Harness escapeFirst={tip.escapeFirst}>
          <tip.Tip />
        </Harness>,
      )
      fireEvent.click(toggle())
      tip.show()
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      expect(screen.getByTestId('tip')).not.toBeVisible()
      expect(target()).toHaveAttribute('data-mode', 'overlay')
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      await settle()
      expect(target()).toHaveAttribute('data-mode', 'off')
    } finally {
      tip.cleanup()
    }
  })

  it('Fullscreen API: the same order when the page gets the key', async () => {
    const api = stubFullscreenApi()
    const tip = withTip()
    try {
      render(
        <Harness escapeFirst={tip.escapeFirst}>
          <tip.Tip />
        </Harness>,
      )
      fireEvent.click(toggle())
      await settle()
      tip.show()
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      await settle()
      expect(target()).toHaveAttribute('data-mode', 'api')
      expect(api.exit).not.toHaveBeenCalled()
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      await settle()
      expect(api.exit).toHaveBeenCalledTimes(1)
      expect(target()).toHaveAttribute('data-mode', 'off')
    } finally {
      tip.cleanup()
    }
  })

  it('a tooltip Escape does NOT dismiss is not waited on twice: the reader is never kept in full screen', () => {
    const tip = withTip({ stuck: true })
    vi.useFakeTimers()
    try {
      render(
        <Harness escapeFirst={tip.escapeFirst}>
          <tip.Tip />
        </Harness>,
      )
      fireEvent.click(toggle())
      tip.show()
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      expect(target()).toHaveAttribute('data-mode', 'overlay')
      act(() => vi.advanceTimersByTime(ESCAPE_SETTLE_MS))
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      expect(target()).toHaveAttribute('data-mode', 'off')
    } finally {
      vi.useRealTimers()
      tip.cleanup()
    }
  })

  it('with nothing showing, one Escape leaves', async () => {
    const tip = withTip()
    try {
      render(
        <Harness escapeFirst={tip.escapeFirst}>
          <tip.Tip />
        </Harness>,
      )
      fireEvent.click(toggle())
      fireEvent.keyDown(toggle(), { key: 'Escape' })
      await settle()
      expect(target()).toHaveAttribute('data-mode', 'off')
    } finally {
      tip.cleanup()
    }
  })
})
