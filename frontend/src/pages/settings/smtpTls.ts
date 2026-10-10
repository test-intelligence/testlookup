/**
 * SMTP port <-> TLS mode pairing for the Email (SMTP) settings form.
 *
 * Owner report 2026-10-10: Gmail on port 587 with implicit TLS on failed every
 * send with an SSL "wrong version number"; the generic "verify the SMTP
 * configuration" read as "the password did not save".
 */

/** The TLS mode a well-known submission port implies, or null for any other port. */
export function tlsModeForPort(port: number): boolean | null {
  if (port === 465) return true
  if (port === 587 || port === 25) return false
  return null
}

/** Why this port and TLS mode will not connect, or null when they agree. */
export function tlsMismatch(port: number, implicitTls: boolean): string | null {
  const expected = tlsModeForPort(port)
  if (expected === null || expected === implicitTls) return null
  return implicitTls
    ? `Port ${port} normally uses STARTTLS, not implicit TLS. Sending will fail with an SSL error; switch the mode.`
    : `Port ${port} normally uses implicit TLS. Switch the mode, or use port 587 for STARTTLS.`
}
