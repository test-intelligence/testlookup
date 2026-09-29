/**
 * VIZ-104 K0 — `readyState`: the drawn state a page hands a kit frame when the
 * page already owns its loading, empty and error branches.
 */
import { describe, expect, it } from 'vitest'
import { hasChartData, readyState } from './chartState'

describe('readyState', () => {
  it('is a drawn, settled state with no envelope meta', () => {
    const data = { rows: [1, 2] }
    const state = readyState(data)
    expect(state).toEqual({ status: 'ready', data, meta: null, revalidating: false })
    // The same object, not a copy: the frame and the page read one value.
    expect(state.data).toBe(data)
    expect(hasChartData(state)).toBe(true)
  })

  it('carries null data too (a page that passes its model to the frame separately)', () => {
    expect(readyState(null)).toEqual({ status: 'ready', data: null, meta: null, revalidating: false })
  })
})
