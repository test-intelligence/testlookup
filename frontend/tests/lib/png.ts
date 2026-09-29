/**
 * A minimal PNG reader for the export specs (VIZ-606), on node's own `zlib`.
 *
 * The story's test is "decode the PNG and assert dimensions and a non-blank
 * footer region". No PNG library is a dependency of this repo, and one is not
 * worth adding for a reader this small: a canvas's `toBlob('image/png')` is
 * always 8-bit, non-interlaced RGBA (or RGB), so the whole format we meet is
 * the chunk list, IHDR, the concatenated IDAT stream and the five scanline
 * filters. Anything else is rejected loudly rather than misread.
 */
import { inflateSync } from 'node:zlib'

export interface DecodedPng {
  width: number
  height: number
  /** RGBA, 4 bytes per pixel, row-major — RGB input is widened with alpha 255. */
  pixels: Uint8Array
}

const SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]

/** The IHDR fields and the IDAT stream, without decoding the pixels. */
export function readPngHeader(bytes: Uint8Array): { width: number; height: number; bitDepth: number; colorType: number; interlace: number; idat: Uint8Array } {
  SIGNATURE.forEach((byte, i) => {
    if (bytes[i] !== byte) throw new Error('not a PNG: bad signature')
  })
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  let offset = 8
  let header: { width: number; height: number; bitDepth: number; colorType: number; interlace: number } | null = null
  const idat: Uint8Array[] = []
  while (offset < bytes.length) {
    const length = view.getUint32(offset)
    const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8))
    const data = bytes.subarray(offset + 8, offset + 8 + length)
    if (type === 'IHDR') {
      header = {
        width: view.getUint32(offset + 8),
        height: view.getUint32(offset + 12),
        bitDepth: data[8],
        colorType: data[9],
        interlace: data[12],
      }
    } else if (type === 'IDAT') {
      idat.push(data)
    } else if (type === 'IEND') {
      break
    }
    offset += 12 + length // length + type + data + CRC
  }
  if (!header) throw new Error('not a PNG: no IHDR chunk')
  const total = idat.reduce((sum, part) => sum + part.length, 0)
  const joined = new Uint8Array(total)
  let at = 0
  for (const part of idat) {
    joined.set(part, at)
    at += part.length
  }
  return { ...header, idat: joined }
}

function paeth(a: number, b: number, c: number): number {
  const p = a + b - c
  const pa = Math.abs(p - a)
  const pb = Math.abs(p - b)
  const pc = Math.abs(p - c)
  if (pa <= pb && pa <= pc) return a
  return pb <= pc ? b : c
}

/** Decode an 8-bit, non-interlaced RGBA or RGB PNG. */
export function decodePng(bytes: Uint8Array): DecodedPng {
  const { width, height, bitDepth, colorType, interlace, idat } = readPngHeader(bytes)
  if (bitDepth !== 8 || interlace !== 0 || (colorType !== 6 && colorType !== 2)) {
    throw new Error(`unsupported PNG: bit depth ${bitDepth}, colour type ${colorType}, interlace ${interlace}`)
  }
  const channels = colorType === 6 ? 4 : 3
  const stride = width * channels
  const raw = inflateSync(idat)
  if (raw.length !== (stride + 1) * height) throw new Error(`PNG data is ${raw.length} bytes, expected ${(stride + 1) * height}`)
  const out = new Uint8Array(stride * height)
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1))
    const row = y * stride
    for (let x = 0; x < stride; x++) {
      const left = x >= channels ? out[row + x - channels] : 0
      const up = y > 0 ? out[row - stride + x] : 0
      const upLeft = y > 0 && x >= channels ? out[row - stride + x - channels] : 0
      let value = line[x]
      if (filter === 1) value += left
      else if (filter === 2) value += up
      else if (filter === 3) value += (left + up) >> 1
      else if (filter === 4) value += paeth(left, up, upLeft)
      else if (filter !== 0) throw new Error(`bad PNG filter ${filter} on row ${y}`)
      out[row + x] = value & 0xff
    }
  }
  if (channels === 4) return { width, height, pixels: out }
  const rgba = new Uint8Array(width * height * 4)
  for (let i = 0, j = 0; i < out.length; i += 3, j += 4) {
    rgba[j] = out[i]
    rgba[j + 1] = out[i + 1]
    rgba[j + 2] = out[i + 2]
    rgba[j + 3] = 255
  }
  return { width, height, pixels: rgba }
}

/** The RGBA of one pixel. */
export function pixelAt(png: DecodedPng, x: number, y: number): [number, number, number, number] {
  const i = (y * png.width + x) * 4
  return [png.pixels[i], png.pixels[i + 1], png.pixels[i + 2], png.pixels[i + 3]]
}

/** How far a pixel is from a colour: the largest channel difference. */
export function distance(a: readonly number[], b: readonly number[]): number {
  return Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]), Math.abs(a[3] - b[3]))
}

/** The share of pixels in row `y` that differ from `background` by more than `tolerance`. */
export function inkInRow(png: DecodedPng, y: number, background: readonly number[], tolerance = 24): number {
  let ink = 0
  for (let x = 0; x < png.width; x++) if (distance(pixelAt(png, x, y), background) > tolerance) ink += 1
  return ink / png.width
}
