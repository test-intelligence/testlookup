/**
 * Full screen for one element (VIZ-608): the Fullscreen API where the browser
 * allows it, and a CSS "maximised" overlay where it does not.
 *
 *   const fs = useFullscreen<HTMLDivElement>({ returnFocus: () => buttonRef.current })
 *   <div ref={fs.ref} data-fullscreen={fs.mode ?? undefined}>…</div>
 *   <button ref={buttonRef} onClick={fs.isFullscreen ? fs.exit : fs.enter}>
 *
 * WHY A FALLBACK AT ALL. `requestFullscreen` is missing on iPhone Safari, is
 * refused inside an iframe that lacks `allow="fullscreen"`, is refused when it
 * is not called from a user gesture, and can be refused by the user agent for
 * no stated reason. Each of those used to mean "the button does nothing".
 * Here each of them — the method absent, `fullscreenEnabled` false, the call
 * throwing, the promise rejecting — lands in the same overlay instead. So does
 * a request that is NEITHER granted nor refused: an embedding host (an
 * Electron-style webview that has not wired up full screen) can leave the
 * promise pending for ever, which was observed in the desktop app's browser
 * pane. After `GRANT_TIMEOUT_MS` with no answer the overlay is shown; if the
 * browser grants the request after all, `fullscreenchange` switches to it.
 *
 * A LATE ANSWER TO A REQUEST NOBODY WANTS ANY MORE. A request is only ever
 * acted on while the reader still wants full screen (`wanted`) and while it
 * is the latest one (`requestSeq`). A reader who left the overlay before the
 * browser answered has left: a late refusal re-opens nothing, and a late
 * GRANT — the browser is now really full screen on the element — is undone
 * with `exitFullscreen` rather than adopted.
 *
 * WHAT THE CALLER RENDERS. This hook owns behaviour, not markup: the caller
 * puts `ref` on the element, and while `mode === 'overlay'` gives it
 * `role="dialog"`, `aria-modal="true"`, a label, and the fixed full-viewport
 * styling. It must NOT be a `<dialog>` element — `requestFullscreen` on one is
 * an error in the spec, so a `<dialog>` goes straight to the overlay.
 *
 * KEYBOARD. While full screen (either mode) the element is a modal: Tab and
 * Shift+Tab cycle inside it and Escape exits. That is `useModalFocus`, shared
 * with every other dialog in the app, so an open dialog or menu inside the
 * full-screen chart gets Escape first rather than both closing on one press.
 * Under the Fullscreen API the browser also exits on Escape by itself; the
 * trap still matters there, because Tab would otherwise walk out onto the
 * page underneath, which is focusable and invisible.
 *
 * ESCAPE, INNERMOST FIRST. One Escape does one thing: an open menu inside
 * closes first (it stops the key itself), then a tooltip that Escape
 * dismisses (`escapeFirst`), and only an Escape with nothing of the kind
 * showing leaves full screen. What is showing is read in the CAPTURE phase,
 * before any listener has acted on the key: a tooltip's own document
 * listener hides it on the same key press, and by the time the modal's
 * listener runs the tooltip is already gone from the DOM. Something that
 * Escape did NOT dismiss is not waited on a second time, so a stuck tooltip
 * can never keep the reader in full screen. Under the real Fullscreen API
 * the browser may take Escape for itself and leave at once; the caller then
 * closes whatever it had open when the mode goes off.
 *
 * THE PAGE BEHIND IS INERT. `aria-modal` alone does not stop a screen
 * reader's browse cursor from walking out of the dialog into the covered
 * page (Chromium keeps it in the tree for the overlay), and Tab trapping
 * covers only Tab. So while full screen, every sibling of the element and of
 * each of its ancestors, up to `<body>`, is `inert` — and put back exactly as
 * it was on exit and on unmount. Anything that must still speak (the page's
 * one announcer) has to speak from inside the element meanwhile
 * (`ChartAnnouncerOutlet`).
 *
 * LEAVING. The page goes back exactly where it was: every scrolled ancestor
 * (in this app that is `<main>`, not the window) gets its scroll position
 * back, and focus goes to `returnFocus()` — the button that entered — without
 * scrolling the page to reach it.
 *
 * RE-LAYOUT. Width-driven charts re-measure through their ResizeObservers on
 * their own; a `resize` event is dispatched on every change as well, for
 * anything that sizes from the window.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useModalFocus } from './useModalFocus'

export type FullscreenMode = 'api' | 'overlay'

/**
 * How long a full-screen request may go unanswered before the overlay stands
 * in for it. Long enough for a real grant (macOS animates into full screen,
 * and the promise settles after the animation), short enough that a reader
 * who pressed the button is not left wondering whether it worked.
 */
export const GRANT_TIMEOUT_MS = 1500

export interface FullscreenOptions {
  /**
   * Where focus goes on exit (the button that entered). Without it, focus
   * returns to whatever had it on entry. A function rather than a ref: a ref
   * handed to a hook makes the lint (react-hooks/refs) treat everything the
   * hook returns as a ref, which the caller then cannot read while rendering.
   */
  returnFocus?: () => HTMLElement | null
  /**
   * The things inside the element that Escape dismisses before it leaves full
   * screen — a pointer tooltip — as they are showing NOW (empty when none
   * is). Read at the start of each Escape key press.
   */
  escapeFirst?: () => readonly Element[]
}

/**
 * How long a dismissible thing has to go after the Escape that was meant for
 * it. One still showing after this was not dismissed by that Escape, and the
 * next Escape leaves full screen instead of waiting on it again.
 */
export const ESCAPE_SETTLE_MS = 150

export interface Fullscreen<T extends HTMLElement> {
  /** Put this on the element that goes full screen. */
  ref: (node: T | null) => void
  /** The element, once mounted — for a portal container. */
  element: T | null
  /** Which kind of full screen is on, or `null` when it is off. */
  mode: FullscreenMode | null
  isFullscreen: boolean
  /** True when the browser offers the Fullscreen API here; false means `enter` goes straight to the overlay. */
  supported: boolean
  /** Call from a user gesture (a click or key handler), never from an effect. */
  enter: () => void
  exit: () => void
}

/** Whether the Fullscreen API can be used in this document at all. */
function apiSupported(): boolean {
  return (
    typeof document !== 'undefined' &&
    document.fullscreenEnabled === true &&
    typeof HTMLElement !== 'undefined' &&
    typeof HTMLElement.prototype.requestFullscreen === 'function'
  )
}

interface ScrollMark {
  node: Element
  left: number
  top: number
}

/**
 * Every scroll position between the element and the document: the ancestors
 * that are scrolled, and the document's own scroller. Taken before entering,
 * because taking the element out of the flow (the overlay is `fixed`) shortens
 * the page, and a scroller clamps its position to the shorter content.
 */
function markScroll(node: Element): ScrollMark[] {
  const marks: ScrollMark[] = []
  for (let el = node.parentElement; el; el = el.parentElement) {
    if (el.scrollTop !== 0 || el.scrollLeft !== 0) marks.push({ node: el, left: el.scrollLeft, top: el.scrollTop })
  }
  const root = document.scrollingElement
  if (root && !marks.some((m) => m.node === root)) marks.push({ node: root, left: root.scrollLeft, top: root.scrollTop })
  return marks
}

function restoreScroll(marks: ScrollMark[]) {
  for (const { node, left, top } of marks) {
    if (!node.isConnected) continue
    node.scrollLeft = left
    node.scrollTop = top
  }
}

/**
 * Makes everything on the page except `node` (and its ancestors) inert: every
 * sibling of `node` and of each ancestor up to `<body>`. Returns the undo,
 * which touches only the elements this call made inert — one that was inert
 * already stays so.
 */
function inertPageAround(node: Element): () => void {
  const made: Element[] = []
  for (let el: Element | null = node; el && el !== document.body && el.parentElement; el = el.parentElement) {
    for (const sibling of Array.from(el.parentElement.children)) {
      if (sibling === el || sibling.hasAttribute('inert')) continue
      sibling.setAttribute('inert', '')
      made.push(sibling)
    }
  }
  return () => {
    for (const el of made) el.removeAttribute('inert')
  }
}

export function useFullscreen<T extends HTMLElement = HTMLDivElement>({
  returnFocus,
  escapeFirst,
}: FullscreenOptions = {}): Fullscreen<T> {
  const [mode, setMode] = useState<FullscreenMode | null>(null)
  const [element, setElement] = useState<T | null>(null)
  const supported = apiSupported()
  // Scroll positions taken on entry; restored once, on exit.
  const scrollMarks = useRef<ScrollMark[] | null>(null)
  // The pending request's "no answer" timer (see GRANT_TIMEOUT_MS).
  const grantTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const settleRequest = useCallback(() => {
    if (grantTimer.current !== null) clearTimeout(grantTimer.current)
    grantTimer.current = null
  }, [])
  // Whether the reader wants full screen now (set by `enter`, cleared by
  // leaving in any way), and which request is the live one: a request's late
  // answer is acted on only while both still hold.
  const wanted = useRef(false)
  const requestSeq = useRef(0)
  // The mode as last set, for the event listener (which must not wait for a render).
  const modeNow = useRef<FullscreenMode | null>(null)
  const switchMode = useCallback((next: FullscreenMode | null) => {
    modeNow.current = next
    if (next === null) wanted.current = false
    setMode(next)
  }, [])
  // The latest callbacks, read when they are needed, so an inline arrow does not re-run an effect.
  const returnFocusFn = useRef(returnFocus)
  const escapeFirstFn = useRef(escapeFirst)
  useEffect(() => {
    returnFocusFn.current = returnFocus
    escapeFirstFn.current = escapeFirst
  })

  const exit = useCallback(() => {
    // Whatever request is still out stops counting once the mode goes off
    // (`switchMode(null)` clears `wanted`); its grant timer goes now.
    settleRequest()
    if (element && document.fullscreenElement === element) {
      // The `fullscreenchange` listener turns the mode off. If the browser
      // refuses (it has already left, say), turn it off here instead.
      document.exitFullscreen().catch(() => switchMode(null))
      return
    }
    switchMode(null)
  }, [element, settleRequest, switchMode])

  const enter = useCallback(() => {
    // `wanted` with no mode yet: a request is already out. A second one would
    // be a second promise that could flip the mode later.
    if (!element || mode !== null || wanted.current) return
    wanted.current = true
    const request = ++requestSeq.current
    // Only the live request, for a reader who still wants it, may open the overlay.
    const live = () => request === requestSeq.current && wanted.current
    scrollMarks.current = markScroll(element)
    if (!apiSupported() || element.tagName === 'DIALOG') {
      switchMode('overlay')
      return
    }
    try {
      // The mode flips to 'api' on `fullscreenchange`, not here: the request
      // can still be refused, and the promise is how we hear about it.
      const pending = element.requestFullscreen()
      settleRequest()
      grantTimer.current = setTimeout(() => {
        grantTimer.current = null
        // A grant would have cleared this timer (fullscreenchange).
        if (live()) switchMode('overlay')
      }, GRANT_TIMEOUT_MS)
      Promise.resolve(pending).catch(() => {
        if (!live()) return
        settleRequest()
        switchMode('overlay')
      })
    } catch {
      switchMode('overlay')
    }
  }, [element, mode, settleRequest, switchMode])

  // Escape: which dismissible things were showing BEFORE this key press
  // reached anyone (captured on the window), so the modal's Escape below
  // knows whether the key was a tooltip's rather than full screen's.
  const escapeWasForInside = useRef(false)
  const notDismissed = useRef(new WeakSet<Element>())
  useEffect(() => {
    if (mode === null) return
    const timers = new Set<number>()
    const onKeyDownCapture = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      const showing = (escapeFirstFn.current?.() ?? []).filter((el) => !notDismissed.current.has(el))
      escapeWasForInside.current = showing.length > 0
      if (showing.length === 0) return
      // Still there once the key had its effect → this Escape did not dismiss
      // it, so the next one must not wait on it again.
      const timer = window.setTimeout(() => {
        timers.delete(timer)
        const still = new Set(escapeFirstFn.current?.() ?? [])
        for (const el of showing) if (still.has(el)) notDismissed.current.add(el)
      }, ESCAPE_SETTLE_MS)
      timers.add(timer)
    }
    window.addEventListener('keydown', onKeyDownCapture, true)
    return () => {
      window.removeEventListener('keydown', onKeyDownCapture, true)
      for (const timer of timers) window.clearTimeout(timer)
      escapeWasForInside.current = false
    }
  }, [mode])
  const onEscape = useCallback(() => {
    const forInside = escapeWasForInside.current
    escapeWasForInside.current = false
    if (!forInside) exit()
  }, [exit])

  // The page behind is inert while full screen (see the header). Declared
  // before the focus trap, so its cleanup runs first on the way out and the
  // page is live again before focus is handed back into it.
  useEffect(() => {
    if (mode === null || !element) return
    return inertPageAround(element)
  }, [mode, element])

  const trapRef = useModalFocus<T>({ open: mode !== null, onClose: onEscape })
  const ref = useCallback(
    (node: T | null) => {
      trapRef.current = node
      setElement(node)
    },
    [trapRef],
  )

  // The API's own state is the truth: Escape, F11-style exits and a second
  // element taking full screen all arrive here, not through `exit`.
  useEffect(() => {
    if (!element) return
    function onChange() {
      if (document.fullscreenElement === element) {
        settleRequest()
        // Granted after the reader had already left: the browser is full
        // screen on a chart nobody asked to see so. Leave again, rather than
        // adopt it.
        if (!wanted.current) {
          document.exitFullscreen().catch(() => {})
          return
        }
        switchMode('api')
      } else if (modeNow.current === 'api') {
        // Left under the API: the overlay (or a pending request) is untouched.
        switchMode(null)
      }
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => {
      document.removeEventListener('fullscreenchange', onChange)
      settleRequest()
    }
  }, [element, settleRequest, switchMode])

  // The overlay is `fixed` over the page: the page underneath must not scroll
  // behind it (a wheel over the overlay would otherwise move the page).
  useEffect(() => {
    if (mode !== 'overlay') return
    const html = document.documentElement
    const previous = html.style.overflow
    html.style.overflow = 'hidden'
    return () => {
      html.style.overflow = previous
    }
  }, [mode])

  // Every change of mode: re-layout, and on the way OUT, put the page back.
  // This runs after `useModalFocus` has handed focus back to the opener (all
  // effect cleanups run before any effect body), so it has the last word on
  // where focus and scroll end up.
  const wasOn = useRef(false)
  useEffect(() => {
    const on = mode !== null
    if (on === wasOn.current) return
    wasOn.current = on
    window.dispatchEvent(new Event('resize'))
    if (on) return
    const target = returnFocusFn.current?.()
    if (target && target.isConnected) target.focus({ preventScroll: true })
    const marks = scrollMarks.current
    scrollMarks.current = null
    if (!marks) return
    restoreScroll(marks)
    // Leaving the Fullscreen API re-lays the page out after this event; the
    // browser can move the scroll once more while doing so.
    const frame = requestAnimationFrame(() => restoreScroll(marks))
    return () => cancelAnimationFrame(frame)
  }, [mode])

  // Unmounted while full screen (a route change): do not leave the document
  // in full screen on an element that no longer exists.
  useEffect(() => {
    if (!element) return
    return () => {
      // A request still out has no one to answer to.
      wanted.current = false
      requestSeq.current += 1
      if (document.fullscreenElement === element) document.exitFullscreen().catch(() => {})
    }
  }, [element])

  return { ref, element, mode, isFullscreen: mode !== null, supported, enter, exit }
}
