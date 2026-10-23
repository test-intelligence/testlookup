/**
 * W10 (contract §1.6): a FastAPI 422 → `{ field: sentence }` for the fields a
 * form renders; null for anything that is not a field-level 422.
 */
import { describe, expect, it } from 'vitest'

import { fieldErrorsFrom } from './fieldErrors'

const KNOWN = ['splunk_base_url', 'ocp_api_url', 'jira_domain'] as const

function axiosError(status: number, detail: unknown) {
  return Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })
}

describe('fieldErrorsFrom', () => {
  it('maps each known field to its message', () => {
    const err = axiosError(422, [
      { loc: ['body', 'splunk_base_url'], msg: 'Target host is not allowed', type: 'target_not_allowed' },
      { loc: ['body', 'jira_domain'], msg: 'Jira domain must be a host name such as your-company.atlassian.net, without https:// or a path.', type: 'jira_domain' },
    ])
    expect(fieldErrorsFrom(err, KNOWN)).toEqual({
      splunk_base_url: 'Target host is not allowed',
      jira_domain: 'Jira domain must be a host name such as your-company.atlassian.net, without https:// or a path.',
    })
  })

  it('drops a leading "Value error, " and keeps the first message per field', () => {
    const err = axiosError(422, [
      { loc: ['body', 'ocp_api_url'], msg: 'Value error, Enter a URL that starts with http:// or https://', type: 'value_error' },
      { loc: ['body', 'ocp_api_url'], msg: 'second', type: 'x' },
    ])
    expect(fieldErrorsFrom(err, KNOWN)).toEqual({ ocp_api_url: 'Enter a URL that starts with http:// or https://' })
  })

  it('reads a nested loc by its top-level body field', () => {
    const err = axiosError(422, [{ loc: ['body', 'jira_domain', 0], msg: 'bad', type: 'x' }])
    expect(fieldErrorsFrom(err, KNOWN)).toEqual({ jira_domain: 'bad' })
  })

  it('null when no item names a known field (an unknown field, a query param, the whole body)', () => {
    expect(fieldErrorsFrom(axiosError(422, [{ loc: ['body', 'github_token'], msg: 'x', type: 'x' }]), KNOWN)).toBeNull()
    expect(fieldErrorsFrom(axiosError(422, [{ loc: ['query', 'splunk_base_url'], msg: 'x', type: 'x' }]), KNOWN)).toBeNull()
    expect(fieldErrorsFrom(axiosError(422, [{ loc: ['body'], msg: 'Field required', type: 'missing' }]), KNOWN)).toBeNull()
    expect(fieldErrorsFrom(axiosError(422, [{ loc: ['body', 'splunk_base_url'], msg: '   ', type: 'x' }]), KNOWN)).toBeNull()
  })

  it('null for another status, a string detail, and things that are not HTTP errors', () => {
    expect(fieldErrorsFrom(axiosError(400, [{ loc: ['body', 'splunk_base_url'], msg: 'x', type: 'x' }]), KNOWN)).toBeNull()
    expect(fieldErrorsFrom(axiosError(422, 'Target host is not allowed'), KNOWN)).toBeNull()
    expect(fieldErrorsFrom(new Error('Network Error'), KNOWN)).toBeNull()
    expect(fieldErrorsFrom(null, KNOWN)).toBeNull()
    expect(fieldErrorsFrom(undefined, KNOWN)).toBeNull()
    expect(fieldErrorsFrom('boom', KNOWN)).toBeNull()
  })

  it('skips malformed items without failing', () => {
    const err = axiosError(422, [null, 'x', { loc: 'body.splunk_base_url', msg: 'x' }, { loc: ['body', 'splunk_base_url'], msg: 42 },
      { loc: ['body', 'splunk_base_url'], msg: 'Target host is not allowed' }])
    expect(fieldErrorsFrom(err, KNOWN)).toEqual({ splunk_base_url: 'Target host is not allowed' })
  })

  it('a field named like an Object.prototype key is not special', () => {
    const err = axiosError(422, [{ loc: ['body', 'constructor'], msg: 'x', type: 'x' }])
    expect(fieldErrorsFrom(err, ['constructor'])).toEqual({ constructor: 'x' })
  })
})
