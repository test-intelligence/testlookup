/**
 * Renders a ```mermaid block as a picture.
 *
 * Three things this deliberately does:
 *
 * 1. **Loads mermaid lazily.** It is a large library and only the docs pages
 *    use it, so it is a dynamic import — a separate chunk nobody else pays for.
 *    It is bundled, never fetched from a CDN, because this product ships to
 *    air-gapped deployments.
 *
 * 2. **Takes its colours from the active theme.** The app has six themes and
 *    the CSS custom properties are the single source for them, so the diagram
 *    reads the same variables every other component does rather than carrying
 *    a second palette that would drift.
 *
 * 3. **Falls back to the source, visibly.** If mermaid fails to load or the
 *    diagram fails to parse, the reader gets the diagram source and a plain
 *    statement that it could not be drawn. A blank space where a diagram should
 *    be is the failure mode worth avoiding — the reader cannot tell whether the
 *    documentation is broken or the page simply has nothing there.
 *
 * The written "In words:" description under every diagram in the content stays
 * regardless. A picture is an aid, not the explanation, and a screen reader
 * never gets one.
 */
import { useEffect, useId, useRef, useState } from 'react'

import { useThemeStore } from '@/store/themeStore'
import { mermaidConfig, readDiagramPalette } from './mermaidConfig'

type RenderState = 'pending' | 'drawn' | 'failed'

/**
 * How long to wait for mermaid before showing the source instead.
 *
 * Without this, 'pending' is terminal: a render that never settles leaves an
 * empty bordered box on the page for good, which is precisely the outcome the
 * fallback exists to prevent. Generous, because a slow machine drawing a large
 * diagram is not a failure.
 */
export const RENDER_TIMEOUT_MS = 15_000

export default function MermaidDiagram({ source }: { source: string }) {
  const theme = useThemeStore((s) => s.theme)
  const reactId = useId()
  const hostRef = useRef<HTMLDivElement>(null)
  const attemptRef = useRef(0)
  const [state, setState] = useState<RenderState>('pending')

  useEffect(() => {
    let cancelled = false

    async function draw() {
      // Declared out here so the catch below can clean up mermaid's scratch
      // element by the same id.
      //
      // Unique per ATTEMPT, not per component: two renders of this instance can
      // overlap — a theme toggle or a quick navigation re-runs the effect while
      // the previous render is still in flight — and giving both the same id
      // makes them collide inside mermaid, so neither resolves, the component
      // stays 'pending' forever and the reader gets the blank box this
      // component exists to avoid. Reproduced 6 of 13 diagrams stuck.
      const id = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, '')}-${++attemptRef.current}`

      // Removes mermaid's scratch node for THIS attempt, whenever the render
      // ends up finishing.
      //
      // Scoped to a DIRECT CHILD of <body>, which is what a stray is. This
      // matters because mermaid names the SVG it returns after the id it was
      // given (`idSelector = "#" + id` in its source, and `render` hands back
      // that element's serialised markup) — so once the result is written into
      // this component, `getElementById(id)` finds THE DIAGRAM.
      //
      // Honestly: the guard is defence-in-depth, not load-bearing today.
      // Attaching the cleanup to the render promise means it always runs
      // before the innerHTML write, so removing the scope alone changes
      // nothing — mutation testing confirms that mutation survives. What is
      // killed is the PAIR: reorder the cleanup past the write AND drop the
      // scope, and the component deletes the picture it just drew. The guard
      // is what makes the first of those survivable on its own.
      const removeStray = () => {
        for (const el of [document.getElementById(`d${id}`), document.getElementById(id)]) {
          if (el && el.parentElement === document.body) el.remove()
        }
      }

      try {
        const mermaid = (await import('mermaid')).default

        mermaid.initialize(mermaidConfig(readDiagramPalette()))

        let timer: ReturnType<typeof setTimeout> | undefined
        // Held in a variable so the cleanup can be attached to the RENDER,
        // not just to the race.
        //
        // `Promise.race` does not cancel the loser. When the timeout wins, the
        // catch below cleans up while `mermaid.render` is still running — so
        // anything it appends after that moment is stranded under <body> with
        // nothing left to remove it, and in an SPA the leftover survives every
        // navigation. That is the original report this fallback was written
        // for ("Syntax error in text" on a page with no diagrams of its own),
        // reappearing through the one path the cleanup could not reach.
        //
        // Attaching here means the scratch node is removed however late the
        // render settles, and on both outcomes.
        const rendering = mermaid.render(id, source)
        rendering.then(removeStray, removeStray)

        const { svg } = await Promise.race([
          rendering,
          new Promise<never>((_, reject) => {
            timer = setTimeout(
              () => reject(new Error('mermaid did not settle')),
              RENDER_TIMEOUT_MS,
            )
          }),
        ]).finally(() => clearTimeout(timer))
        if (cancelled || !hostRef.current) return
        hostRef.current.innerHTML = svg
        setState('drawn')
      } catch {
        // Belt as well as braces. `suppressErrorRendering` makes mermaid clean
        // up after itself, but the cleanup only runs on the paths mermaid
        // knows about — if it ever fails between creating its scratch element
        // and reaching that code, the leftover would be stranded under <body>
        // and visible on every page. Removing it by id costs nothing and does
        // not depend on which branch inside mermaid failed.
        //
        // Still needed alongside the late cleanup above: this catch also fires
        // for a failure BEFORE `rendering` exists (the dynamic import, or
        // `initialize`), where there is no promise to attach to.
        removeStray()

        if (cancelled) return
        // Clear any diagram from a previous render, so the fallback is not
        // shown underneath a stale picture of something else.
        if (hostRef.current) hostRef.current.innerHTML = ''
        setState('failed')
      }
    }

    void draw()
    return () => {
      cancelled = true
    }
    // Re-draw on theme change so the diagram never keeps a stale palette.
  }, [source, theme, reactId])

  return (
    <figure className="mb-3">
      {/*
        Always mounted, hidden rather than removed. This div IS the render
        target: if it is unmounted while the fallback shows, the ref is null
        and a redraw — a theme change, a new diagram — has nowhere to draw,
        so a single parse failure would be permanent.
      */}
      <div
        ref={hostRef}
        role="img"
        aria-label="Diagram; the same flow is described in words below."
        className="overflow-x-auto rounded-lg border p-3"
        style={{
          borderColor: 'var(--color-border)',
          background: 'var(--color-bg-secondary)',
          display: state === 'failed' ? 'none' : undefined,
        }}
      />

      {state === 'failed' && (
        <>
          <figcaption className="text-[12px] text-[var(--color-text-muted)] mb-1">
            This diagram could not be drawn. Its source is below, and the same flow is
            described in words underneath.
          </figcaption>
          <pre
            className="overflow-x-auto rounded-lg border p-3 text-[12px] leading-relaxed"
            style={{
              borderColor: 'var(--color-border)',
              background: 'var(--color-bg-secondary)',
              color: 'var(--color-text-muted)',
            }}
          >
            <code>{source}</code>
          </pre>
        </>
      )}
    </figure>
  )
}
