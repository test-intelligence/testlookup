/**
 * Mounts one ECharts instance for a chart type: loads the engine chunk lazily,
 * `init`s on the container, applies the option, resizes with a ResizeObserver
 * and DISPOSES on unmount (a leaked instance keeps its canvas, listeners and
 * animation frame alive).
 *
 * Every failure lands in `status` — a failed load, and an `init` / `setOption`
 * that throws (on mount or on a later option change) — never an unhandled
 * rejection with the chart stuck on `loading`. An option carrying a formatter
 * not built by `domTooltipFormatter` is refused the same way (ADR decision 4). `retry()` starts over: a fresh
 * load (a failed load is not cached by the registry) and a fresh instance.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { assertSafeChartOption } from '../tooltip'
import { isStaleBuildError } from './lazyChartEngine'
import { loadChartEngine, type ChartEngineType, type ChartInstance } from './registry'

export type EChartStatus = 'loading' | 'ready' | 'stale-build' | 'error'

export function useEChart(type: ChartEngineType, option: object) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const instanceRef = useRef<ChartInstance | null>(null)
  const optionRef = useRef(option)
  const [status, setStatus] = useState<EChartStatus>('loading')
  const [attempt, setAttempt] = useState(0)

  // A new chart type is a new engine: back to loading (adjusted during render,
  // so no frame reports the old type's `ready`).
  const [seenType, setSeenType] = useState(type)
  if (seenType !== type) {
    setSeenType(type)
    setStatus('loading')
  }

  // Mount: load, init, observe size. Re-runs when the chart TYPE changes or on retry.
  useEffect(() => {
    let cancelled = false
    let observer: ResizeObserver | null = null
    const fail = (error: unknown) => {
      if (!cancelled) setStatus(isStaleBuildError(error) ? 'stale-build' : 'error')
    }
    loadChartEngine(type)
      .then((engine) => {
        const el = containerRef.current
        if (cancelled || !el) return
        try {
          assertSafeChartOption(optionRef.current)
          const instance = engine.init(el, null, { renderer: 'canvas' })
          instanceRef.current = instance
          instance.setOption(optionRef.current, { notMerge: true })
          observer = new ResizeObserver(() => instance.resize())
          observer.observe(el)
          setStatus('ready')
        } catch (error) {
          instanceRef.current?.dispose()
          instanceRef.current = null
          fail(error)
        }
      })
      .catch(fail)
    return () => {
      cancelled = true
      observer?.disconnect()
      instanceRef.current?.dispose()
      instanceRef.current = null
    }
  }, [type, attempt])

  // Option changes (new data, theme switch) replace the option in place.
  useEffect(() => {
    optionRef.current = option
    const instance = instanceRef.current
    if (!instance) return
    try {
      assertSafeChartOption(option)
      instance.setOption(option, { notMerge: true })
    } catch {
      instance.dispose()
      instanceRef.current = null
      // Reported after this effect, not synchronously inside it (no cascading
      // render); a set after unmount is a no-op.
      void Promise.resolve().then(() => setStatus('error'))
    }
  }, [option])

  const retry = useCallback(() => {
    setStatus('loading')
    setAttempt((n) => n + 1)
  }, [])

  return { containerRef, instanceRef, status, retry }
}
