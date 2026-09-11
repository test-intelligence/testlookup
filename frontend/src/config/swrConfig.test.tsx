/**
 * M19: a hidden tab must stop polling, and a tab that comes back must show
 * fresh data at once instead of waiting out one more interval.
 *
 * Driven through real SWR under the app's root config: the only thing faked
 * is the clock and `document.visibilityState`. The assertion is the number of
 * requests the fetcher received, which is what the backend sees.
 */
import { act, render } from '@testing-library/react'
import useSWR, { SWRConfig, type SWRConfiguration } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { APP_SWR_CONFIG } from './swrConfig'

const INTERVAL = 1_000

let visibility: DocumentVisibilityState = 'visible'

function setVisibility(next: DocumentVisibilityState) {
  visibility = next
  document.dispatchEvent(new Event('visibilitychange'))
}

function Poller({ fetcher, options }: { fetcher: () => Promise<string>; options: SWRConfiguration }) {
  const { data } = useSWR('poll-key', fetcher, options)
  return <span>{data ?? 'none'}</span>
}

function renderPoller(options: SWRConfiguration) {
  const fetcher = vi.fn(async () => 'ok')
  render(
    <SWRConfig value={{ ...APP_SWR_CONFIG, provider: () => new Map() }}>
      <Poller fetcher={fetcher} options={options} />
    </SWRConfig>,
  )
  return fetcher
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => visibility === 'hidden' })
})

afterEach(() => {
  vi.useRealTimers()
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => false })
})

describe('polling in a background tab', () => {
  it('stops while hidden, refetches at once on return, then keeps polling', async () => {
    // dedupingInterval 0: SWR's default 2 s dedupe would swallow a 1 s tick.
    const fetcher = renderPoller({ refreshInterval: INTERVAL, revalidateOnFocus: false, dedupingInterval: 0 })
    await advance(50)
    expect(fetcher).toHaveBeenCalledTimes(1)

    await advance(INTERVAL)
    expect(fetcher).toHaveBeenCalledTimes(2)

    setVisibility('hidden')
    await advance(INTERVAL * 20)
    expect(fetcher, 'a hidden tab must not hit the backend').toHaveBeenCalledTimes(2)

    setVisibility('visible')
    await advance(50) // far less than one interval
    expect(fetcher, 'returning must refresh immediately, not after the next tick').toHaveBeenCalledTimes(3)

    await advance(INTERVAL * 2 + 50)
    expect(fetcher, 'polling must resume after the tab returns').toHaveBeenCalledTimes(5)
  })

  it('does not wake a hook that does not poll', async () => {
    const fetcher = renderPoller({ refreshInterval: 0, revalidateOnFocus: false })
    await advance(50)
    expect(fetcher).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await advance(INTERVAL * 10)
    setVisibility('visible')
    await advance(INTERVAL * 10)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('a hook that keeps focus revalidation refetches once on return, not twice', async () => {
    const fetcher = renderPoller({ refreshInterval: INTERVAL * 60, dedupingInterval: 0 })
    await advance(50)
    expect(fetcher).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await advance(10_000) // past SWR's 5 s focus throttle
    setVisibility('visible')
    await advance(50)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('leaves a focus-revalidating hook to SWR, throttle included', async () => {
    // A quick alt-tab inside SWR's 5 s focus throttle: SWR deliberately does
    // not refetch, and the resume path must not override that decision.
    const fetcher = renderPoller({ refreshInterval: INTERVAL * 60, dedupingInterval: 0 })
    await advance(50)
    setVisibility('hidden')
    await advance(10_000)
    setVisibility('visible')
    await advance(50)
    expect(fetcher).toHaveBeenCalledTimes(2)

    setVisibility('hidden')
    await advance(500)
    setVisibility('visible')
    await advance(50)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
