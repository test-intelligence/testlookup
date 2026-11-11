import { expect, test, type APIResponse } from '@playwright/test'

export async function checkStatus(method: string, response: APIResponse, expected: number) {
  const evidence = {
    method,
    path: new URL(response.url()).pathname,
    status: response.status(),
    expected,
    request_id: response.headers()['x-request-id'] || null,
    at: new Date().toISOString(),
  }
  await test.info().attach('request-evidence', { body: JSON.stringify(evidence), contentType: 'application/json' })
  expect(response.status(), method + ' ' + evidence.path + ' request ' + evidence.request_id).toBe(expected)
}
