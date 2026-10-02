/**
 * K2 (Wave 2.6): mount a report section only once it is near the reader.
 *
 * Until then it is an empty box of the section's own height — so nothing
 * below it jumps when the section arrives — hidden from assistive technology
 * and without a heading (a heading with nothing under it would be a false
 * entry in a screen reader's outline). Once near, the box is REPLACED by the
 * section, which then stays mounted for good (`useNearViewport` latches): the
 * section's requests go out once, when the reader is about to need them, and
 * never again because of scrolling.
 *
 * Nothing inside the children runs before that — no hook, no request, and,
 * when the children are a `lazy()` component, no chunk download.
 */
import { useRef, type ReactNode } from 'react'
import { useNearViewport } from './useNearViewport'

export interface LazySectionProps {
  /** The section's height when drawn, px: the placeholder holds exactly this. */
  minHeight: number
  /** Which section this is (`data-lazy-section`), for specs; never shown. */
  label: string
  /** Mount this far ahead of the scroller's visible box, px. */
  marginPx?: number
  children: ReactNode
}

export default function LazySection({ minHeight, label, marginPx, children }: LazySectionProps) {
  const ref = useRef<HTMLDivElement>(null)
  const near = useNearViewport(ref, { marginPx })
  if (near) return <>{children}</>
  return <div ref={ref} data-lazy-section={label} aria-hidden="true" style={{ minHeight }} />
}
