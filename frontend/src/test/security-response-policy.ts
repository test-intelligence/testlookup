/** Fixed response controls for the isolated release acceptance lane. */
import { parse, type DefaultTreeAdapterTypes } from 'parse5'

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

function elements(parent: DefaultTreeAdapterTypes.ParentNode, name: string): DefaultTreeAdapterTypes.Element[] {
  return parent.childNodes.filter((node): node is DefaultTreeAdapterTypes.Element =>
    'tagName' in node && node.tagName === name && node.namespaceURI === 'http://www.w3.org/1999/xhtml',
  )
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
  // The release lane runs in Node. Parse inertly with HTML's actual comment,
  // raw-text, template and attribute rules; no script executes or resource loads.
  const root = elements(parse(html, { scriptingEnabled: true }), 'html')[0]
  const head = root ? elements(root, 'head')[0] : undefined
  const tags = head ? elements(head, 'meta') : []
  let meta: string | undefined
  for (const tag of tags) {
    const attrs = new Map(tag.attrs.map(attribute => [attribute.name, attribute.value]))
    if (attrs.get('http-equiv')?.toLowerCase() === 'content-security-policy') {
      requireControl(meta === undefined, 'single_meta_csp')
      meta = attrs.get('content') ?? ''
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
