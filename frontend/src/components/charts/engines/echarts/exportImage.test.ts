import { afterEach, describe, expect, it, vi } from 'vitest'

const getInstanceByDom = vi.fn()
vi.mock('./core', () => ({ echarts: { getInstanceByDom: (el: HTMLElement) => getInstanceByDom(el) } }))

import { CANVAS_ENGINE_SELECTOR } from '../../ChartExportMenu'
import { echartsImage, ECHARTS_INSTANCE_ATTRIBUTE, findEChartsContainer } from './exportImage'

function body(withEngine: boolean) {
  const root = document.createElement('div')
  if (withEngine) {
    const container = document.createElement('div')
    container.setAttribute(ECHARTS_INSTANCE_ATTRIBUTE, 'ec_1')
    root.appendChild(container)
  }
  return root
}

afterEach(() => getInstanceByDom.mockReset())

describe('echartsImage (VIZ-606)', () => {
  it('asks the engine for a PNG at the export pixel ratio, sized as drawn on the page', () => {
    const getDataURL = vi.fn(() => 'data:image/png;base64,QQ==')
    getInstanceByDom.mockReturnValue({ getDataURL, getWidth: () => 640, getHeight: () => 320, isDisposed: () => false })
    const root = body(true)
    expect(echartsImage(root, 2, 'transparent')).toEqual({ dataUrl: 'data:image/png;base64,QQ==', width: 640, height: 320 })
    expect(getDataURL).toHaveBeenCalledWith({ type: 'png', pixelRatio: 2, backgroundColor: 'transparent' })
    expect(getInstanceByDom).toHaveBeenCalledWith(findEChartsContainer(root))
  })

  it('null with no container, no instance, or a disposed one', () => {
    expect(echartsImage(body(false), 2, 'transparent')).toBeNull()
    getInstanceByDom.mockReturnValue(undefined)
    expect(echartsImage(body(true), 2, 'transparent')).toBeNull()
    getInstanceByDom.mockReturnValue({ isDisposed: () => true })
    expect(echartsImage(body(true), 2, 'transparent')).toBeNull()
  })

  it('the menu recognises the same container without importing the engine', () => {
    const root = body(true)
    expect(root.querySelector(CANVAS_ENGINE_SELECTOR)).toBe(findEChartsContainer(root))
  })
})
