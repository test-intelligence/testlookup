/**
 * A chart token (a resolved CSS colour string, `useChartTokens()`) as a
 * three.js `Color` (VIZ-508). The ONE way a colour reaches the 3D engine:
 * never a numeric literal, so `npm run check:theme` keeps every chart colour
 * in `tokens.ts`.
 *
 * three parses only a few CSS syntaxes itself; a browser parses them all. So
 * the token is painted on a 1 x 1 2D canvas and the pixel read back: whatever
 * the theme writes (`#hex`, a named colour, a modern colour space) arrives as
 * sRGB bytes. Where there is no 2D canvas (jsdom) the string goes to three as
 * it is.
 */
import { Color, SRGBColorSpace } from 'three'

let probe: CanvasRenderingContext2D | null | undefined

function probeContext(): CanvasRenderingContext2D | null {
  if (probe !== undefined) return probe
  try {
    const canvas = document.createElement('canvas')
    canvas.width = 1
    canvas.height = 1
    probe = canvas.getContext('2d', { willReadFrequently: true })
  } catch {
    probe = null
  }
  return probe
}

/** Test seam: forget the probe canvas. */
export function resetColorProbe(): void {
  probe = undefined
}

export function tokenColor(token: string): Color {
  const ctx = probeContext()
  if (!ctx) return new Color(token)
  ctx.clearRect(0, 0, 1, 1)
  ctx.fillStyle = token
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return new Color().setRGB(r / 255, g / 255, b / 255, SRGBColorSpace)
}
