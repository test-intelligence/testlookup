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

type RenderState = 'pending' | 'drawn' | 'failed'

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export default function MermaidDiagram({ source }: { source: string }) {
  const theme = useThemeStore((s) => s.theme)
  const reactId = useId()
  const hostRef = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<RenderState>('pending')

  useEffect(() => {
    let cancelled = false

    async function draw() {
      try {
        const mermaid = (await import('mermaid')).default

        const text = cssVar('--color-text', '#e6e6e6')
        const muted = cssVar('--color-text-muted', '#9aa0a6')
        const accent = cssVar('--color-accent', '#3b82f6')
        const surface = cssVar('--color-bg-secondary', '#181d25')
        const border = cssVar('--color-border', '#2a2f38')
        // A CONCRETE stack, never 'inherit'. Mermaid sizes every node box by
        // measuring its label in a detached element; under 'inherit' that
        // element resolves the font differently from the finished SVG, so the
        // boxes come out narrower than the text and every label is clipped
        // ("Backend AP", "MCP serve"). The app uses one sans stack across all
        // six themes, so naming it here costs nothing and keeps measurement
        // and rendering in the same font.
        const font = cssVar('--font-sans', 'system-ui, sans-serif')

        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          // 'base' is the only theme that honours themeVariables fully.
          theme: 'base',
          fontFamily: font,
          themeVariables: {
            background: 'transparent',
            fontFamily: font,
            primaryColor: surface,
            primaryTextColor: text,
            primaryBorderColor: accent,
            secondaryColor: surface,
            tertiaryColor: surface,
            lineColor: muted,
            textColor: text,
            mainBkg: surface,
            nodeBorder: accent,
            clusterBkg: 'transparent',
            clusterBorder: border,
            edgeLabelBackground: surface,
          },
        })

        // mermaid needs a DOM id that is a valid CSS selector.
        const id = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, '')}`
        const { svg } = await mermaid.render(id, source)
        if (cancelled || !hostRef.current) return
        hostRef.current.innerHTML = svg
        setState('drawn')
      } catch {
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
