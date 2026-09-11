import { describe, expect, it } from 'vitest'
import { AVAILABLE_SCOPES } from './keyScopeOptions'

// The server enforces these two scopes (backend/app/core/deps.py): a key that
// lists scopes but not project:admin is refused on project-administration
// routes. Without them in the picker, an admin could not mint such a key here.
describe('API key scope picker', () => {
  it('offers the scopes the server enforces', () => {
    expect(AVAILABLE_SCOPES).toContain('stream:write')
    expect(AVAILABLE_SCOPES).toContain('project:admin')
  })
})
