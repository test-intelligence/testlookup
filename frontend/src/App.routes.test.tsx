import { describe, expect, it } from 'vitest'

import { managementRoutes } from './App'

describe('management routes', () => {
  it('registers the release detail deep link used by release cards', () => {
    expect(managementRoutes.map(({ path }) => path)).toContain(
      'releases/:releaseId',
    )
  })
})
