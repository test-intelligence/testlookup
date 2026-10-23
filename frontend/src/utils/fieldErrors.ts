/**
 * Field-level errors out of a FastAPI 422, for forms that render each sentence
 * under its own field (W10: Settings › Integrations, Edit Project, New Project).
 *
 * A Pydantic / route 422 answers `detail: [{ loc: ['body', <field>, ...], msg, type }]`.
 * The server writes its sentences for people (`Target host is not allowed`,
 * the Splunk index rule); a plain `ValueError` arrives prefixed "Value error, ",
 * which is dropped here.
 */

const VALUE_ERROR_PREFIX = /^Value error,\s*/

function detailOf(error: unknown): unknown {
  if (!error || typeof error !== 'object') return undefined
  const response = (error as { response?: unknown }).response
  if (!response || typeof response !== 'object') return undefined
  const { status, data } = response as { status?: unknown; data?: unknown }
  if (status !== 422 || !data || typeof data !== 'object') return undefined
  return (data as { detail?: unknown }).detail
}

/**
 * A FastAPI 422 body → `{ field: sentence }` for the fields in `known`; null
 * when the error is not a field-level 422 (another status, a string detail, or
 * no item naming a known field). The first message per field wins.
 */
export function fieldErrorsFrom(error: unknown, known: readonly string[]): Record<string, string> | null {
  const detail = detailOf(error)
  if (!Array.isArray(detail)) return null
  const out: Record<string, string> = {}
  for (const item of detail) {
    if (!item || typeof item !== 'object') continue
    const { loc, msg } = item as { loc?: unknown; msg?: unknown }
    if (!Array.isArray(loc) || loc[0] !== 'body' || typeof loc[1] !== 'string') continue
    const field = loc[1]
    if (!known.includes(field) || Object.prototype.hasOwnProperty.call(out, field)) continue
    if (typeof msg !== 'string') continue
    const sentence = msg.replace(VALUE_ERROR_PREFIX, '').trim()
    if (sentence) out[field] = sentence
  }
  return Object.keys(out).length ? out : null
}
