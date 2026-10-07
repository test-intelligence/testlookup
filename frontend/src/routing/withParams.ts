/** `search` (a `?a=b` string) with `extra` set on top; `''` when nothing is left. */
export function withParams(search: string, extra: Record<string, string>): string {
  const params = new URLSearchParams(search)
  for (const [key, value] of Object.entries(extra)) params.set(key, value)
  const query = params.toString()
  return query ? `?${query}` : ''
}
