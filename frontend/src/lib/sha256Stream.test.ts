import { describe, expect, it, vi } from 'vitest'
import { SHA256_SLICE_BYTES, Sha256, sha256OfBlob, sha256Parts, toHex } from './sha256Stream'

const MiB = 1024 * 1024
const encode = (text: string) => new TextEncoder().encode(text)

/** Deterministic pseudo-random bytes (xorshift32) so a large input has no pattern. */
function noise(length: number, seed = 0x2545f491): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(length)
  let x = seed >>> 0
  for (let i = 0; i < length; i++) {
    x ^= x << 13
    x ^= x >>> 17
    x ^= x << 5
    out[i] = x & 0xff
  }
  return out
}

async function subtleHex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  return toHex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))
}

describe('Sha256 (FIPS 180-4 vectors pinned)', () => {
  it.each([
    ['the empty message', '', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'],
    ['"abc"', 'abc', 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'],
    [
      'the 448-bit message',
      'abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq',
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
    ],
  ])('hashes %s', (_label, text, digest) => {
    expect(new Sha256().update(encode(text)).hex()).toBe(digest)
  })

  it('hashes one million "a" fed in uneven pieces', () => {
    const hash = new Sha256()
    const piece = encode('a'.repeat(997))
    let fed = 0
    while (fed + piece.length <= 1_000_000) {
      hash.update(piece)
      fed += piece.length
    }
    hash.update(encode('a'.repeat(1_000_000 - fed)))
    expect(hash.hex()).toBe('cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0')
  })

  it('gives the same digest whatever the update boundaries (around the 56 / 64-byte padding edges)', async () => {
    for (const length of [55, 56, 57, 63, 64, 65, 119, 120, 128, 1000]) {
      const bytes = noise(length, length + 1)
      const expected = await subtleHex(bytes)
      expect(new Sha256().update(bytes).hex()).toBe(expected)
      for (const size of [1, 2, 63, 64, 65]) {
        const split = new Sha256()
        for (let offset = 0; offset < bytes.length; offset += size) {
          split.update(bytes.subarray(offset, offset + size))
        }
        expect(split.hex(), `length ${length}, pieces of ${size}`).toBe(expected)
      }
    }
  })

  it('is finished by its first digest', () => {
    const hash = new Sha256().update(encode('abc'))
    expect(hash.hex()).toBe(hash.hex())
    expect(() => hash.update(encode('d'))).toThrow(/after digest/)
  })
})

/** A Blob whose reads are recorded: the whole file must never be read at once. */
function recordedBlob(bytes: Uint8Array<ArrayBuffer>) {
  const blob = new Blob([bytes])
  const reads: number[] = []
  const wholeRead = vi.spyOn(blob, 'arrayBuffer')
  const slice = blob.slice.bind(blob)
  vi.spyOn(blob, 'slice').mockImplementation((start?: number, end?: number) => {
    const part = slice(start, end)
    reads.push(part.size)
    return part
  })
  return { blob, reads, wholeRead }
}

describe('sha256OfBlob / sha256Parts (equal to crypto.subtle.digest)', () => {
  it('hashes an empty file: the empty digest and no part', async () => {
    const empty = new Blob([])
    const expected = await subtleHex(new Uint8Array(0))
    expect(await sha256OfBlob(empty)).toBe(expected)
    expect(await sha256Parts(empty)).toEqual({ sha256: expected, parts: [] })
  })

  it('hashes a one-byte file as one part', async () => {
    const bytes = new Uint8Array([0x7a])
    const expected = await subtleHex(bytes)
    expect(await sha256OfBlob(new Blob([bytes]))).toBe(expected)
    expect(await sha256Parts(new Blob([bytes]))).toEqual({ sha256: expected, parts: [expected] })
  })

  it('hashes a 33 MiB file in 32 MiB slices — never the whole file in memory — with every part digest', async () => {
    const bytes = noise(33 * MiB)
    const { blob, reads, wholeRead } = recordedBlob(bytes)
    const progress: Array<[number, number]> = []

    const result = await sha256Parts(blob, { onProgress: (done, total) => progress.push([done, total]) })

    expect(SHA256_SLICE_BYTES).toBe(32 * MiB)
    expect(result.sha256).toBe(await subtleHex(bytes))
    expect(result.parts).toEqual([
      await subtleHex(bytes.subarray(0, 32 * MiB)),
      await subtleHex(bytes.subarray(32 * MiB)),
    ])
    expect(reads).toEqual([32 * MiB, MiB])
    expect(wholeRead).not.toHaveBeenCalled()
    expect(progress).toEqual([[32 * MiB, 33 * MiB], [33 * MiB, 33 * MiB]])
  })

  it('agrees for a slice size that is not a multiple of the 64-byte block', async () => {
    const bytes = noise(3 * 1_000_003 + 17, 7)
    const { blob, reads, wholeRead } = recordedBlob(bytes)
    expect(await sha256OfBlob(blob, { sliceBytes: 1_000_003 })).toBe(await subtleHex(bytes))
    expect(reads).toEqual([1_000_003, 1_000_003, 1_000_003, 17])
    expect(wholeRead).not.toHaveBeenCalled()
  })

  it('stops between slices when aborted and refuses a bad slice size', async () => {
    const controller = new AbortController()
    controller.abort(new Error('the person chose another file'))
    await expect(sha256OfBlob(new Blob([noise(10)]), { signal: controller.signal })).rejects.toThrow(
      'the person chose another file',
    )
    await expect(sha256Parts(new Blob([noise(10)]), { sliceBytes: 0 })).rejects.toThrow(RangeError)
  })
})
