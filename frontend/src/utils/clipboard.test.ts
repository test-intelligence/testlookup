import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { copyTextToClipboard } from './clipboard'

// `frontend.clipboard-util` (quality gate) forces all 16 copy call sites through
// this helper precisely because navigator.clipboard is unavailable on HTTP
// origins — the homelab and air-gapped installs. The fallback path is the whole
// point of the helper and it shipped with no test at all.

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
const originalIsSecureContext = Object.getOwnPropertyDescriptor(window, 'isSecureContext')

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, 'clipboard', {
    value,
    configurable: true,
    writable: true,
  })
}

function setSecureContext(value: boolean) {
  Object.defineProperty(window, 'isSecureContext', {
    value,
    configurable: true,
    writable: true,
  })
}

function restore(target: object, prop: string, descriptor: PropertyDescriptor | undefined) {
  if (descriptor) {
    Object.defineProperty(target, prop, descriptor)
  } else {
    delete (target as Record<string, unknown>)[prop]
  }
}

describe('copyTextToClipboard', () => {
  let execCommand: ReturnType<typeof vi.fn>

  beforeEach(() => {
    execCommand = vi.fn(() => true)
    // jsdom does not implement execCommand at all.
    ;(document as unknown as Record<string, unknown>).execCommand = execCommand
  })

  afterEach(() => {
    restore(navigator, 'clipboard', originalClipboard)
    restore(window, 'isSecureContext', originalIsSecureContext)
    delete (document as unknown as Record<string, unknown>).execCommand
    vi.restoreAllMocks()
  })

  describe('the async clipboard path (https origins)', () => {
    it('writes through navigator.clipboard and reports success', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      setClipboard({ writeText })
      setSecureContext(true)

      await expect(copyTextToClipboard('run-42')).resolves.toBe(true)

      expect(writeText).toHaveBeenCalledWith('run-42')
      // The legacy path must not also run — a double copy can clobber the value.
      expect(execCommand).not.toHaveBeenCalled()
    })

    it('passes the value through verbatim, including empty strings', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      setClipboard({ writeText })
      setSecureContext(true)

      await expect(copyTextToClipboard('')).resolves.toBe(true)
      expect(writeText).toHaveBeenCalledWith('')
    })
  })

  describe('the legacy fallback (http origins, denied permission)', () => {
    it('falls back to execCommand on an insecure context', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      setClipboard({ writeText })
      setSecureContext(false)

      await expect(copyTextToClipboard('api-key')).resolves.toBe(true)

      // This is the homelab / air-gap case: the async API exists but must not
      // be used, because the browser refuses it off a secure origin.
      expect(writeText).not.toHaveBeenCalled()
      expect(execCommand).toHaveBeenCalledWith('copy')
    })

    it('falls back when navigator.clipboard is absent entirely', async () => {
      setClipboard(undefined)
      setSecureContext(true)

      await expect(copyTextToClipboard('api-key')).resolves.toBe(true)
      expect(execCommand).toHaveBeenCalledWith('copy')
    })

    it('falls back when the async write rejects (permission denied)', async () => {
      const writeText = vi.fn().mockRejectedValue(new Error('NotAllowedError'))
      setClipboard({ writeText })
      setSecureContext(true)

      await expect(copyTextToClipboard('api-key')).resolves.toBe(true)

      expect(writeText).toHaveBeenCalledWith('api-key')
      expect(execCommand).toHaveBeenCalledWith('copy')
    })

    it('stages the value in a textarea and removes it again', async () => {
      setClipboard(undefined)
      setSecureContext(false)
      let staged: string | undefined
      let attachedDuringCopy = false
      execCommand.mockImplementation(() => {
        const el = document.querySelector('textarea')
        staged = el?.value
        attachedDuringCopy = Boolean(el)
        return true
      })

      await expect(copyTextToClipboard('secret-token')).resolves.toBe(true)

      expect(attachedDuringCopy).toBe(true)
      expect(staged).toBe('secret-token')
      // A leaked off-screen textarea holding a copied API key would linger in
      // the DOM for the rest of the session.
      expect(document.querySelector('textarea')).toBeNull()
    })

    it('reports failure when execCommand declines', async () => {
      setClipboard(undefined)
      setSecureContext(false)
      execCommand.mockReturnValue(false)

      await expect(copyTextToClipboard('x')).resolves.toBe(false)
      expect(document.querySelector('textarea')).toBeNull()
    })

    it('reports failure instead of throwing when the fallback blows up', async () => {
      setClipboard(undefined)
      setSecureContext(false)
      execCommand.mockImplementation(() => {
        throw new Error('execCommand is not supported')
      })

      // Callers render a toast off the boolean; an exception here would escape
      // into an onClick handler and surface as an unhandled rejection.
      await expect(copyTextToClipboard('x')).resolves.toBe(false)
    })
  })
})
