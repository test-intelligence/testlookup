/** Fixed response controls for the isolated release acceptance lane. */
function requireControl(condition: boolean, code: string): void {
  // A failed check exposes the control ID, never response bodies or headers.
  if (!condition) throw new Error('security_control_failed:' + code)
}

function directives(value: string): Map<string, string[]> {
  const result = new Map<string, string[]>()
  for (const clause of value.split(';')) {
    const [name, ...sources] = clause.trim().split(/\s+/)
    if (!name) continue
    requireControl(!result.has(name.toLowerCase()), 'duplicate_csp_directive')
    result.set(name.toLowerCase(), sources)
  }
  return result
}

export function verifySecurityResponse(headers: Record<string, string>, html: string): string[] {
  const normalized = Object.fromEntries(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]))
  requireControl(normalized['x-frame-options']?.toUpperCase() === 'DENY', 'frame_options')
  requireControl(normalized['x-content-type-options']?.toLowerCase() === 'nosniff', 'content_type_options')
  requireControl(normalized['referrer-policy']?.toLowerCase() === 'strict-origin-when-cross-origin', 'referrer_policy')
  const age = /(?:^|;)\s*max-age=(\d+)(?:;|$)/i.exec(normalized['strict-transport-security'] ?? '')
  requireControl(!!age && Number(age[1]) >= 31_536_000, 'hsts_header')
  const header = directives(normalized['content-security-policy'] ?? '')
  requireControl(JSON.stringify(header.get('frame-ancestors')) === JSON.stringify(["'none'"]), 'csp_frame_ancestors')
  requireControl((normalized['cache-control'] ?? '').toLowerCase().includes('no-store'), 'login_document_no_store')
  requireControl(html.length <= 262_144, 'login_document_bound')
  const document = html.replace(/<!--[\s\S]*?-->/g, '').replace(/<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
  const tags = document.match(/<meta\s+[^>]*>/gi) ?? []
  let meta: string | undefined
  for (const tag of tags) {
    const attrs = new Map<string, string>()
    for (const match of tag.matchAll(/([\w-]+)\s*=\s*(["'])(.*?)\2/g)) attrs.set(match[1].toLowerCase(), match[3])
    if (attrs.get('http-equiv')?.toLowerCase() === 'content-security-policy') {
      requireControl(meta === undefined, 'single_meta_csp')
      meta = attrs.get('content')
    }
  }
  const policy = directives(meta ?? '')
  for (const [key, expected] of Object.entries({
    'default-src': ["'self'"], 'script-src': ["'self'"], 'object-src': ["'none'"],
    'base-uri': ["'self'"], 'form-action': ["'self'"],
  })) requireControl(JSON.stringify(policy.get(key)) === JSON.stringify(expected), 'meta_csp_' + key)
  requireControl(!policy.has('frame-ancestors'), 'frame_ancestors_uses_header')
  return ['frame_options', 'content_type_options', 'referrer_policy', 'hsts_header', 'csp_frame_ancestors', 'login_document_no_store', 'meta_csp_baseline']
}
