import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const sync = vi.fn()
vi.mock('@/services/releasesService', () => ({ releasesService: { sync: (...a: unknown[]) => sync(...a) } }))

import { GitLabSyncSection } from './GitLabSyncSection'

function renderIt(canEdit = true) {
  return render(<MemoryRouter><GitLabSyncSection projectId="p1" canEdit={canEdit} /></MemoryRouter>)
}

describe('GitLabSyncSection (W19 INT-05)', () => {
  beforeEach(() => {
    sync.mockReset()
  })

  it('syncs GitLab milestones for the project and shows the counts', async () => {
    sync.mockResolvedValue({ source: 'gitlab', created: 2, updated: 1, skipped: 0 })
    renderIt()
    fireEvent.click(screen.getByRole('button', { name: /sync milestones now/i }))
    await waitFor(() => expect(screen.getByTestId('gitlab-sync-result')).toHaveTextContent('2 created, 1 updated.'))
    expect(sync).toHaveBeenCalledWith({ project_id: 'p1', source: 'gitlab' })
  })

  it("shows the server's refusal sentence", async () => {
    sync.mockRejectedValue(Object.assign(new Error('503'), {
      response: { data: { detail: 'GitLab sync is unavailable: the gitlab feature flag is off' } },
    }))
    renderIt()
    fireEvent.click(screen.getByRole('button', { name: /sync milestones now/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('gitlab feature flag is off')
  })

  it('offers no button to a read-only user', () => {
    renderIt(false)
    expect(screen.queryByRole('button', { name: /sync milestones now/i })).toBeNull()
  })
})
