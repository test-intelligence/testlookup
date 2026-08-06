/**
 * Pure formatting/normalization for the MFA inputs.
 *
 * Lives outside the components so they stay component-only modules (Vite fast
 * refresh) and so the paste behaviour is unit-testable on its own.
 */

/** Base32 secrets are read aloud and typed by hand — chunk them. */
export function formatSecret(secret: string): string {
  return (secret.match(/.{1,4}/g) ?? [secret]).join(' ')
}

/** Digits only, capped at 6. Lets "123 456" or "123-456" paste cleanly. */
export function normalizeTotpInput(raw: string): string {
  return raw.replace(/\D/g, '').slice(0, 6)
}

/**
 * Uppercase alphanumerics + hyphens. The backend strips whitespace and hyphens
 * before comparing, so grouping the user pasted is harmless — we keep it
 * visible because that is how the code was displayed to them.
 */
export function normalizeRecoveryInput(raw: string): string {
  return raw
    .toUpperCase()
    .replace(/[^A-Z0-9-]/g, '')
    .slice(0, 32)
}
