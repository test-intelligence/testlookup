import { describe, expect, it } from 'vitest'
import frontendIndex from '../../index.html?raw'
import { verifySecurityResponse } from './security-response-policy'

const headers = {
  'x-frame-options': 'DENY', 'x-content-type-options': 'nosniff',
  'referrer-policy': 'strict-origin-when-cross-origin', 'strict-transport-security': 'max-age=31536000',
  'content-security-policy': "frame-ancestors 'none'", 'cache-control': 'no-cache, no-store, must-revalidate',
}
const html = '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; object-src \'none\'; base-uri \'self\'; form-action \'self\'">'

describe('isolated release response controls', () => {
  it('accepts the healthy reference and real frontend meta policy', () => {
    expect(verifySecurityResponse(headers, html)).toContain('meta_csp_baseline')
    expect(verifySecurityResponse(headers, frontendIndex)).toHaveLength(7)
  })

  it.each(Object.keys(headers))('detects a synthetic omitted %s header without exposing content', key => {
    const omitted: Record<string, string> = { ...headers }
    delete omitted[key]
    expect(() => verifySecurityResponse(omitted, html)).toThrow(/^security_control_failed:/)
  })

  it('requires effective response anti-framing even when the meta policy exists', () => {
    expect(() => verifySecurityResponse({ ...headers, 'content-security-policy': '' }, html)).toThrow('security_control_failed:csp_frame_ancestors')
  })

  it('does not treat a commented example as a served meta policy', () => {
    expect(() => verifySecurityResponse(headers, '<!-- ' + html + ' -->')).toThrow('security_control_failed:meta_csp_default-src')
  })

  it.each(['script', 'style', 'template', 'noscript', 'textarea'])('ignores policy examples inside %s content', tag => {
    expect(() => verifySecurityResponse(headers, `<${tag}>${html}</${tag}>`)).toThrow('security_control_failed:meta_csp_default-src')
  })

  it('requires the policy in the document head', () => {
    expect(() => verifySecurityResponse(headers, `<html><head></head><body>${html}</body></html>`)).toThrow('security_control_failed:meta_csp_default-src')
  })

  it('reads parsed attributes, entities and quoted greater-than characters', () => {
    const policy = html.replace('http-equiv="Content-Security-Policy"', 'data-note="a > b" HTTP-EQUIV=Content-Security-Policy')
      .replace(/'/g, '&#39;')
    expect(verifySecurityResponse(headers, policy)).toContain('meta_csp_baseline')
  })

  it('rejects duplicate meta policies including a first policy without content', () => {
    expect(() => verifySecurityResponse(headers, html + html)).toThrow('security_control_failed:single_meta_csp')
    expect(() => verifySecurityResponse(headers, '<meta http-equiv=Content-Security-Policy>' + html)).toThrow('security_control_failed:single_meta_csp')
  })

  it('rejects a missing script policy and duplicate directives with safe control IDs', () => {
    expect(() => verifySecurityResponse(headers, html.replace("script-src 'self';", ''))).toThrow('security_control_failed:meta_csp_script-src')
    expect(() => verifySecurityResponse(headers, html.replace("script-src 'self';", "script-src 'self'; script-src 'self';"))).toThrow('security_control_failed:duplicate_csp_directive')
  })
})
