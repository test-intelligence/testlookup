/**
 * The opt-in 3D view of the test scatter (VIZ-508): the same tests, the same
 * quadrant colours and shapes, with executions as depth, rotated by dragging.
 * Drawn OVER the 2D scatter, which stays mounted underneath (the host makes it
 * `inert`): export, print and the selection tools keep using the 2D chart, and
 * "Back to 2D" is instant.
 *
 * Loading, in order:
 *   1. no WebGL 2 -> `onUnavailable('no-webgl')` and the engine chunk is NEVER
 *      fetched (`hasWebGL2` is asked first);
 *   2. the chunk (`loadScatter3D`): a missing one is a stale build ("A new
 *      version is available", Reload), any other failure is the kit's draw
 *      error with Try again;
 *   3. `mountScatter3D`: a throw there is the browser refusing a context, so
 *      it is "no WebGL" too.
 * Later, the browser taking the context back (`webglcontextlost`) is
 * `onUnavailable('context-lost')`. The host goes back to 2D and says why.
 *
 * The engine renders on demand; this component resizes it once per animation
 * frame (the `useEChart` rule), re-colours it on a theme switch, and disposes
 * it on unmount — a disposed view gives its WebGL context back.
 *
 * Hostile names: the tooltip is `ChartTooltipBody`, React text; the engine
 * draws no test name at all.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { PointsChart } from '@/lib/viz/contracts'
import { useChartTokens } from './tokens'
import { ChartTooltipBody, TIP_BOX_STYLE } from './ChartTooltipBody'
import { CHART_DRAW_ERROR } from './ChartErrorBoundary'
import { STALE_BUILD_ACTION, STALE_BUILD_MESSAGE, isStaleBuildError } from './engines/lazyChartEngine'
import { loadScatter3D } from './engines/three/load'
import { quadrantColor } from './engines/echarts/scatterOption'
import { QuadrantKey } from './TestScatter'
import { hasWebGL2 } from './webglSupport'
import { availableIntents, pointerIntent, type MarkActivationProps } from './marks'
import { scatterMark, scatterTooltipContent, type Quadrant } from './testScatter.model'
import {
  EXPORT_NOTE,
  RESET_VIEW_LABEL,
  ROTATE_HINT,
  scatter3DColors,
  scatter3DDescription,
  scatter3DLayout,
  type Scatter3DHandle,
  type Scatter3DUnavailable,
} from './scatter3d.model'

export type Scatter3DStatus = 'loading' | 'ready' | 'stale-build' | 'error'

export interface Scatter3DViewProps extends MarkActivationProps {
  data: PointsChart
  /** The plot's height, px, when nothing around it sets one (the host's overlay does). */
  height: number
  /** The view cannot run (no WebGL 2) or stopped (context lost): the host goes back to 2D. */
  onUnavailable: (reason: Scatter3DUnavailable) => void
}

const BUTTON =
  'min-h-6 rounded border border-[var(--color-border-light)] px-3 py-1 text-xs text-[var(--color-text)] hover:bg-[var(--color-bg-hover)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]'

/** A press that moved less than this (CSS px) is a click on a point, not a rotation. */
const CLICK_SLOP = 4
/** The tooltip's distance from the pointer, px. */
const TIP_OFFSET = 14

interface Hover {
  index: number
  x: number
  y: number
  /** Past the plot's middle: the tip opens to the left of the pointer, so it stays on the plot. */
  flip: boolean
}

export default function Scatter3DView({ data, height, onUnavailable, onMarkActivate, markIntents }: Scatter3DViewProps) {
  const tokens = useChartTokens()
  const layout = useMemo(() => scatter3DLayout(data), [data])
  const colors = useMemo(() => scatter3DColors(tokens), [tokens])
  const [status, setStatus] = useState<Scatter3DStatus>('loading')
  const [attempt, setAttempt] = useState(0)
  const [hover, setHover] = useState<Hover | null>(null)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const handleRef = useRef<Scatter3DHandle | null>(null)

  // What the mount reads when the chunk arrives: the latest, not the first render's.
  const latest = useRef({ layout, colors, onUnavailable })
  useEffect(() => {
    latest.current = { layout, colors, onUnavailable }
  })

  // Mount: probe, load, mount, observe size. Re-runs on Try again.
  useEffect(() => {
    if (!hasWebGL2()) {
      latest.current.onUnavailable('no-webgl')
      return
    }
    let cancelled = false
    let handle: Scatter3DHandle | null = null
    let observer: ResizeObserver | null = null
    let frame = 0
    loadScatter3D()
      .then((engine) => {
        const host = hostRef.current
        if (cancelled || !host) return
        try {
          handle = engine.mountScatter3D(host, latest.current.layout, latest.current.colors, {
            onContextLost: () => {
              if (!cancelled) latest.current.onUnavailable('context-lost')
            },
            // Written straight to the DOM: a drag reports every frame, and nothing React draws depends on it.
            onCameraChange: (degrees) => rootRef.current?.setAttribute('data-scatter-3d-azimuth', String(degrees)),
          })
        } catch {
          latest.current.onUnavailable('no-webgl')
          return
        }
        handleRef.current = handle
        const mounted = handle
        observer = new ResizeObserver(() => {
          if (frame) return
          frame = requestAnimationFrame(() => {
            frame = 0
            mounted.resize()
          })
        })
        observer.observe(host)
        setStatus('ready')
      })
      .catch((error: unknown) => {
        if (!cancelled) setStatus(isStaleBuildError(error) ? 'stale-build' : 'error')
      })
    return () => {
      cancelled = true
      observer?.disconnect()
      if (frame) cancelAnimationFrame(frame)
      handle?.dispose()
      handleRef.current = null
    }
  }, [attempt])

  // New data and a theme switch change the drawn view in place.
  useEffect(() => {
    handleRef.current?.update(layout)
  }, [layout])
  useEffect(() => {
    handleRef.current?.setColors(colors)
  }, [colors])

  const retry = useCallback(() => {
    setStatus('loading')
    setAttempt((n) => n + 1)
  }, [])

  // ── Pointer: hover picks once per animation frame; a press that did not move is a click ──
  const pointerAt = useRef<{ x: number; y: number } | null>(null)
  const pickFrame = useRef(0)
  const downAt = useRef<{ x: number; y: number } | null>(null)
  useEffect(
    () => () => {
      if (pickFrame.current) cancelAnimationFrame(pickFrame.current)
    },
    [],
  )

  const local = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    // A drag is a rotation: no tooltip follows it.
    if (event.buttons > 0) {
      setHover(null)
      return
    }
    pointerAt.current = local(event)
    if (pickFrame.current) return
    pickFrame.current = requestAnimationFrame(() => {
      pickFrame.current = 0
      const at = pointerAt.current
      const index = at ? (handleRef.current?.pick(at.x, at.y) ?? null) : null
      const width = hostRef.current?.clientWidth ?? 0
      setHover(index === null || !at ? null : { index, x: at.x, y: at.y, flip: at.x > width / 2 })
    })
  }, [])

  const onPointerLeave = useCallback(() => {
    pointerAt.current = null
    setHover(null)
  }, [])

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    downAt.current = { x: event.clientX, y: event.clientY }
  }, [])

  const onPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      const start = downAt.current
      downAt.current = null
      if (!start || !onMarkActivate) return
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > CLICK_SLOP) return
      const at = local(event)
      const index = handleRef.current?.pick(at.x, at.y) ?? null
      const mark = index === null ? null : scatterMark(data, index)
      if (!mark) return
      const intent = pointerIntent(availableIntents(mark, { onMarkActivate, markIntents }), {
        shiftKey: event.shiftKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        pointerType: event.pointerType,
      })
      if (intent) onMarkActivate(mark, intent)
    },
    [data, onMarkActivate, markIntents],
  )

  const resetView = useCallback(() => handleRef.current?.resetView(), [])
  const colorOf = useCallback((q: Quadrant) => quadrantColor(q, tokens), [tokens])
  const tip = hover ? scatterTooltipContent(data, hover.index) : null
  const failed = status === 'stale-build' || status === 'error'

  return (
    <div
      ref={rootRef}
      data-scatter-3d=""
      data-chart-engine="three"
      data-chart-status={status}
      style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%', background: 'var(--color-bg-card)' }}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <button type="button" data-scatter-3d-reset="" onClick={resetView} disabled={status !== 'ready'} className={BUTTON}>
          {RESET_VIEW_LABEL}
        </button>
        <span data-scatter-3d-note="" className="text-xs text-[var(--color-text-secondary)]">
          {`${ROTATE_HINT} ${EXPORT_NOTE}`}
        </span>
      </div>
      <div style={{ position: 'relative', flex: '1 1 auto', height, minHeight: 0 }}>
        {/* The engine's own: its canvas and label layer are appended here, and React renders nothing inside. */}
        <div
          ref={hostRef}
          role="img"
          aria-label={scatter3DDescription(data)}
          data-scatter-3d-plot=""
          onPointerMove={onPointerMove}
          onPointerLeave={onPointerLeave}
          onPointerDown={onPointerDown}
          onPointerUp={onPointerUp}
          style={{ position: 'absolute', inset: 0, cursor: 'grab', visibility: failed ? 'hidden' : undefined }}
        />
        {tip && hover ? (
          <ChartTooltipBody
            content={tip}
            style={{
              ...TIP_BOX_STYLE,
              position: 'absolute',
              top: hover.y + TIP_OFFSET,
              left: hover.x + TIP_OFFSET,
              transform: hover.flip ? `translateX(calc(-100% - ${2 * TIP_OFFSET}px))` : undefined,
              pointerEvents: 'none',
              zIndex: 1,
            }}
          />
        ) : null}
        {failed ? (
          // Static text: the frame, not a live region, owns how a state change is announced.
          <div
            data-chart-draw-error={status}
            style={{ position: 'absolute', inset: 0 }}
            className="flex flex-col items-center justify-center gap-2 text-sm text-[var(--color-text-secondary)]"
          >
            {status === 'stale-build' ? (
              <>
                <span>{STALE_BUILD_MESSAGE}</span>
                <button type="button" onClick={() => window.location.reload()} className={BUTTON}>
                  {STALE_BUILD_ACTION}
                </button>
              </>
            ) : (
              <>
                <span>{CHART_DRAW_ERROR}</span>
                <button type="button" onClick={retry} className={BUTTON}>
                  Try again
                </button>
              </>
            )}
          </div>
        ) : null}
      </div>
      <QuadrantKey data={data} colorOf={colorOf} />
    </div>
  )
}
