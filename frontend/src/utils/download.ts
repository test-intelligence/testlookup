/**
 * One way to hand the user a file (VIZ-109).
 *
 * Every NEW export goes through `downloadBlob`. The ad-hoc copies already in
 * the tree (anchor + `createObjectURL`, each slightly different, several
 * never revoking) are deliberately left for their own migration: this module
 * is the target, not a sweep.
 *
 * What the copies got wrong, and this does not:
 *   - the object URL is ALWAYS revoked — also when `click()` throws — so an
 *     export repeated on a long-lived dashboard does not pin every blob it
 *     ever made in memory;
 *   - revocation waits `REVOKE_DELAY_MS` (40 s). Revoking synchronously after
 *     `click()` cancels the download in some browsers, and even one task is
 *     not enough for Safari and older Firefox, which read the blob later;
 *     40 s is FileSaver.js's long-standing figure. A blob held 40 s longer
 *     is cheap; a download that silently never happens is not;
 *   - the filename is sanitised. It is usually built from data (a release
 *     name, a suite name) and a `/`, a control character or a bare `CON`
 *     silently turns into a different name or a failed save.
 */

/** How long the object URL outlives the click (FileSaver.js precedent). */
export const REVOKE_DELAY_MS = 40_000

/** Longest filename we hand the browser, in code points, extension included. */
export const MAX_FILENAME_LENGTH = 150

/** Used when sanitising leaves nothing. */
export const FALLBACK_FILENAME = 'download'

/** Longest suffix still treated as an extension to preserve (`.xlsx`, `.json`, `.tar.gz` → `.gz`). */
const MAX_EXTENSION_LENGTH = 10

/** Windows device names, reserved with ANY extension (`con.txt` too). */
const WINDOWS_RESERVED = /^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])$/i

/** Path separators and the characters Windows forbids in a name. */
const FORBIDDEN = new Set(['/', '\\', ':', '*', '?', '"', '<', '>', '|'])

/**
 * C0/C1 control characters, plus the bidirectional overrides and isolates
 * that can make `report‮fdp.exe` display as `reportexe.pdf`.
 */
function isInvisibleOrControl(codePoint: number): boolean {
  return (
    codePoint <= 0x1f ||
    (codePoint >= 0x7f && codePoint <= 0x9f) ||
    (codePoint >= 0x202a && codePoint <= 0x202e) ||
    (codePoint >= 0x2066 && codePoint <= 0x2069)
  )
}

/**
 * A filename safe to offer on every desktop OS. Control characters are
 * dropped, path separators and reserved punctuation become `_`, leading dots
 * (hidden files) and trailing dots/spaces (stripped by Windows) go, a
 * reserved device name gets a `_` prefix, and an over-long name is cut from
 * the END OF THE STEM so the extension — what decides which app opens it —
 * survives.
 */
export function sanitizeFilename(name: string, fallback: string = FALLBACK_FILENAME): string {
  const cleaned = Array.from(name)
    .filter((ch) => !isInvisibleOrControl(ch.codePointAt(0) ?? 0))
    .map((ch) => (FORBIDDEN.has(ch) ? '_' : ch))
    .join('')
    .trim()
    .replace(/^\.+/, '')
    .replace(/[. ]+$/, '')

  if (cleaned === '') return fallback === FALLBACK_FILENAME ? FALLBACK_FILENAME : sanitizeFilename(fallback)

  const dot = cleaned.lastIndexOf('.')
  const hasExtension = dot > 0 && cleaned.length - dot <= MAX_EXTENSION_LENGTH + 1
  let stem = hasExtension ? cleaned.slice(0, dot) : cleaned
  const extension = hasExtension ? cleaned.slice(dot) : ''

  // `con.tar.gz` is as reserved as `con`: Windows looks at the part before the FIRST dot.
  const firstSegment = stem.split('.')[0]
  if (WINDOWS_RESERVED.test(firstSegment)) stem = `_${stem}`

  const stemChars = Array.from(stem)
  const room = Math.max(1, MAX_FILENAME_LENGTH - Array.from(extension).length)
  if (stemChars.length > room) stem = stemChars.slice(0, room).join('').replace(/[. ]+$/, '') || '_'

  return `${stem}${extension}`
}

/** Save `blob` as `filename` (sanitised). Always revokes the object URL. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = sanitizeFilename(filename)
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  try {
    document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS)
  }
}
