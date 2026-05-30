// crypto.randomUUID is only exposed in secure contexts (HTTPS or localhost).
// Homelab/plain-HTTP deployments (e.g. http://testlookup.local) lack it, which
// breaks third-party libraries that call it unconditionally. crypto.getRandomValues
// is available even in insecure contexts, so we shim randomUUID on top of it.
if (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID !== 'function') {
  const getRandomBytes = (): Uint8Array => {
    const bytes = new Uint8Array(16)
    if (typeof globalThis.crypto.getRandomValues === 'function') {
      globalThis.crypto.getRandomValues(bytes)
    } else {
      for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
    }
    return bytes
  }

  const randomUUID = (): `${string}-${string}-${string}-${string}-${string}` => {
    const bytes = getRandomBytes()
    bytes[6] = (bytes[6] & 0x0f) | 0x40
    bytes[8] = (bytes[8] & 0x3f) | 0x80
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}` as `${string}-${string}-${string}-${string}-${string}`
  }

  Object.defineProperty(globalThis.crypto, 'randomUUID', {
    value: randomUUID,
    writable: true,
    configurable: true,
  })
}
