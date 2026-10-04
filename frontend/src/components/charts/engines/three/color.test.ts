/**
 * `tokenColor` (VIZ-508): a resolved token string becomes a three `Color` by
 * the browser's own parse (a 1 x 1 canvas, read back as sRGB bytes), and by
 * three's parse where there is no 2D canvas (jsdom).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Color, SRGBColorSpace } from 'three'
import { resetColorProbe, tokenColor } from './color'

beforeEach(() => resetColorProbe())
afterEach(() => vi.restoreAllMocks())

describe('tokenColor', () => {
  it('reads the pixel the browser painted, as sRGB', () => {
    const painted: string[] = []
    const ctx = {
      fillStyle: '',
      clearRect: vi.fn(),
      fillRect: vi.fn(function (this: { fillStyle: string }) {
        painted.push(this.fillStyle)
      }),
      getImageData: vi.fn(() => ({ data: new Uint8ClampedArray([255, 128, 0, 255]) })),
    }
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    const color = tokenColor('var-resolved-token')
    expect(painted).toEqual(['var-resolved-token'])
    expect(color.getHexString(SRGBColorSpace)).toBe(new Color().setRGB(1, 128 / 255, 0, SRGBColorSpace).getHexString(SRGBColorSpace))
    // One probe canvas, reused.
    tokenColor('another')
    expect(HTMLCanvasElement.prototype.getContext).toHaveBeenCalledTimes(1)
    expect(ctx.clearRect).toHaveBeenCalledTimes(2)
  })

  it('with no 2D canvas, three parses the string itself', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    expect(tokenColor('red').equals(new Color('red'))).toBe(true)
  })

  it('a canvas that throws is the same as none', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => {
      throw new Error('no canvas')
    })
    expect(tokenColor('blue').equals(new Color('blue'))).toBe(true)
  })
})
