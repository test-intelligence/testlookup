/**
 * W21d ACT-02: the comment composer. Suggestions appear only while the caret
 * sits in an `@token`, offer only eligible members other than the signed-in
 * user (from the page's own members read), and a click inserts `@username `;
 * posting toasts who the server notified. The old comment box's strings hold.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const hoisted = vi.hoisted(() => ({
  addComment: vi.fn(),
  useProjectMembers: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/services/testManagementService', () => ({
  testManagementService: { addComment: hoisted.addComment },
}))
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: hoisted.useProjectMembers }))
vi.mock('@/store/authStore', () => ({
  useAuthStore: (select: (s: { user: { id: string } }) => unknown) => select({ user: { id: 'u-self' } }),
}))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: hoisted.success, error: hoisted.error }),
}))

import CommentComposer from './CommentComposer'

const MEMBERS = [
  { user_id: 'u-alice', username: 'alice', full_name: 'Alice Ng', email: 'alice@corp.example', is_active: true },
  { user_id: 'u-alsmith', username: 'alice.smith', full_name: 'Alice Smith', email: 'asmith@corp.example' },
  { user_id: 'u-self', username: 'albert', full_name: 'Albert Me', email: 'albert@corp.example' },
  { user_id: 'u-old', username: 'alma', full_name: 'Alma Gone', is_active: false },
  { user_id: 'u-syn', username: 'alqa', full_name: 'Default QA lead', is_synthetic: true },
  { user_id: 'u-svc', username: 'alsvc', full_name: 'CI bot', is_service_account: true },
  { user_id: 'u-bob', username: 'bob', full_name: null },
]

function setup() {
  const onPosted = vi.fn()
  render(<CommentComposer caseId="case-1" projectId="p-1" onPosted={onPosted} />)
  const box = screen.getByPlaceholderText('Add a comment…') as HTMLTextAreaElement
  const type = (value: string) => fireEvent.change(box, { target: { value } })
  return { onPosted, box, type }
}

const suggestions = () => document.querySelector('[data-mention-suggestions]')
const optionNames = () => Array.from(document.querySelectorAll('[data-mention-option]')).map(b => b.textContent)

describe('CommentComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    hoisted.useProjectMembers.mockReturnValue({ data: MEMBERS })
  })

  it('reads the page’s members key for the project and keeps the old box’s strings', () => {
    setup()
    expect(hoisted.useProjectMembers).toHaveBeenCalledWith('p-1')
    expect(screen.getByRole('button', { name: 'Post Comment' })).toBeDisabled()
    expect(suggestions()).toBeNull()
  })

  it('offers eligible members only while the caret sits in an @token', () => {
    const { type } = setup()
    type('please check')
    expect(suggestions()).toBeNull()
    type('please check @al')
    expect(optionNames()).toEqual(['Alice Ng · @alice', 'Alice Smith · @alice.smith'])
    expect(document.body.textContent).not.toContain('corp.example')
    type('please check @al ')
    expect(suggestions()).toBeNull()
    type('mail me at email@al')
    expect(suggestions()).toBeNull()
    type('@')
    expect(optionNames()).toEqual(['Alice Ng · @alice', 'Alice Smith · @alice.smith', '@bob'])
  })

  it('a click inserts @username and a space, and the list closes', () => {
    const { box, type } = setup()
    type('hi @al')
    fireEvent.click(screen.getByRole('button', { name: 'Alice Ng · @alice' }))
    expect(box.value).toBe('hi @alice ')
    expect(suggestions()).toBeNull()
  })

  it('posts as before and toasts who the server notified', async () => {
    hoisted.addComment.mockResolvedValue({
      id: 'c-1', content: '@alice please re-check step 3',
      mentions: [{ user_id: 'u-alice', username: 'alice', display_name: 'Alice Ng' }],
    })
    const { box, type, onPosted } = setup()
    type('  @alice please re-check step 3 ')
    fireEvent.click(screen.getByRole('button', { name: 'Post Comment' }))
    await waitFor(() => expect(hoisted.success).toHaveBeenCalledWith('Comment added · notified @alice'))
    expect(hoisted.addComment).toHaveBeenCalledWith('case-1', { content: '@alice please re-check step 3', comment_type: 'general' })
    expect(onPosted).toHaveBeenCalledTimes(1)
    expect(box.value).toBe('')
  })

  it('without mentions the toast is the old one; a failure is the old error', async () => {
    hoisted.addComment.mockResolvedValueOnce({ id: 'c-2', content: 'Looks right', mentions: [] })
    const { type, onPosted } = setup()
    type('Looks right')
    fireEvent.click(screen.getByRole('button', { name: 'Post Comment' }))
    await waitFor(() => expect(hoisted.success).toHaveBeenCalledWith('Comment added'))

    hoisted.addComment.mockRejectedValueOnce(new Error('boom'))
    type('Again')
    fireEvent.click(screen.getByRole('button', { name: 'Post Comment' }))
    await waitFor(() => expect(hoisted.error).toHaveBeenCalledWith('Failed to add comment'))
    expect(onPosted).toHaveBeenCalledTimes(1)
  })

  it('no members data yet offers nothing and still posts', async () => {
    hoisted.useProjectMembers.mockReturnValue({ data: undefined })
    hoisted.addComment.mockResolvedValue({ id: 'c-3', content: '@al hi' })
    const { type } = setup()
    type('@al')
    expect(suggestions()).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Post Comment' }))
    await waitFor(() => expect(hoisted.success).toHaveBeenCalledWith('Comment added'))
  })
})
