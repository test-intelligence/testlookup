/**
 * A streaming SHA-256 (FIPS 180-4) for a file too large to hold in memory.
 *
 * `crypto.subtle.digest` hashes one whole buffer and has no incremental form,
 * so the parted project import (W30, ADM-05 run history) — a file of up to 2 GB
 * sent in parts of at most 32 MiB — cannot use it for the whole-file digest
 * without first reading 2 GB into one `ArrayBuffer`. This module reads the
 * `Blob` one slice at a time (`Blob.slice` + `arrayBuffer()`), feeds each slice
 * to a small pure-TypeScript SHA-256 and drops it, so at most ONE slice is in
 * memory. One pass yields the whole-file digest AND each part's digest (the
 * `X-Part-Sha256` header of that part's PUT). No dependency.
 *
 * Digests are lower-case hex, the server's `^[0-9a-f]{64}$`.
 */

/** The import's part size (`IMPORT_PART_MAX_BYTES`, 32 MiB) — the default slice. */
export const SHA256_SLICE_BYTES = 32 * 1024 * 1024

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

const INITIAL_STATE = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
]

/** An incremental SHA-256: `update()` any number of times, then `hex()` once. */
export class Sha256 {
  private readonly state = new Uint32Array(INITIAL_STATE)
  private readonly schedule = new Uint32Array(64)
  private readonly pending = new Uint8Array(64)
  private pendingLength = 0
  private totalBytes = 0
  private finished = false

  /** Adds bytes to the message. Throws after `digest()` / `hex()`. */
  update(data: Uint8Array): this {
    if (this.finished) throw new Error('Sha256: update() after digest()')
    const length = data.length
    let offset = 0
    this.totalBytes += length
    if (this.pendingLength > 0) {
      const take = Math.min(64 - this.pendingLength, length)
      this.pending.set(data.subarray(0, take), this.pendingLength)
      this.pendingLength += take
      offset = take
      if (this.pendingLength < 64) return this
      this.compress(this.pending, 0)
      this.pendingLength = 0
    }
    while (offset + 64 <= length) {
      this.compress(data, offset)
      offset += 64
    }
    if (offset < length) {
      this.pending.set(data.subarray(offset), 0)
      this.pendingLength = length - offset
    }
    return this
  }

  /** The 32-byte digest; the hash is finished after the first call. */
  digest(): Uint8Array {
    if (!this.finished) this.finish()
    const out = new Uint8Array(32)
    for (let i = 0; i < 8; i++) {
      const word = this.state[i]
      out[i * 4] = word >>> 24
      out[i * 4 + 1] = (word >>> 16) & 0xff
      out[i * 4 + 2] = (word >>> 8) & 0xff
      out[i * 4 + 3] = word & 0xff
    }
    return out
  }

  /** The digest as 64 lower-case hex characters. */
  hex(): string {
    return toHex(this.digest())
  }

  private finish(): void {
    // The message length in BITS as a 64-bit big-endian number, split so a
    // file over 512 MiB (more than 2^32 bits) keeps its high word.
    const high = Math.floor(this.totalBytes / 0x20000000)
    const low = (this.totalBytes % 0x20000000) * 8
    const block = this.pending
    block[this.pendingLength++] = 0x80
    if (this.pendingLength > 56) {
      block.fill(0, this.pendingLength)
      this.compress(block, 0)
      this.pendingLength = 0
    }
    block.fill(0, this.pendingLength, 56)
    block[56] = high >>> 24
    block[57] = (high >>> 16) & 0xff
    block[58] = (high >>> 8) & 0xff
    block[59] = high & 0xff
    block[60] = low >>> 24
    block[61] = (low >>> 16) & 0xff
    block[62] = (low >>> 8) & 0xff
    block[63] = low & 0xff
    this.compress(block, 0)
    this.finished = true
  }

  private compress(bytes: Uint8Array, offset: number): void {
    const w = this.schedule
    for (let i = 0; i < 16; i++) {
      const j = offset + i * 4
      w[i] = (bytes[j] << 24) | (bytes[j + 1] << 16) | (bytes[j + 2] << 8) | bytes[j + 3]
    }
    for (let i = 16; i < 64; i++) {
      const w15 = w[i - 15]
      const w2 = w[i - 2]
      const s0 = ((w15 >>> 7) | (w15 << 25)) ^ ((w15 >>> 18) | (w15 << 14)) ^ (w15 >>> 3)
      const s1 = ((w2 >>> 17) | (w2 << 15)) ^ ((w2 >>> 19) | (w2 << 13)) ^ (w2 >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0
    }
    const h = this.state
    let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], k = h[7]
    for (let i = 0; i < 64; i++) {
      const s1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))
      const choose = (e & f) ^ (~e & g)
      const t1 = (k + s1 + choose + K[i] + w[i]) | 0
      const s0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (s0 + majority) | 0
      k = g
      g = f
      f = e
      e = (d + t1) | 0
      d = c
      c = b
      b = a
      a = (t1 + t2) | 0
    }
    h[0] += a
    h[1] += b
    h[2] += c
    h[3] += d
    h[4] += e
    h[5] += f
    h[6] += g
    h[7] += k
  }
}

/** Lower-case hex of a byte array. */
export function toHex(bytes: Uint8Array): string {
  let out = ''
  for (let i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, '0')
  return out
}

export interface Sha256BlobOptions {
  /** Bytes read per step (default 32 MiB): the most this module holds at once. */
  sliceBytes?: number
  /** Called after each slice with the bytes hashed so far and the total. */
  onProgress?: (doneBytes: number, totalBytes: number) => void
  /** Stops between slices (the rejection is the signal's reason). */
  signal?: AbortSignal
}

export interface Sha256Parts {
  /** The SHA-256 of the whole file. */
  sha256: string
  /** The SHA-256 of each part, in order (part 1 first). */
  parts: string[]
}

function sliceSize(value: number | undefined): number {
  const size = value ?? SHA256_SLICE_BYTES
  if (!Number.isInteger(size) || size < 1) throw new RangeError('sliceBytes must be a positive integer')
  return size
}

/**
 * The whole-file digest and every part's digest in ONE read of the file, the
 * parts being consecutive slices of `sliceBytes` (the last one shorter) — the
 * server's part rule. An empty file has no part and the empty digest.
 */
export async function sha256Parts(blob: Blob, options: Sha256BlobOptions = {}): Promise<Sha256Parts> {
  const size = sliceSize(options.sliceBytes)
  const whole = new Sha256()
  const parts: string[] = []
  for (let start = 0; start < blob.size; start += size) {
    options.signal?.throwIfAborted()
    const end = Math.min(start + size, blob.size)
    const bytes = new Uint8Array(await blob.slice(start, end).arrayBuffer())
    whole.update(bytes)
    parts.push(new Sha256().update(bytes).hex())
    options.onProgress?.(end, blob.size)
  }
  return { sha256: whole.hex(), parts }
}

/** The SHA-256 of a whole `Blob` / `File`, read one slice at a time. */
export async function sha256OfBlob(blob: Blob, options: Sha256BlobOptions = {}): Promise<string> {
  const size = sliceSize(options.sliceBytes)
  const whole = new Sha256()
  for (let start = 0; start < blob.size; start += size) {
    options.signal?.throwIfAborted()
    const end = Math.min(start + size, blob.size)
    whole.update(new Uint8Array(await blob.slice(start, end).arrayBuffer()))
    options.onProgress?.(end, blob.size)
  }
  return whole.hex()
}
