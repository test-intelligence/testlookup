/**
 * VIZ-209: at most four chart requests in flight at once, from one page.
 *
 * A report page mounts up to 8 chart frames. An API pod has 12 database
 * connections (pool 2 + overflow 1, x 4 workers), so an uncapped page is a
 * self-inflicted connection storm — and the slowest of the 8 decides when any
 * of them paints. The queue must also be honest: first asked, first served,
 * and a request superseded before it ever started must not hold a slot.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('./api', () => ({ api: { get } }))

const { chartGet, MAX_CONCURRENT_CHART_REQUESTS, __resetChartConcurrency } = await import('./chartApi')

/** One pending `api.get`, with the handles to settle it later. */
interface Pending {
  url: string
  resolve: (data: unknown) => void
  reject: (error: unknown) => void
}

let pending: Pending[] = []

/** Let every already-scheduled microtask run. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function canceled(): Error {
  const error = new Error('canceled')
  error.name = 'CanceledError'
  return error
}

beforeEach(() => {
  pending = []
  get.mockReset()
  get.mockImplementation((url: string, config: { signal?: AbortSignal }) => {
    return new Promise((resolve, reject) => {
      const entry: Pending = {
        url,
        resolve: (data) => resolve({ data, headers: {} }),
        reject,
      }
      pending.push(entry)
      // Axios rejects an in-flight request when its signal aborts.
      config.signal?.addEventListener('abort', () => reject(canceled()))
    })
  })
})

afterEach(() => __resetChartConcurrency())

/** Fire n chart requests, swallowing rejections so no test sees an unhandled one. */
function fire(n: number, signals: (AbortSignal | undefined)[] = []): Promise<unknown>[] {
  return Array.from({ length: n }, (_, index) =>
    chartGet(`/api/v1/analytics/chart-data?frame=${index}`, { signal: signals[index] }).catch(
      (error: unknown) => error,
    ),
  )
}

const started = () => pending.map((entry) => entry.url)

describe('chart request concurrency (VIZ-209)', () => {
  it('caps a page of 8 frames at 4 requests in flight', async () => {
    fire(8)
    await flush()

    expect(MAX_CONCURRENT_CHART_REQUESTS).toBe(4)
    expect(get).toHaveBeenCalledTimes(4)
    expect(started()).toEqual([
      '/api/v1/analytics/chart-data?frame=0',
      '/api/v1/analytics/chart-data?frame=1',
      '/api/v1/analytics/chart-data?frame=2',
      '/api/v1/analytics/chart-data?frame=3',
    ])
  })

  it('admits the queue in the order it was asked for, one per completion', async () => {
    fire(8)
    await flush()

    pending[0].resolve({ ok: 0 })
    await flush()
    expect(started()[4]).toBe('/api/v1/analytics/chart-data?frame=4')
    expect(get).toHaveBeenCalledTimes(5)

    pending[1].resolve({ ok: 1 })
    pending[2].resolve({ ok: 2 })
    await flush()
    expect(started().slice(5)).toEqual([
      '/api/v1/analytics/chart-data?frame=5',
      '/api/v1/analytics/chart-data?frame=6',
    ])
    expect(get).toHaveBeenCalledTimes(7)
  })

  it('never exceeds the cap while the queue drains', async () => {
    fire(8)
    await flush()
    for (let index = 0; index < 4; index += 1) {
      const inFlight = pending.length - index
      expect(inFlight).toBeLessThanOrEqual(MAX_CONCURRENT_CHART_REQUESTS)
      pending[index].resolve({ ok: index })
      await flush()
    }
    expect(get).toHaveBeenCalledTimes(8)
  })

  it('a superseded request waiting in the queue does not hold a slot', async () => {
    const controllers = Array.from({ length: 8 }, () => new AbortController())
    const results = fire(8, controllers.map((c) => c.signal))
    await flush()
    expect(get).toHaveBeenCalledTimes(4)

    // Frame 4 is queued, not running. A newer filter supersedes it.
    controllers[4].abort()
    await flush()
    expect(get).toHaveBeenCalledTimes(4)
    expect((await results[4]) as Error).toMatchObject({ name: 'CanceledError' })

    // Its slot was never taken, so the next completion goes to frame 5.
    pending[0].resolve({ ok: 0 })
    await flush()
    expect(started()[4]).toBe('/api/v1/analytics/chart-data?frame=5')
  })

  it('an aborted in-flight request frees its slot', async () => {
    const controllers = Array.from({ length: 8 }, () => new AbortController())
    fire(8, controllers.map((c) => c.signal))
    await flush()

    controllers[0].abort()
    await flush()

    expect(get).toHaveBeenCalledTimes(5)
    expect(started()[4]).toBe('/api/v1/analytics/chart-data?frame=4')
  })

  it('a failed request frees its slot too', async () => {
    fire(8)
    await flush()
    pending[0].reject(new Error('500'))
    await flush()
    expect(get).toHaveBeenCalledTimes(5)
  })

  it('a request whose signal is already aborted never reaches the network', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(chartGet('/x', { signal: controller.signal })).rejects.toMatchObject({
      name: 'CanceledError',
    })
    expect(get).not.toHaveBeenCalled()
  })

  it('still passes suppressToast, the params and the signal through the queue', async () => {
    const controller = new AbortController()
    const inFlight = chartGet('/api/v1/analytics/chart-data', {
      params: { days: 7 },
      signal: controller.signal,
    })
    await flush()
    expect(get).toHaveBeenCalledWith('/api/v1/analytics/chart-data', {
      params: { days: 7 },
      signal: controller.signal,
      suppressToast: true,
    })
    pending[0].resolve({ ok: 1 })
    await expect(inFlight).resolves.toEqual({ data: { ok: 1 }, requestId: null })
  })
})
